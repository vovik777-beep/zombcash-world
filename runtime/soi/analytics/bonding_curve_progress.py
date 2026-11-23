"""Observed Pump bonding-curve state progression between consecutive snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

WSOL = "So11111111111111111111111111111111111111112"
RESERVE_FIELDS = (
    "virtual_token_reserves", "virtual_quote_reserves",
    "real_token_reserves", "real_quote_reserves",
)


def _time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def summarize_bonding_curve_progress(observations: Iterable[object], mint: str) -> dict:
    """Summarize only observed snapshots; never interpolate unobserved account states."""
    observations = list(observations)
    snapshots = []
    completion_events = []
    for item in observations:
        if item.mint != mint:
            continue
        if item.kind == "bonding_curve_snapshot":
            payload = item.payload if isinstance(item.payload, dict) else {}
            event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
            observed = _time(item.observed_at)
            if observed and isinstance(payload.get("slot"), int):
                snapshots.append({"at": observed, "slot": payload["slot"],
                                  "payload": payload, "event": event})
        elif item.kind == "bonding_curve_completed" and item.event_at:
            completion_events.append(_time(item.event_at))
    snapshots.sort(key=lambda row: (row["at"], row["slot"]))
    # Keep distinct observations only; exact duplicate snapshots add no progression evidence.
    unique = []
    seen = set()
    for row in snapshots:
        key = (row["at"].isoformat(), row["slot"], row["payload"].get("account"))
        if key not in seen:
            seen.add(key)
            unique.append(row)
    snapshots = unique

    transitions = []
    for previous, current in zip(snapshots, snapshots[1:]):
        before, after = previous["event"], current["event"]
        deltas = {}
        for field in RESERVE_FIELDS:
            old, new = before.get(field), after.get(field)
            deltas[field] = new - old if isinstance(old, int) and isinstance(new, int) else None
        old_quote, new_quote = before.get("quote_mint"), after.get("quote_mint")
        quote_mint = new_quote if new_quote == old_quote else None
        transitions.append({
            "from_at": previous["at"].isoformat(),
            "to_at": current["at"].isoformat(),
            "from_slot": previous["slot"], "to_slot": current["slot"],
            "elapsed_seconds": (current["at"] - previous["at"]).total_seconds(),
            "quote_mint": quote_mint,
            "reserve_deltas_raw": deltas,
            "complete_before": before.get("complete"),
            "complete_after": after.get("complete"),
            "completion_transition_observed": before.get("complete") is False and after.get("complete") is True,
        })
    incomplete = [row for row in snapshots if row["event"].get("complete") is False]
    complete = [row for row in snapshots if row["event"].get("complete") is True]
    transition_rows = [row for row in transitions if row["completion_transition_observed"]]
    transition_at = transition_rows[0]["to_at"] if transition_rows else None
    last_incomplete = incomplete[-1]["at"] if incomplete else None
    first_complete = complete[0]["at"] if complete else None

    interval_activity = []
    for transition in transitions:
        start, end = _time(transition["from_at"]), _time(transition["to_at"])
        trades = []
        for item in observations:
            if item.mint != mint or item.kind not in ("trade_buy", "trade_sell") or not item.event_at:
                continue
            when = _time(item.event_at)
            if when and start < when <= end and item.source == "pumpfun_rpc":
                trades.append(item)
        buys = sum(item.kind == "trade_buy" for item in trades)
        sells = sum(item.kind == "trade_sell" for item in trades)
        quote_amount = 0
        quote_complete = True
        quote_mints = set()
        for item in trades:
            event = (item.payload or {}).get("event", {})
            qmint = event.get("quote_mint") or WSOL
            quote_mints.add(qmint)
            amount = event.get("quote_amount") if qmint != WSOL else event.get("sol_amount")
            if isinstance(amount, int):
                quote_amount += amount
            else:
                quote_complete = False
        interval_activity.append({
            "from_at": transition["from_at"], "to_at": transition["to_at"],
            "pumpfun_trade_count": len(trades), "buys": buys, "sells": sells,
            "quote_mint": next(iter(quote_mints)) if len(quote_mints) == 1 else None,
            "quote_volume_raw": quote_amount if quote_complete and len(quote_mints) <= 1 else None,
            "quote_volume_unit": "lamports" if quote_complete and quote_mints == {WSOL} else
                                 "raw_quote_units" if quote_complete and len(quote_mints) == 1 else None,
            "coverage": "observed_pumpfun_trade_events_only",
        })

    observed_completion = bool(completion_events)
    return {
        "mint": mint,
        "snapshot_count": len(snapshots),
        "transition_count": len(transitions),
        "snapshots": [{
            "observed_at": row["at"].isoformat(), "slot": row["slot"],
            "account": row["payload"].get("account"),
            "complete": row["event"].get("complete"),
            "quote_mint": row["event"].get("quote_mint"),
            "reserves_raw": {field: row["event"].get(field) for field in RESERVE_FIELDS},
        } for row in snapshots],
        "transitions": transitions,
        "interval_activity": interval_activity,
        "completion_progress": {
            "last_observed_incomplete_at": last_incomplete.isoformat() if last_incomplete else None,
            "first_observed_complete_at": first_complete.isoformat() if first_complete else None,
            "false_to_true_transition_observed": bool(transition_rows),
            "transition_first_observed_at": transition_at,
            "completion_event_observed": observed_completion,
            "completion_event_times": sorted(t.isoformat() for t in completion_events if t),
            "completion_time_bound_seconds": (
                (first_complete - last_incomplete).total_seconds()
                if last_incomplete and first_complete and first_complete >= last_incomplete else None
            ),
            "interpretation": "Observed state changes are bounded by snapshots; exact on-chain transition time is unknown",
        },
        "coverage": {
            "historical_state_reconstructed": False,
            "snapshot_intervals_complete": False,
            "trade_events_limited_to_pumpfun_rpc": True,
            "missing_snapshots_are_not_zero_activity": True,
        },
    }
