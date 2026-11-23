"""Launch-cohort analytics from append-only observations.

Only observed market-cap values are eligible for milestone calculations.
Missing snapshots are represented as unknown/censored, never as a failed launch.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Iterable

MILESTONES_USD = (100_000, 500_000, 1_000_000)


def _dt(value: str) -> datetime:
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def _event(obs):
    return (getattr(obs, "payload", None) or {}).get("event", {})


def _market_cap(obs):
    e = _event(obs)
    value = e.get("market_cap_usd", e.get("market_cap"))
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _launches(items):
    found = {}
    for o in items:
        if o.kind != "token_created" or not o.mint or not o.event_at:
            continue
        stamp = _dt(o.event_at)
        if o.mint not in found or stamp < found[o.mint][0]:
            found[o.mint] = (stamp, o)
    return found


def analyze_cohorts(observations: Iterable[object], cohort_date: str | None = None) -> dict:
    """Build a daily launch cohort using observed MC snapshots and first-hour trades.

    Snapshot observation kinds accepted: market_snapshot, token_market_snapshot.
    Snapshot payload: {"event": {"market_cap_usd": number, "fdv_usd": number?}}.
    FDV is deliberately never substituted for market cap.
    """
    items = list(observations)
    launches = _launches(items)
    tokens = []
    for mint, (launch_at, launch_obs) in sorted(launches.items(), key=lambda x: x[1][0]):
        day = launch_at.date().isoformat()
        if cohort_date and day != cohort_date:
            continue
        snapshots = []
        for o in items:
            if o.mint != mint or o.kind not in ("market_snapshot", "token_market_snapshot") or not o.event_at:
                continue
            stamp = _dt(o.event_at)
            if stamp < launch_at:
                continue
            mc = _market_cap(o)
            if mc is not None:
                snapshots.append((stamp, mc))
        snapshots.sort(key=lambda x: x[0])
        first_hour = [(t, mc) for t, mc in snapshots if (t-launch_at).total_seconds() <= 3600]
        max_age = max(((t-launch_at).total_seconds() for t, _ in snapshots), default=None)
        milestones = {}
        for target in MILESTONES_USD:
            crossed = next(((t-launch_at).total_seconds() for t, mc in snapshots if mc >= target), None)
            milestones[str(target)] = {
                "crossed": crossed is not None,
                "time_to_seconds": crossed,
                "status": "crossed" if crossed is not None else ("not_observed_within_coverage" if snapshots else "unknown_no_market_cap_data"),
            }
        peak_first_hour = max((mc for _, mc in first_hour), default=None)
        trades = [o for o in items if o.mint == mint and o.kind in ("trade_buy", "trade_sell") and o.event_at
                  and 0 <= (_dt(o.event_at)-launch_at).total_seconds() < 3600]
        buyers = {_event(o).get("user") for o in trades if o.kind == "trade_buy" and _event(o).get("user")}
        sellers = {_event(o).get("user") for o in trades if o.kind == "trade_sell" and _event(o).get("user")}
        tokens.append({
            "mint": mint, "launch_at": launch_at.isoformat(), "cohort_date": day,
            "market_cap_snapshot_count": len(snapshots),
            "market_cap_coverage_seconds": max_age,
            "first_hour_peak_market_cap_usd": peak_first_hour,
            "milestones": milestones,
            "first_hour": {"trade_count": len(trades), "buys": sum(o.kind == "trade_buy" for o in trades),
                           "sells": sum(o.kind == "trade_sell" for o in trades),
                           "unique_buyers": len(buyers), "unique_sellers": len(sellers)},
            "market_cap_data_status": "observed" if snapshots else "missing",
        })
    summary = {}
    for target in MILESTONES_USD:
        vals = [t["milestones"][str(target)]["time_to_seconds"] for t in tokens
                if t["milestones"][str(target)]["crossed"]]
        statuses = [t["milestones"][str(target)]["status"] for t in tokens]
        summary[str(target)] = {
            "crossed_count": len(vals),
            "crossed_rate_among_cohort": (len(vals)/len(tokens)) if tokens else None,
            "median_time_to_seconds_among_crossers": median(vals) if vals else None,
            "unknown_no_market_cap_data": statuses.count("unknown_no_market_cap_data"),
            "not_observed_within_coverage": statuses.count("not_observed_within_coverage"),
        }
    return {
        "cohort_date": cohort_date or (tokens[0]["cohort_date"] if tokens and len({t['cohort_date'] for t in tokens}) == 1 else "mixed"),
        "token_count": len(tokens), "tokens_with_market_cap_data": sum(t["market_cap_data_status"] == "observed" for t in tokens),
        "milestones": summary, "tokens": tokens,
        "methodology": {
            "market_cap_only": True, "fdv_substitution": False,
            "milestone_statuses": ["crossed", "not_observed_within_coverage", "unknown_no_market_cap_data"],
            "time_basis": "UTC event_at relative to first observed token_created event",
            "snapshot_kinds": ["market_snapshot", "token_market_snapshot"],
            "warning": "Crossing rates are descriptive for the observed cohort, not predictive. Tokens without market-cap observations remain unknown.",
        },
    }
