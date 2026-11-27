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


def classify_signature_status(status: Any) -> str | None:
    """Return confirmed/failed only for a definitive chain status."""
    if not isinstance(status, dict):
        return None
    if status.get("err") is not None:
        return "failed"
    if status.get("confirmationStatus") in {"confirmed", "finalized"}:
        return "confirmed"
    return None


async def _solana_rpc_call(method: str, params: list) -> Any:
    rpc_url = os.getenv("HELIUS_RPC_URL", "").strip()
    if not rpc_url.startswith("https://"):
        raise LiveExecutionDisabled("helius_execution_rpc_not_configured")
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    timeout = ClientTimeout(total=8, connect=3)
    try:
        async with ClientSession(timeout=timeout) as session:
            async with session.post(rpc_url, json=payload) as response:
                data = await response.json(content_type=None)
        if response.status != 200 or not isinstance(data, dict) or data.get("error"):
            raise LiveExecutionError("solana_rpc_request_failed")
        return data.get("result")
    except (LiveExecutionDisabled, LiveExecutionError):
        raise
    except Exception as exc:
        log.warning("Solana RPC request inconclusive method=%s error=%s", method, type(exc).__name__)
        raise LiveExecutionError("solana_rpc_request_inconclusive") from exc


async def check_execution_rpc() -> bool:
    """Perform a minimal RPC preflight only when an operator is enabling Live."""
    try:
        slot = await _solana_rpc_call("getSlot", [{"commitment": "confirmed"}])
        return isinstance(slot, int) and not isinstance(slot, bool) and slot > 0
    except Exception as exc:
        log.warning("Live execution RPC preflight failed (%s)", type(exc).__name__)
        return False


async def get_wallet_sol_balance_lamports(public_key: str) -> int:
    if not MINT_RE.fullmatch(str(public_key or "")):
        raise ValueError("invalid_wallet_public_key")
    result = await _solana_rpc_call("getBalance", [public_key, {"commitment": "confirmed"}])
    value = result.get("value") if isinstance(result, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LiveExecutionError("solana_balance_response_invalid")
    return value


async def fetch_confirmed_transaction(signature: str, attempts: int = 3) -> dict | None:
    if not isinstance(signature, str) or not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{80,100}", signature):
        return None
    attempts = max(1, min(int(attempts), 6))
    for attempt in range(attempts):
        try:
            result = await _solana_rpc_call(
                "getTransaction",
                [signature, {
                    "encoding": "json",
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0,
                }],
            )
            if isinstance(result, dict) and isinstance(result.get("meta"), dict):
                return result
        except Exception as exc:
            log.warning("Confirmed transaction lookup inconclusive (%s)", type(exc).__name__)
        if attempt < attempts - 1:
            await asyncio.sleep(0.5)
    return None


def live_execution_ready() -> bool:
    # Code readiness is separate from the global deployment kill switch.
    # Actual arming also requires database health and SNIPER_LIVE_TRADING_ENABLED.
    rpc_url = os.getenv("HELIUS_RPC_URL", "").strip()
    trade_url = os.getenv("PUMPPORTAL_TRADE_LOCAL_URL", "https://pumpportal.fun/api/trade-local").strip()
    return rpc_url.startswith("https://") and trade_url.startswith("https://")


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
        try:
            async with session.post(rpc_url, json=rpc_payload) as response:
                try:
                    rpc_result = await response.json(content_type=None)
                except Exception:
                    rpc_result = None
                if response.status != 200 or (isinstance(rpc_result, dict) and rpc_result.get("error")):
                    log.warning("Solana RPC rejected signed trade: HTTP %s", response.status)
                    raise LiveExecutionError("solana_transaction_rejected")
                if not isinstance(rpc_result, dict) or not rpc_result.get("result"):
                    # The request may have reached the RPC even if its response was lost.
                    # Never retry automatically: the caller must reconcile this unknown order.
                    return {"signature": None, "status": "unknown", "errorCode": "broadcast_response_unknown", "side": side, "mint": mint}
                signature = str(rpc_result["result"])
        except LiveExecutionError:
            raise
        except Exception as exc:
            log.warning("Solana transaction broadcast outcome unknown (%s)", type(exc).__name__)
            return {"signature": None, "status": "unknown", "errorCode": "broadcast_response_unknown", "side": side, "mint": mint}

        # A signature means broadcast was accepted; until the chain confirms it,
        # the result remains submitted/unknown and must never be retried blindly.
        for attempt in range(15):
            status_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignatureStatuses",
                "params": [[signature], {"searchTransactionHistory": True}],
            }
            try:
                async with session.post(rpc_url, json=status_payload) as response:
                    status_result = await response.json(content_type=None)
                if response.status == 200 and isinstance(status_result, dict) and not status_result.get("error"):
                    values = (status_result.get("result") or {}).get("value") or []
                    status = values[0] if values else None
                    classification = classify_signature_status(status)
                    if classification == "failed":
                        return {"signature": signature, "status": "failed", "errorCode": "solana_transaction_failed", "slot": (status or {}).get("slot"), "side": side, "mint": mint}
                    if classification == "confirmed":
                        return {
                            "signature": signature,
                            "status": "confirmed",
                            "slot": status.get("slot"),
                            "side": side,
                            "mint": mint,
                        }
            except Exception as exc:
                log.warning("Solana confirmation lookup inconclusive (%s)", type(exc).__name__)
            if attempt < 14:
                await asyncio.sleep(0.5)
        return {"signature": signature, "status": "unknown", "errorCode": "confirmation_timeout", "side": side, "mint": mint}
