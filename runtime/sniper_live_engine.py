"""Live entry and exit orchestration. Both loops fail closed behind readiness and global gates."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sniper_execution import live_execution_enabled, live_execution_ready
from sniper_live_lifecycle import submit_live_order

log = logging.getLogger("zombcash.sniper.live_engine")


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def process_live_entry_candidates(
    supabase_request: Callable[..., Awaitable[Any]],
    *,
    submit_order: Callable[..., Awaitable[dict]] = submit_live_order,
    limit: int = 20,
) -> dict:
    rows = await supabase_request("POST", "rpc/sniper_live_entry_candidates", payload={})
    if not isinstance(rows, list):
        return {"checked": 0, "submitted": 0, "confirmed": 0, "unknown": 0, "failed": 0, "errors": 1}
    checked = submitted = confirmed = unknown = failed = errors = 0
    for row in rows[:max(1, min(int(limit), 50))]:
        if not isinstance(row, dict):
            continue
        checked += 1
        try:
            telegram_id = int(row.get("telegram_id"))
            mint = str(row.get("mint") or "")
            launch_at = str(row.get("launch_at") or "")
            amount = float(row.get("max_trade_sol"))
            if telegram_id <= 0 or not mint or not launch_at or amount <= 0:
                errors += 1
                continue
            client_order_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"zombcash-live-entry:{telegram_id}:{mint}:{launch_at}",
            ))
            result = await submit_order(
                telegram_id=telegram_id,
                client_order_id=client_order_id,
                mint=mint,
                side="buy",
                amount=amount,
                supabase_request=supabase_request,
            )
            status = str(result.get("status") or "unknown") if isinstance(result, dict) else "unknown"
            if status in {"prepared", "broadcasting", "submitted"}:
                submitted += 1
            elif status == "confirmed":
                confirmed += 1
            elif status == "failed":
                failed += 1
            else:
                unknown += 1
        except Exception as exc:
            errors += 1
            log.warning("Live entry candidate failed (%s)", type(exc).__name__)
    return {
        "checked": checked, "submitted": submitted, "confirmed": confirmed,
        "unknown": unknown, "failed": failed, "errors": errors,
    }


async def process_live_exit_positions(
    supabase_request: Callable[..., Awaitable[Any]],
    *,
    submit_order: Callable[..., Awaitable[dict]] = submit_live_order,
    now: datetime | None = None,
    limit: int = 50,
) -> dict:
    rows = await supabase_request("POST", "rpc/sniper_live_open_positions", payload={})
    if not isinstance(rows, list):
        return {"checked": 0, "triggered": 0, "stale": 0, "errors": 1}
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    checked = triggered = stale = errors = 0
    for row in rows[:max(1, min(int(limit), 100))]:
        if not isinstance(row, dict):
            continue
        checked += 1
        try:
            last_mark_at = _parse_datetime(row.get("last_mark_at"))
            if last_mark_at is None or (current - last_mark_at).total_seconds() > 90:
                stale += 1
                continue
            quantity = float(row.get("quantity") or 0)
            net_pct = float(row.get("net_pnl_pct"))
            take_profit = float(row.get("take_profit_pct") or 0)
            stop_loss = float(row.get("stop_loss_pct") or 0)
            if quantity <= 0:
                continue

            reason = None
            if take_profit > 0 and net_pct >= take_profit:
                reason = "target"
            elif stop_loss > 0 and net_pct <= -stop_loss:
                reason = "stop"
            else:
                idle_seconds = int(row.get("idle_exit_seconds") or 0)
                last_movement_at = _parse_datetime(row.get("last_movement_at")) or _parse_datetime(row.get("opened_at"))
                if idle_seconds > 0 and last_movement_at and current >= last_movement_at + timedelta(seconds=idle_seconds):
                    reason = "stagnant"
            if reason is None:
                continue

            telegram_id = int(row.get("telegram_id"))
            position_id = str(row.get("position_id") or "")
            mint = str(row.get("mint") or "")
            if telegram_id <= 0 or not position_id or not mint:
                errors += 1
                continue
            previous_exit = str(row.get("last_exit_order_id") or "none")
            if previous_exit != "none":
                retry_allowed = await supabase_request(
                    "POST",
                    "rpc/sniper_live_exit_retry_allowed",
                    payload={"p_order_id": previous_exit, "p_cooldown_seconds": 30},
                )
                if retry_allowed is not True:
                    continue

            client_order_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"zombcash-live-exit:{position_id}:{reason}:{previous_exit}",
            ))
            result = await submit_order(
                telegram_id=telegram_id,
                client_order_id=client_order_id,
                mint=mint,
                side="sell",
                amount=quantity,
                supabase_request=supabase_request,
            )
            if isinstance(result, dict) and result.get("status") == "failed" and result.get("orderId"):
                await supabase_request(
                    "POST",
                    "rpc/sniper_live_position_note_exit_attempt",
                    payload={"p_position_id": position_id, "p_order_id": str(result["orderId"])},
                )
            triggered += 1
        except Exception as exc:
            errors += 1
            log.warning("Live exit position check failed (%s)", type(exc).__name__)
    return {"checked": checked, "triggered": triggered, "stale": stale, "errors": errors}


async def live_entry_worker(supabase_request, state) -> None:
    while True:
        try:
            if not live_execution_ready() or not live_execution_enabled():
                state["liveEntryStatus"] = "locked"
            else:
                result = await process_live_entry_candidates(supabase_request)
                state["liveEntryStatus"] = "active"
                state["liveEntryLastAt"] = int(time.time() * 1000)
                state["liveEntryLastSweep"] = result
                if result["checked"]:
                    log.info("[Sniper] Live entry sweep checked=%s confirmed=%s failed=%s errors=%s", result["checked"], result["confirmed"], result["failed"], result["errors"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state["liveEntryStatus"] = "error"
            state["liveEntryLastError"] = type(exc).__name__
            log.warning("[Sniper] Live entry worker failed (%s)", type(exc).__name__)
        await asyncio.sleep(5)


async def live_exit_worker(supabase_request, state) -> None:
    while True:
        try:
            if not live_execution_ready() or not live_execution_enabled():
                state["liveExitStatus"] = "locked"
            else:
                result = await process_live_exit_positions(supabase_request)
                state["liveExitStatus"] = "active"
                state["liveExitLastAt"] = int(time.time() * 1000)
                state["liveExitLastSweep"] = result
                if result["triggered"]:
                    log.info("[Sniper] Live exit sweep checked=%s triggered=%s stale=%s errors=%s", result["checked"], result["triggered"], result["stale"], result["errors"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state["liveExitStatus"] = "error"
            state["liveExitLastError"] = type(exc).__name__
            log.warning("[Sniper] Live exit worker failed (%s)", type(exc).__name__)
        await asyncio.sleep(5)
