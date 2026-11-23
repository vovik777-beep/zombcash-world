"""Cross-venue launch-to-first-hour timeline with explicit quote-unit handling."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Iterable

LAMPORTS_PER_SOL = 1_000_000_000
WSOL = "So11111111111111111111111111111111111111112"


def _dt(value: str) -> datetime:
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def build_first_hour_timeline(observations: Iterable[object], mint: str) -> dict:
    """Combine Pump.fun and PumpSwap observations for [launch, launch+60m).

    PumpSwap quote amounts are converted to SOL only when the observed pool
    creation event confirms WSOL as quote mint. Other quote assets are excluded
    from SOL volume, never treated as SOL by assumption.
    """
    items = list(observations)
    launches = [o for o in items if o.mint == mint and o.kind == "token_created" and o.event_at]
    if not launches:
        raise ValueError(f"No observed token_created event for mint {mint}")
    launch_evidence = set()
    for item in launches:
        payload = item.payload if isinstance(item.payload, dict) else {}
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        launch_evidence.add((
            payload.get("signature"),
            _dt(item.event_at).isoformat(),
            event.get("creator"),
            event.get("user"),
        ))
    if len(launch_evidence) > 1:
        raise ValueError(
            f"Ambiguous launch evidence for mint {mint}: "
            f"{len(launch_evidence)} distinct signature/time/creator records"
        )
    # Exact repeated observations of the same transaction/event are harmless;
    # conflicting launch records must never be resolved by choosing min(time).
    launch = launches[0]
    start, end = _dt(launch.event_at), _dt(launch.event_at) + timedelta(minutes=60)

    all_pool_rows = {}
    for o in items:
        if o.kind == "pool_created":
            e = (o.payload or {}).get("event", {})
            if e.get("pool"):
                all_pool_rows.setdefault(e["pool"], set()).add((
                    e.get("base_mint"), e.get("quote_mint"),
                    e.get("base_mint_decimals"), e.get("quote_mint_decimals"),
                    _dt(o.event_at).isoformat() if o.event_at else None,
                ))
    # Only pools with at least one observed mapping to this mint affect this timeline,
    # but compare every record for such a pool to catch a conflicting base-mint mapping.
    pool_rows = {
        pool: rows for pool, rows in all_pool_rows.items()
        if any(row[0] == mint for row in rows)
    }
    conflicting_pools = [pool for pool, rows in pool_rows.items() if len(rows) > 1]
    if conflicting_pools:
        raise ValueError(
            f"Ambiguous PumpSwap pool metadata for mint {mint}: "
            f"{', '.join(sorted(conflicting_pools))}"
        )
    pool_info = {}
    for pool, rows in pool_rows.items():
        row = sorted(rows, key=lambda value: value[4] or "")[0]
        pool_info[pool] = {
            "quote_mint": row[1], "quote_decimals": row[3],
            "created_at": min((value[4] for value in rows if value[4]), default=None),
        }

    events = []
    totals = {"pumpfun": {"buys": 0, "sells": 0, "sol_volume": 0},
              "pumpswap": {"buys": 0, "sells": 0, "sol_volume": 0}}
    unique_buyers, unique_sellers = set(), set()
    non_sol_quote = 0
    for o in items:
        if o.mint != mint or o.kind not in ("trade_buy", "trade_sell") or not o.event_at:
            continue
        when = _dt(o.event_at)
        if not (start <= when < end):
            continue
        e = (o.payload or {}).get("event", {})
        venue = "pumpswap" if o.source == "pumpswap_rpc" else "pumpfun"
        side = "buy" if o.kind == "trade_buy" else "sell"
        totals[venue]["buys" if side == "buy" else "sells"] += 1
        wallet = e.get("user")
        if wallet:
            (unique_buyers if side == "buy" else unique_sellers).add(wallet)

        sol_lamports = None
        quote_mint = e.get("quote_mint")
        if venue == "pumpfun":
            quote_mint = quote_mint or WSOL
            if quote_mint == WSOL:
                sol_lamports = int(e.get("sol_amount", 0))
        else:
            pool = e.get("pool")
            pool_meta = pool_info.get(pool, {})
            quote_mint = pool_meta.get("quote_mint")
            if quote_mint == WSOL:
                amount_key = "quote_amount_in" if side == "buy" else "quote_amount_out"
                sol_lamports = int(e.get(amount_key, 0))
        if sol_lamports is None:
            non_sol_quote += 1
        else:
            totals[venue]["sol_volume"] += sol_lamports / LAMPORTS_PER_SOL

        events.append({"event_at": when.isoformat(), "venue": venue, "side": side,
            "wallet": wallet, "signature": (o.payload or {}).get("signature"),
            "quote_mint": quote_mint,
            "sol_amount": None if sol_lamports is None else sol_lamports / LAMPORTS_PER_SOL,
            "source_ref": o.source_ref, "slot": (o.payload or {}).get("slot"), "log_index": (o.payload or {}).get("log_index")})

    # Log index is authoritative only within one transaction.
    events.sort(key=lambda x: (
        x["event_at"],
        x["slot"] if isinstance(x["slot"], int) else -1,
        x["signature"] or "",
        x["log_index"] if isinstance(x["log_index"], int) else -1,
    ))
    same_slot_time_groups = {}
    for event in events:
        if event["slot"] is not None:
            same_slot_time_groups.setdefault((event["event_at"], event["slot"]), set()).add(event["signature"])
    ambiguous_cross_transaction_ties = sum(
        1 for signatures in same_slot_time_groups.values()
        if len([signature for signature in signatures if signature]) > 1
    )
    checkpoints = {}
    for minute in (1, 5, 15, 30, 60):
        cutoff = start + timedelta(minutes=minute)
        selected = [e for e in events if _dt(e["event_at"]) < cutoff]
        checkpoints[str(minute)] = {
            "trade_count": len(selected),
            "buys": sum(e["side"] == "buy" for e in selected),
            "sells": sum(e["side"] == "sell" for e in selected),
            "unique_wallets": len({e["wallet"] for e in selected if e["wallet"]}),
            "sol_volume": sum(e["sol_amount"] or 0 for e in selected),
        }
    curve_completion_events = [o for o in items if o.kind == "bonding_curve_completed" and o.mint == mint and o.event_at]
    curve_completion_keys = {
        ((o.payload or {}).get("signature"),
         (o.payload or {}).get("event", {}).get("bonding_curve"),
         _dt(o.event_at).isoformat())
        for o in curve_completion_events
    }
    if len(curve_completion_keys) > 1:
        raise ValueError(f"Ambiguous Pump bonding-curve completion evidence for mint {mint}")
    curve_completion = curve_completion_events[0] if curve_completion_events else None
    curve_completion_payload = (curve_completion.payload or {}) if curve_completion else {}
    curve_completion_at = _dt(curve_completion.event_at).isoformat() if curve_completion else None
    curve_completion_signature = curve_completion_payload.get("signature") if curve_completion else None

    migration_events = [o for o in items if o.kind == "migration_completed" and o.mint == mint and o.event_at]
    migration_keys = set()
    for item in migration_events:
        event = (item.payload or {}).get("event", {})
        migration_keys.add(((item.payload or {}).get("signature"), event.get("pool"),
                           event.get("quote_mint"), _dt(item.event_at).isoformat()))
    if len(migration_keys) > 1:
        raise ValueError(f"Ambiguous Pump AMM migration evidence for mint {mint}")
    migration_event = migration_events[0] if migration_events else None
    migration_payload = (migration_event.payload or {}) if migration_event else {}
    if migration_event and curve_completion:
        completion_signature = curve_completion_payload.get("signature")
        migration_signature_for_order = migration_payload.get("signature")
        completion_log_index = curve_completion_payload.get("log_index")
        migration_log_for_order = migration_payload.get("log_index")
        if completion_signature == migration_signature_for_order and (
            isinstance(completion_log_index, int) and isinstance(migration_log_for_order, int)
            and completion_log_index >= migration_log_for_order
        ):
            raise ValueError(f"Pump AMM migration precedes bonding-curve completion in transaction for mint {mint}")
        if _dt(migration_event.event_at) < _dt(curve_completion.event_at):
            raise ValueError(f"Pump AMM migration timestamp precedes bonding-curve completion for mint {mint}")
    migration_data = migration_payload.get("event", {})
    migration_pool = migration_data.get("pool")
    matching_pool_rows = [o for o in items if o.kind == "pool_created" and o.mint == mint
        and (o.payload or {}).get("signature") == migration_payload.get("signature")
        and ((o.payload or {}).get("event", {}).get("pool") == migration_pool)] if migration_event and migration_pool else []
    migration_pool_metadata_match = any(
        (o.payload or {}).get("event", {}).get("quote_mint") == migration_data.get("quote_mint")
        for o in matching_pool_rows
    )
    migration_log_index = migration_payload.get("log_index")
    ordered_pool_rows = [o for o in matching_pool_rows
        if isinstance((o.payload or {}).get("log_index"), int)
        and isinstance(migration_log_index, int)
        and (o.payload or {}).get("log_index") < migration_log_index]
    order_verifiable = bool(matching_pool_rows) and isinstance(migration_log_index, int) and all(
        isinstance((o.payload or {}).get("log_index"), int) for o in matching_pool_rows)
    if migration_event and matching_pool_rows and migration_pool_metadata_match and ordered_pool_rows:
        migration_status = "confirmed"
    elif migration_event and matching_pool_rows and not order_verifiable:
        migration_status = "migration_pool_match_order_unverified"
    elif migration_event:
        migration_status = "migration_event_only" if not matching_pool_rows else "migration_pool_metadata_or_order_mismatch"
    elif pool_info:
        migration_status = "pool_without_completion_event"
    else:
        migration_status = "not_observed"
    migration_at = _dt(migration_event.event_at).isoformat() if migration_event else None
    return {"mint": mint, "launch_at": start.isoformat(),
        "window_end": end.isoformat(),
        "bonding_curve_completed": curve_completion is not None,
        "bonding_curve_completed_at": curve_completion_at,
        "bonding_curve_completion_signature": curve_completion_signature,
        "bonding_curve_completion_evidence": "official_pump_complete_event" if curve_completion else "not_observed",
        "migration_observed": migration_event is not None,
        "migration_status": migration_status,
        "migration_at": migration_at, "migration_pool": migration_pool if migration_event else None,
        "migration_signature": migration_payload.get("signature") if migration_event else None,
        "migration_pool_creation_matched": bool(matching_pool_rows and migration_pool_metadata_match and ordered_pool_rows),
        "migration_pool_order_verified": bool(ordered_pool_rows),
        "checkpoints_minutes": checkpoints,
        "pools": [{"pool": p, **v} for p, v in sorted(pool_info.items())],
        "events": events, "event_count": len(events),
        "unique_buyers": len(unique_buyers), "unique_sellers": len(unique_sellers),
        "venues": totals,
        "sol_volume_total": totals["pumpfun"]["sol_volume"] + totals["pumpswap"]["sol_volume"],
        "non_sol_or_unresolved_quote_trades": non_sol_quote,
        "ordering_quality": {"within_transaction": "program_log_index" if all(event["log_index"] is not None for event in events) else "partial_or_unavailable", "ambiguous_same_slot_same_time_cross_transaction_groups": ambiguous_cross_transaction_ties, "cross_transaction_tie_breaker": "signature_lexical_only_not_chain_order", "note": "Transaction index is not available in the current getTransaction collector; events from different signatures sharing the same slot and event timestamp cannot be canonically ordered."},
        "coverage_note": "Only observed decoded events are included; missing signatures, unavailable logs and unresolved pools create gaps.",
        "method": "UTC event time in [launch, launch+60m); Pump.fun sol_amount and PumpSwap quote amounts count as SOL only when quote mint is confirmed WSOL by event evidence."}
