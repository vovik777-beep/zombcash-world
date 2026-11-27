"""Fail-closed orchestration for one authenticated Live order.

This module is deliberately not wired to an API route yet. Live readiness remains
false until transaction-fill accounting, position management, and recovery workers
are implemented and tested end to end.
"""
from __future__ import annotations

import logging
import math
import uuid
from typing import Any, Awaitable, Callable

from sniper_execution import (
    LiveExecutionDisabled,
    LiveExecutionError,
    execute_pumpportal_local_trade,
    live_execution_enabled,
)

log = logging.getLogger("zombcash.sniper.live_lifecycle")


async def _transition(
    supabase_request: Callable[..., Awaitable[Any]],
    order_id: str,
    status: str,
    *,
    signature: str | None = None,
    error_code: str | None = None,
    chain_slot: int | None = None,
) -> dict:
    result = await supabase_request(
        "POST",
        "rpc/sniper_live_order_transition",
        payload={
            "p_order_id": order_id,
            "p_status": status,
            "p_signature": signature,
            "p_error_code": error_code,
            "p_chain_slot": chain_slot,
        },
    )
    if not isinstance(result, dict):
        raise LiveExecutionError("live_order_transition_unavailable")
    return result


async def submit_live_order(
    *,
    telegram_id: int,
    client_order_id: str,
    mint: str,
    side: str,
    amount: float,
    supabase_request: Callable[..., Awaitable[Any]],
    executor: Callable[..., Awaitable[dict]] = execute_pumpportal_local_trade,
    priority_fee_sol: float | None = None,
) -> dict:
    """Prepare, lock, sign, broadcast and record one idempotent Live order.

    The caller must enforce the higher-level live readiness gate. This function
    additionally requires the global execution flag and marks the database order
    as broadcasting before the network broadcast begins. Any inconclusive result
    remains non-retryable until a separate reconciler resolves it.
    """
    if not live_execution_enabled():
        raise LiveExecutionDisabled("live_execution_globally_disabled")
    if not isinstance(telegram_id, int) or isinstance(telegram_id, bool) or telegram_id <= 0:
        raise ValueError("invalid_telegram_id")
    try:
        normalized_order_id = str(uuid.UUID(str(client_order_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("invalid_client_order_id") from exc
    if side not in {"buy", "sell"}:
        raise ValueError("invalid_trade_side")
    try:
        numeric_amount = float(amount)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_trade_amount") from exc
    if not math.isfinite(numeric_amount) or numeric_amount <= 0:
        raise ValueError("invalid_trade_amount")
    settings = await supabase_request(
        "POST",
        "rpc/sniper_user_settings_get",
        payload={"p_telegram_id": telegram_id},
    )
    if not isinstance(settings, dict) or settings.get("mode") != "live" or not settings.get("enabled") or not settings.get("live_armed"):
        raise LiveExecutionDisabled("live_trading_not_armed")
    try:
        max_slippage_bps = int(settings.get("max_slippage_bps", 300))
    except (TypeError, ValueError) as exc:
        raise LiveExecutionError("invalid_stored_slippage") from exc
    if not 1 <= max_slippage_bps <= 5000:
        raise LiveExecutionError("invalid_stored_slippage")

    amount_unit = "sol" if side == "buy" else "token"
    prepared = await supabase_request(
        "POST",
        "rpc/sniper_live_order_prepare",
        payload={
            "p_telegram_id": telegram_id,
            "p_client_order_id": normalized_order_id,
            "p_mint": mint,
            "p_side": side,
            "p_amount": numeric_amount,
            "p_amount_unit": amount_unit,
        },
    )
    if not isinstance(prepared, dict) or not prepared.get("orderId"):
        raise LiveExecutionError("live_order_prepare_unavailable")

    order_id = str(prepared["orderId"])
    if prepared.get("status") != "prepared":
        # A retry of broadcasting/submitted/unknown/terminal orders never signs
        # or broadcasts again. The caller must use the order reconciliation path.
        return {
            "orderId": order_id,
            "status": str(prepared.get("status") or "unknown"),
            "signature": prepared.get("signature"),
            "reused": True,
        }

    wallet = await supabase_request(
        "POST",
        "rpc/sniper_live_order_wallet_get",
        payload={"p_order_id": order_id},
    )
    if not isinstance(wallet, dict) or not wallet.get("publicKey") or wallet.get("status") != "active":
        await _transition(supabase_request, order_id, "failed", error_code="active_managed_wallet_required")
        return {"orderId": order_id, "status": "failed", "errorCode": "active_managed_wallet_required"}

    secret = await supabase_request(
        "POST",
        "rpc/sniper_live_order_wallet_secret",
        payload={"p_order_id": order_id},
    )
    if not isinstance(secret, str) or not secret:
        await _transition(supabase_request, order_id, "failed", error_code="wallet_secret_unavailable")
        return {"orderId": order_id, "status": "failed", "errorCode": "wallet_secret_unavailable"}

    # This durable transition is the retry lock. Do not call the executor unless
    # the database has acknowledged that the order entered broadcasting.
    try:
        await _transition(supabase_request, order_id, "broadcasting")
    except Exception:
        secret = None
        raise

    try:
        result = await executor(
            public_key=str(wallet["publicKey"]),
            secret_key_base58=secret,
            mint=mint,
            side=side,
            amount=numeric_amount,
            max_slippage_bps=max_slippage_bps,
            priority_fee_sol=priority_fee_sol,
        )
    except LiveExecutionDisabled as exc:
        code = str(exc)[:100] or "live_execution_disabled"
        await _transition(supabase_request, order_id, "failed", error_code=code)
        return {"orderId": order_id, "status": "failed", "errorCode": code}
    except LiveExecutionError as exc:
        code = str(exc)[:100] or "live_execution_failed"
        await _transition(supabase_request, order_id, "failed", error_code=code)
        return {"orderId": order_id, "status": "failed", "errorCode": code}
    except Exception as exc:
        # Once the broadcasting lock is set, an unexpected exception is
        # ambiguous. Preserve the order as unknown; never submit it again.
        log.warning("Live order outcome unknown (%s)", type(exc).__name__)
        try:
            await _transition(
                supabase_request, order_id, "unknown",
                error_code="execution_outcome_unknown",
            )
        except Exception as transition_exc:
            log.error("Could not persist unknown Live order state (%s)", type(transition_exc).__name__)
        return {"orderId": order_id, "status": "unknown", "errorCode": "execution_outcome_unknown"}
    finally:
        # Do not retain the Vault secret in module state or return values.
        secret = None

    if not isinstance(result, dict):
        result = {"status": "unknown", "errorCode": "invalid_execution_result"}
    result_status = str(result.get("status") or "unknown")
    signature = str(result.get("signature") or "") or None
    slot = result.get("slot") if isinstance(result.get("slot"), int) and not isinstance(result.get("slot"), bool) else None
    error_code = str(result.get("errorCode") or "")[:100] or None

    try:
        if result_status == "confirmed":
            # The ledger requires a submitted state before confirmed.
            await _transition(supabase_request, order_id, "submitted", signature=signature, chain_slot=slot)
            saved = await _transition(supabase_request, order_id, "confirmed", signature=signature, chain_slot=slot)
        elif result_status in {"failed", "unknown", "submitted"}:
            saved = await _transition(
                supabase_request, order_id, result_status,
                signature=signature, error_code=error_code, chain_slot=slot,
            )
        else:
            saved = await _transition(
                supabase_request, order_id, "unknown",
                signature=signature, error_code="unrecognized_execution_status",
                chain_slot=slot,
            )
    except Exception as exc:
        # A chain result exists but the DB update failed. The broadcasting state
        # blocks retries; a reconciler can recover using the signature.
        log.error("Live order ledger update failed after execution (%s)", type(exc).__name__)
        return {
            "orderId": order_id,
            "status": "unknown",
            "signature": signature,
            "errorCode": "order_ledger_update_failed",
        }

    return {
        "orderId": order_id,
        "status": str(saved.get("status") or result_status),
        "signature": saved.get("signature") or signature,
        "chainSlot": saved.get("chainSlot") or slot,
        "errorCode": saved.get("errorCode") or error_code,
        "reused": False,
    }
