"""First-hour snapshot summaries; rolling API counters are never summed across snapshots."""
def summarize_snapshots(snapshots):
    ordered = sorted(snapshots, key=lambda x: x["minute"])
    if not ordered:
        return {"coverage_minutes": [], "peak_market_cap_usd": None,
                "market_cap_change_pct": None, "latest_snapshot_volume_usd": None,
                "latest_snapshot_transactions": None, "crossed_1m_market_cap": None,
                "counter_method": "no_observations"}
    first, last = ordered[0], ordered[-1]
    start, end = first.get("market_cap_usd"), last.get("market_cap_usd")
    return {
        "coverage_minutes": [x["minute"] for x in ordered],
        "peak_market_cap_usd": max((x.get("market_cap_usd") or 0) for x in ordered),
        "market_cap_change_pct": None if not start else ((end-start)/start)*100,
        # DexScreener volume/transaction counters are rolling-window snapshots,
        # not disjoint per-interval deltas. Summing them double-counts activity.
        "latest_snapshot_volume_usd": last.get("volume_usd"),
        "latest_snapshot_transactions": last.get("txns"),
        "crossed_1m_market_cap": any((x.get("market_cap_usd") or 0) >= 1_000_000 for x in ordered),
        "counter_method": "latest_observed_snapshot_only; rolling counters are not summed",
    }
