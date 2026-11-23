"""Server-side PumpPortal Local transaction signer.

This module only exposes an execution primitive. It is not started automatically.
Callers must enforce the user's authenticated live-mode opt-in, daily loss limits,
position limits, idempotency, and an explicit global SNIPER_LIVE_TRADING_ENABLED flag.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import math
import os
import re
from typing import Any

from aiohttp import ClientSession, ClientTimeout
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

log = logging.getLogger("zombcash.sniper.execution")
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
PUMPPORTAL_LOCAL_URL = "https://pumpportal.fun/api/trade-local"


class LiveExecutionDisabled(RuntimeError):
    pass


class LiveExecutionError(RuntimeError):
    pass


def live_execution_enabled() -> bool:
    return os.getenv("SNIPER_LIVE_TRADING_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def live_execution_ready() -> bool:
    # The signer is implemented, but the per-user live order lifecycle is not
    # wired yet. Keep the UI/API locked until pending-order, balance, sell and
    # recovery flows are complete and integration-tested.
    return False


def _positive_amount(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("invalid_" + name)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("invalid_" + name)
    return number


def build_pumpportal_trade_request(
    *, public_key: str, mint: str, side: str, amount: float,
    max_slippage_bps: int, priority_fee_sol: float,
) -> dict:
    if not MINT_RE.fullmatch(str(mint or "")):
        raise ValueError("invalid_token_mint")
    if side not in {"buy", "sell"}:
        raise ValueError("invalid_trade_side")
    if not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", str(public_key or "")):
        raise ValueError("invalid_wallet_public_key")
    amount = _positive_amount(amount, "trade_amount")
    if not isinstance(max_slippage_bps, int) or not 1 <= max_slippage_bps <= 5000:
        raise ValueError("invalid_slippage")
    priority_fee_sol = float(priority_fee_sol)
    if not math.isfinite(priority_fee_sol) or not 0 <= priority_fee_sol <= 0.01:
        raise ValueError("invalid_priority_fee")
    return {
        "publicKey": public_key,
        "action": side,
        "mint": mint,
        "amount": amount,
        "denominatedInSol": "true" if side == "buy" else "false",
        "slippage": max_slippage_bps / 100,
        "priorityFee": priority_fee_sol,
        "pool": "pump",
    }


async def execute_pumpportal_local_trade(
    *,
    public_key: str,
    secret_key_base58: str,
    mint: str,
    side: str,
    amount: float,
    max_slippage_bps: int,
    priority_fee_sol: float | None = None,
) -> dict:
    """Build, locally sign and broadcast one PumpPortal trade, then confirm it."""
    if not live_execution_enabled():
        raise LiveExecutionDisabled("live_execution_globally_disabled")

    # Helius is an execution RPC only: it is never opened by launch discovery or paper mode.
    rpc_url = os.getenv("HELIUS_RPC_URL", "").strip()
    if not rpc_url.startswith("https://"):
        raise LiveExecutionDisabled("helius_execution_rpc_not_configured")

    try:
        keypair = Keypair.from_base58_string(secret_key_base58)
    except Exception as exc:
        raise LiveExecutionError("wallet_secret_invalid") from exc
    if str(keypair.pubkey()) != public_key:
        raise LiveExecutionError("wallet_key_mismatch")

    fee = priority_fee_sol
    if fee is None:
        fee = float(os.getenv("SNIPER_PRIORITY_FEE_SOL", "0.00005"))
    trade_request = build_pumpportal_trade_request(
        public_key=public_key,
        mint=mint,
        side=side,
        amount=amount,
        max_slippage_bps=max_slippage_bps,
        priority_fee_sol=fee,
    )

    timeout = ClientTimeout(total=12, connect=4)
    async with ClientSession(timeout=timeout) as session:
        async with session.post(
            PUMPPORTAL_LOCAL_URL,
            json=trade_request,
            headers={"Content-Type": "application/json"},
        ) as response:
            raw = await response.read()
            if response.status != 200:
                # Do not echo provider response: it may contain request details.
                log.warning("PumpPortal Local transaction build failed: HTTP %s", response.status)
                raise LiveExecutionError("pumpportal_transaction_build_failed")
            if not raw or len(raw) > 1_000_000:
                raise LiveExecutionError("pumpportal_transaction_payload_invalid")

        try:
            unsigned = VersionedTransaction.from_bytes(raw)
            signed = VersionedTransaction(unsigned.message, [keypair])
            signed_bytes = bytes(signed)
        except Exception as exc:
            raise LiveExecutionError("transaction_signing_failed") from exc

        encoded = base64.b64encode(signed_bytes).decode("ascii")
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                encoded,
                {
                    "encoding": "base64",
                    "preflightCommitment": "confirmed",
                    "maxRetries": 3,
                },
            ],
        }
        async with session.post(rpc_url, json=rpc_payload) as response:
            try:
                rpc_result = await response.json(content_type=None)
            except Exception as exc:
                raise LiveExecutionError("solana_rpc_invalid_response") from exc
            if response.status != 200 or rpc_result.get("error") or not rpc_result.get("result"):
                log.warning("Solana RPC rejected signed trade: HTTP %s", response.status)
                raise LiveExecutionError("solana_transaction_rejected")
            signature = str(rpc_result["result"])

        # Confirmation is polled via the same RPC; a submitted signature is not
        # reported as a completed trade until the chain reports confirmed/finalized.
        for attempt in range(15):
            status_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignatureStatuses",
                "params": [[signature], {"searchTransactionHistory": True}],
            }
            async with session.post(rpc_url, json=status_payload) as response:
                try:
                    status_result = await response.json(content_type=None)
                except Exception as exc:
                    raise LiveExecutionError("solana_confirmation_lookup_failed") from exc
            if response.status == 200 and not status_result.get("error"):
                values = (status_result.get("result") or {}).get("value") or []
                status = values[0] if values else None
                if status:
                    if status.get("err") is not None:
                        raise LiveExecutionError("solana_transaction_failed")
                    if status.get("confirmationStatus") in {"confirmed", "finalized"}:
                        return {
                            "signature": signature,
                            "status": status.get("confirmationStatus"),
                            "slot": status.get("slot"),
                            "side": side,
                            "mint": mint,
                        }
            if attempt < 14:
                await asyncio.sleep(0.5)
    raise LiveExecutionError("solana_transaction_confirmation_timeout")
