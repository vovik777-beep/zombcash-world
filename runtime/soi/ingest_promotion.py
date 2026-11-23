"""Capture latest DexScreener promotion observations as time-stamped evidence.

This is an optional enrichment adapter. Latest endpoints are rolling lists, not
proof of complete promotion history. Data reuse/display must respect source terms.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
from soi.models import Observation
from soi.sources.dexscreener import DexScreenerClient
from soi.storage import ObservationStore


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def capture(client, store):
    now = utc_now()
    stats = {"profiles": 0, "ads": 0, "boosts": "existing boost_monitor", "orders": 0}
    # Boost observations are written by the existing 15-second boost_monitor.py
    # process into this same SOI database; do not poll the same DexScreener endpoint twice.
    endpoints = (("profiles", client.latest_profiles, "profile_observed"),
                 ("ads", client.latest_ads, "paid_ad_observed"))
    for label, fetch, kind in endpoints:
        try:
            records = fetch()
        except Exception as exc:
            stats[label + "_error"] = str(exc)[:240]
            continue
        for item in records:
            mint = item.get("tokenAddress") or item.get("address")
            if not mint:
                continue
            ref = str(item.get("url") or item.get("amount") or item.get("totalAmount") or "item")
            obs = Observation("dexscreener", f"{kind}:{mint}:{ref}:{now}", now, now,
                              kind, mint, {"event": item}, "observed")
            store.add(obs)
            stats[label] += 1
    # Paid orders are queried only for tokens with a launch observation; this
    # bounded pass avoids broad fan-out against the API.
    mints = list(dict.fromkeys(o.mint for o in store.observations()
                               if o.kind == "token_created" and o.mint))[-50:]
    for mint in mints:
        try:
            orders = client.token_orders(mint)
            for item in orders:
                status = item.get("status") or item.get("type") or "order"
                obs = Observation("dexscreener", f"order:{mint}:{status}:{now}", now, now,
                                  "paid_order_observed", mint, {"event": item}, "observed")
                store.add(obs)
                stats["orders"] += 1
        except Exception as exc:
            stats.setdefault("order_errors", []).append({"mint": mint, "error": str(exc)[:180]})
    stats["observation_count"] = store.count()
    return stats


def main(argv=None):
    p = argparse.ArgumentParser(description="Capture latest promotion signals for known SOI tokens")
    p.add_argument("--db", required=True)
    a = p.parse_args(argv)
    result = capture(DexScreenerClient(), ObservationStore(Path(a.db)))
    print(result)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
