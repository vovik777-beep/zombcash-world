"""UTC launch-day cohorts built only from canonical observed lifecycle events."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
import math
from typing import Iterable

from soi.analytics.timeline import build_first_hour_timeline


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _percentile(values: list[float], fraction: float):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _stats(values: list[float]) -> dict:
    return {
        "n": len(values),
        "median": median(values) if values else None,
        "p25": _percentile(values, 0.25),
        "p75": _percentile(values, 0.75),
    }


def build_launch_cohorts(observations: Iterable[object], limit: int = 100) -> dict:
    """Compare launches by UTC day; missing lifecycle evidence remains unknown.

    Completion duration uses official Pump CompleteEvent. Migration duration is
    reported only for timeline status 'confirmed'. First-hour volume is
    Pump.fun-only and only SOL when the quote mint is WSOL.
    """
    items = list(observations)
    launches = {}
    for item in items:
        if item.kind == "token_created" and item.mint and item.event_at:
            launches.setdefault(item.mint, []).append(item)
    selected = list(launches.items())[-max(1, int(limit)):]
    daily = defaultdict(list)
    errors = []

    for mint, launch_rows in selected:
        try:
            timeline = build_first_hour_timeline(items, mint)
            launch_at = _utc(timeline["launch_at"])
            completion_at = timeline.get("bonding_curve_completed_at")
            migration_at = timeline.get("migration_at") if timeline.get("migration_status") == "confirmed" else None
            completion_seconds = (_utc(completion_at) - launch_at).total_seconds() if completion_at else None
            migration_seconds = (_utc(migration_at) - launch_at).total_seconds() if migration_at else None
            if completion_seconds is not None and completion_seconds < 0:
                raise ValueError("Official completion event precedes canonical launch")
            if migration_seconds is not None and migration_seconds < 0:
                raise ValueError("Confirmed migration precedes canonical launch")
            market_rows = []
            for snapshot in items:
                if snapshot.kind != "market_snapshot" or snapshot.mint != mint or not snapshot.observed_at:
                    continue
                observed = _utc(snapshot.observed_at)
                elapsed = (observed - launch_at).total_seconds()
                if not 0 <= elapsed <= 3600:
                    continue
                event = (snapshot.payload or {}).get("event", {})
                market_cap = event.get("market_cap_usd")
                if isinstance(market_cap, (int, float)) and not isinstance(market_cap, bool) and math.isfinite(market_cap) and market_cap >= 0:
                    market_rows.append({"elapsed_seconds": elapsed, "market_cap_usd": float(market_cap),
                                        "observed_at": observed.isoformat(),
                                        "pair_address": event.get("pair_address")})
            market_rows.sort(key=lambda row: (row["elapsed_seconds"], row["observed_at"], row.get("pair_address") or ""))
            market_cap_milestones = {}
            for threshold in (100_000, 500_000, 1_000_000):
                hit = next((row for row in market_rows if row["market_cap_usd"] >= threshold), None)
                market_cap_milestones[str(threshold)] = {
                    "reached": hit is not None,
                    "elapsed_seconds": hit["elapsed_seconds"] if hit else None,
                    "observed_at": hit["observed_at"] if hit else None,
                    "threshold_usd": threshold,
                    "evidence": "observed_dexscreener_marketCap_field" if hit else "not_observed",
                }
            pf = timeline["venues"]["pumpfun"]
            pumpfun_events = [event for event in timeline["events"] if event["venue"] == "pumpfun"]
            buyers = {event["wallet"] for event in pumpfun_events
                      if event["side"] == "buy" and event["wallet"]}
            pumpfun_activity_observed = bool(pumpfun_events)
            pumpfun_quote_coverage_complete = timeline["non_sol_or_unresolved_quote_trades"] == 0
            daily[launch_at.date().isoformat()].append({
                "mint": mint,
                "launch_at": launch_at.isoformat(),
                "completion_elapsed_seconds": completion_seconds,
                "completion_event_observed": completion_seconds is not None,
                "migration_elapsed_seconds": migration_seconds,
                "migration_status": timeline["migration_status"],
                "confirmed_migration": migration_seconds is not None,
                "pumpfun_first_hour_buyers": len(buyers) if pumpfun_activity_observed else None,
                "pumpfun_first_hour_trade_count": pf["buys"] + pf["sells"] if pumpfun_activity_observed else None,
                "pumpfun_first_hour_sol_volume": pf["sol_volume"] if pumpfun_activity_observed and pumpfun_quote_coverage_complete else None,
                "pumpfun_activity_observed": pumpfun_activity_observed,
                "pumpfun_quote_coverage_complete": pumpfun_quote_coverage_complete,
                "pumpfun_sol_volume_scope": "observed_pumpfun_events_with_wsol_quote_only",
                "first_hour_event_count": timeline["event_count"],
                "market_cap_snapshot_count_first_hour": len(market_rows),
                "market_cap_first_hour_max_observed_usd": max((row["market_cap_usd"] for row in market_rows), default=None),
                "market_cap_milestones_first_hour": market_cap_milestones,
                "timeline_coverage_note": timeline["coverage_note"],
            })
        except Exception as exc:
            errors.append({"mint": mint, "error": str(exc)[:240]})

    cohorts = []
    for day, rows in sorted(daily.items()):
        completions = [r["completion_elapsed_seconds"] for r in rows if r["completion_elapsed_seconds"] is not None]
        migrations = [r["migration_elapsed_seconds"] for r in rows if r["migration_elapsed_seconds"] is not None]
        volumes = [r["pumpfun_first_hour_sol_volume"] for r in rows
                   if r["pumpfun_first_hour_sol_volume"] is not None]
        buyers = [r["pumpfun_first_hour_buyers"] for r in rows
                  if r["pumpfun_first_hour_buyers"] is not None]
        milestone_summary = {}
        for threshold in ("100000", "500000", "1000000"):
            reached = [row["market_cap_milestones_first_hour"][threshold]["elapsed_seconds"]
                       for row in rows if row["market_cap_milestones_first_hour"][threshold]["reached"]]
            milestone_summary[threshold] = {
                **_stats(reached),
                "reached": len(reached),
                "not_observed_or_unknown": len(rows) - len(reached),
                "market_cap_source_field": "DexScreener marketCap (not fdv)",
            }
        cohorts.append({
            "launch_day_utc": day,
            "launch_count": len(rows),
            "completion": {
                **_stats(completions),
                "observed": len(completions),
                "not_observed_or_unknown": len(rows) - len(completions),
                "not_observed_is_not_proof_of_non_completion": True,
            },
            "confirmed_migration_elapsed_seconds": {
                **_stats(migrations),
                "confirmed": len(migrations),
                "not_confirmed_or_unknown": len(rows) - len(migrations),
            },
            "pumpfun_first_hour_sol_volume_per_launch": {
                **_stats(volumes), "unknown_or_incomplete": len(rows) - len(volumes)},
            "pumpfun_first_hour_unique_buyers_per_launch": {
                **_stats(buyers), "unknown": len(rows) - len(buyers)},
            "market_cap_milestones_first_hour": milestone_summary,
            "market_cap_snapshot_coverage": {
                "launches_with_at_least_one_market_cap_snapshot": sum(
                    row["market_cap_snapshot_count_first_hour"] > 0 for row in rows),
                "launches_without_market_cap_snapshot": sum(
                    row["market_cap_snapshot_count_first_hour"] == 0 for row in rows),
                "snapshot_count": sum(row["market_cap_snapshot_count_first_hour"] for row in rows),
            },
            "launches": rows,
        })
    return {
        "cohort_unit": "UTC_calendar_day",
        "launches_considered": len(selected),
        "cohort_count": len(cohorts),
        "cohorts": cohorts,
        "errors": errors,
        "coverage": {
            "launch_selection_limit": limit,
            "launches_with_ambiguous_or_invalid_timeline": len(errors),
            "completion_requires_official_pump_complete_event": True,
            "migration_requires_confirmed_canonical_event_pool_match": True,
            "first_hour_volume_is_pumpfun_only": True,
            "non_wsol_quote_volume_excluded_from_sol_volume": True,
            "market_cap_metrics_use_marketCap_not_fdv": True,
            "market_cap_window_seconds_after_launch": 3600,
            "market_cap_thresholds_usd": [100000, 500000, 1000000],
            "missing_data_is_not_zero": True,
        },
    }
