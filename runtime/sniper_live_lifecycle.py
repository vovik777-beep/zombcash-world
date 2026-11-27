"""Fail-closed orchestration for one managed-wallet Live order.

Only the guarded Live entry/exit workers call this module. There is no public
arbitrary-order route; database state, wallet binding, balance and chain checks
are repeated before any broadcast.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import uuid
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sniper_execution import (
    LiveExecutionDisabled,
    LiveExecutionError,
    execute_pumpportal_local_trade,
    fetch_confirmed_transaction,
    fetch_signature_status,
    get_wallet_sol_balance_lamports,
    classify_signature_status,
    live_execution_enabled,
)
from sniper_live_transaction import extract_live_fill

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


async def _apply_confirmed_fill(supabase_request, *, order_id, wallet_public_key, mint, side, signature, chain_slot, transaction_fetcher):
    transaction = await transaction_fetcher(signature)
    if not transaction:
        return False
    fill = extract_live_fill(transaction, wallet_public_key=wallet_public_key, mint=mint, side=side)
    if not fill:
        return False
    block_time = fill.get('blockTime')
    block_time_iso = datetime.fromtimestamp(block_time, tz=timezone.utc).isoformat() if isinstance(block_time, int) else None
    applied = await supabase_request(
        'POST', 'rpc/sniper_live_position_apply_fill',
        payload={
            'p_order_id': order_id,
            'p_token_amount': fill['tokenAmount'],
            'p_sol_amount': fill['solAmount'],
            'p_fee_sol': fill['feeSol'],
            'p_chain_slot': fill.get('slot') or chain_slot,
            'p_block_time': block_time_iso,
        },
    )
    return isinstance(applied, dict)


async def reconcile_unknown_live_orders(supabase_request, *, signature_status_fetcher=fetch_signature_status, limit=5) -> dict:
    rows = await supabase_request("POST", "rpc/sniper_live_unknown_orders", payload={})
    if not isinstance(rows, list):
        return {"checked": 0, "confirmed": 0, "failed": 0, "pending": 0, "errors": 1}
    checked = confirmed = failed = pending = errors = 0
    for order in rows[:max(1, min(int(limit), 20))]:
        if not isinstance(order, dict):
            continue
        checked += 1
        order_id = str(order.get("order_id") or "")
        signature = str(order.get("signature") or "")
        if not order_id or not signature:
            errors += 1
            continue
        try:
            chain_status = await signature_status_fetcher(signature)
            classified = classify_signature_status(chain_status)
            if classified not in {"confirmed", "failed"}:
                pending += 1
                continue
            slot = chain_status.get("slot") if isinstance(chain_status.get("slot"), int) else None
            await _transition(supabase_request, order_id, classified, signature=signature, error_code="chain_transaction_failed" if classified == "failed" else None, slot=slot)
            if classified == "confirmed":
                confirmed += 1
            else:
                failed += 1
        except Exception as exc:
            errors += 1
            log.warning("Unknown Live order reconciliation failed (%s)", type(exc).__name__)
        finally:
            try:
                await supabase_request("POST", "rpc/sniper_live_order_unknown_reconciliation_touch", payload={"p_order_id": order_id})
            except Exception:
                pass
    return {"checked": checked, "confirmed": confirmed, "failed": failed, "pending": pending, "errors": errors}

async def reconcile_confirmed_live_orders(supabase_request, *, transaction_fetcher=fetch_confirmed_transaction, limit=5):
    rows = await supabase_request("POST", "rpc/sniper_live_unfilled_confirmed_orders", payload={})
    if not isinstance(rows, list):
        return {"checked": 0, "applied": 0, "pending": 0, "errors": 1}
    checked = applied = pending = errors = 0
    for order in rows[:max(1, min(int(limit), 20))]:
        if not isinstance(order, dict):
            continue
        checked += 1
        order_id = str(order.get("order_id") or "")
        signature = str(order.get("signature") or "")
        mint = str(order.get("mint") or "")
        side = str(order.get("side") or "")
        if not order_id or not signature or not mint or side not in {"buy", "sell"}:
            errors += 1
            continue
        try:
            wallet = await supabase_request("POST", "rpc/sniper_live_order_wallet_get", payload={"p_order_id": order_id})
            if not isinstance(wallet, dict) or not wallet.get("publicKey"):
                errors += 1
                continue
            if transaction_fetcher is fetch_confirmed_transaction:
                async def fetch_once(sig):
                    return await fetch_confirmed_transaction(sig, attempts=1)
                fetcher = fetch_once
            else:
                fetcher = transaction_fetcher
            filled = await _apply_confirmed_fill(
                supabase_request, order_id=order_id, wallet_public_key=str(wallet["publicKey"]),
                mint=mint, side=side, signature=signature,
                chain_slot=order.get("chain_slot") if isinstance(order.get("chain_slot"), int) else None,
                transaction_fetcher=fetcher,
            )
            if filled:
                applied += 1
            else:
                pending += 1
        except Exception as exc:
            errors += 1
            log.warning("Live fill reconciliation failed (%s)", type(exc).__name__)
        finally:
            try:
                await supabase_request("POST", "rpc/sniper_live_order_fill_reconciliation_touch", payload={"p_order_id": order_id})
            except Exception:
                pass
    return {"checked": checked, "applied": applied, "pending": pending, "errors": errors}

async def live_fill_reconciliation_worker(supabase_request, state):
    while True:
        try:
            unknown = await reconcile_unknown_live_orders(supabase_request, limit=2)
            confirmed_result = await reconcile_confirmed_live_orders(supabase_request, limit=2)
            result = {
                'unknownChecked': unknown['checked'], 'unknownConfirmed': unknown['confirmed'],
                'unknownFailed': unknown['failed'], 'unknownPending': unknown['pending'],
                'confirmedChecked': confirmed_result['checked'], 'fillsApplied': confirmed_result['applied'],
                'fillPending': confirmed_result['pending'], 'errors': unknown['errors'] + confirmed_result['errors'],
            }
            checked = result['unknownChecked'] + result['confirmedChecked']
            state['liveFillReconcilerStatus'] = 'active' if checked else 'idle'
            state['liveFillReconcilerLastAt'] = int(time.time() * 1000)
            state['liveFillReconcilerLastSweep'] = result
            state['liveFillReconcilerLastError'] = None
            if checked:
                log.info('[Sniper] Live reconciliation unknown=%s confirmed=%s fills=%s pending=%s errors=%s', result['unknownChecked'], result['confirmedChecked'], result['fillsApplied'], result['unknownPending'] + result['fillPending'], result['errors'])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state['liveFillReconcilerStatus'] = 'error'
            state['liveFillReconcilerLastError'] = type(exc).__name__
            log.warning('[Sniper] Live fill reconciler failed (%s)', type(exc).__name__)
        await asyncio.sleep(30)

async def submit_live_order(
    *,
    telegram_id: int,
    client_order_id: str,
    mint: str,
    side: str,
    amount: float,
    supabase_request: Callable[..., Awaitable[Any]],
    executor: Callable[..., Awaitable[dict]] = execute_pumpportal_local_trade,
    balance_checker: Callable[..., Awaitable[int]] = get_wallet_sol_balance_lamports,
    transaction_fetcher: Callable[..., Awaitable[dict | None]] = fetch_confirmed_transaction,
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
    if not isinstance(settings, dict):
        raise LiveExecutionDisabled("live_user_settings_unavailable")
    if side == "buy" and (settings.get("mode") != "live" or not settings.get("enabled") or not settings.get("live_armed")):
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
    if (
        not isinstance(wallet, dict)
        or not wallet.get("publicKey")
        or wallet.get("status") != "active"
        or wallet.get("custodyMode") != "managed"
        or (prepared.get("walletId") and wallet.get("walletId") != prepared.get("walletId"))
    ):
        await _transition(supabase_request, order_id, "failed", error_code="active_managed_wallet_required")
        return {"orderId": order_id, "status": "failed", "errorCode": "active_managed_wallet_required"}

    execution_fee = priority_fee_sol
    if execution_fee is None:
        try:
            execution_fee = float(os.getenv("SNIPER_PRIORITY_FEE_SOL", "0.00005"))
        except (TypeError, ValueError) as exc:
            await _transition(supabase_request, order_id, "failed", error_code="invalid_live_fee_configuration")
            return {"orderId": order_id, "status": "failed", "errorCode": "invalid_live_fee_configuration"}
    try:
        execution_fee = float(execution_fee)
    except (TypeError, ValueError):
        await _transition(supabase_request, order_id, "failed", error_code="invalid_live_fee_configuration")
        return {"orderId": order_id, "status": "failed", "errorCode": "invalid_live_fee_configuration"}
    if not math.isfinite(execution_fee) or not 0 <= execution_fee <= 0.01:
        await _transition(supabase_request, order_id, "failed", error_code="invalid_live_fee_configuration")
        return {"orderId": order_id, "status": "failed", "errorCode": "invalid_live_fee_configuration"}

    if side == "buy":
        try:
            reserve_sol = float(os.getenv("SNIPER_LIVE_RESERVE_SOL", "0.01"))
        except (TypeError, ValueError):
            reserve_sol = -1
        if not math.isfinite(reserve_sol) or not 0 <= reserve_sol <= 1:
            await _transition(supabase_request, order_id, "failed", error_code="invalid_live_reserve_configuration")
            return {"orderId": order_id, "status": "failed", "errorCode": "invalid_live_reserve_configuration"}
        try:
            balance_lamports = await balance_checker(str(wallet["publicKey"]))
            required_lamports = math.ceil((numeric_amount + execution_fee + reserve_sol) * 1_000_000_000)
            if not isinstance(balance_lamports, int) or isinstance(balance_lamports, bool) or balance_lamports < required_lamports:
                await _transition(supabase_request, order_id, "failed", error_code="insufficient_wallet_sol_balance")
                return {"orderId": order_id, "status": "failed", "errorCode": "insufficient_wallet_sol_balance"}
        except Exception as exc:
            log.warning("Live wallet balance check failed (%s)", type(exc).__name__)
            await _transition(supabase_request, order_id, "failed", error_code="solana_balance_lookup_failed")
            return {"orderId": order_id, "status": "failed", "errorCode": "solana_balance_lookup_failed"}

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
            priority_fee_sol=float(execution_fee),
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

    final_status = str(saved.get("status") or result_status)
    fill_applied = False
    if final_status == "confirmed" and signature:
        try:
            fill_applied = await _apply_confirmed_fill(
                supabase_request, order_id=order_id, wallet_public_key=str(wallet["publicKey"]),
                mint=mint, side=side, signature=signature, chain_slot=slot,
                transaction_fetcher=transaction_fetcher,
            )
        except Exception as exc:
            log.warning("Confirmed Live fill not yet applied (%s)", type(exc).__name__)
    return {
        "orderId": order_id,
        "status": final_status,
        "signature": saved.get("signature") or signature,
        "chainSlot": saved.get("chainSlot") or slot,
        "errorCode": saved.get("errorCode") or error_code,
        "fillApplied": fill_applied,
        "fillPending": final_status == "confirmed" and not fill_applied,
        "reused": False,
    }
