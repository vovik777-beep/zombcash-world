"""Collect one bounded market snapshot pass for known launches."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from soi.sources.market_snapshots import MarketSnapshotCollector
from soi.storage import ObservationStore


def main(argv=None):
    parser = argparse.ArgumentParser(description="Collect DexScreener market snapshots for known SOI launches")
    parser.add_argument("--db", required=True)
    parser.add_argument("--mint", action="append", default=[], help="Mint to snapshot; repeatable")
    parser.add_argument("--limit", type=int, default=50, help="Maximum known launches if --mint omitted (1..200)")
    parser.add_argument("--interval", type=float, default=1.1, help="Seconds between requests")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 200:
        parser.error("--limit must be between 1 and 200")
    if args.interval < 1.0:
        parser.error("--interval must be at least 1 second")
    store = ObservationStore(args.db)
    mints = list(dict.fromkeys(args.mint))
    if not mints:
        launches = [o for o in store.observations() if o.kind == "token_created" and o.mint]
        mints = list(dict.fromkeys(o.mint for o in launches))[-args.limit:]
    result = MarketSnapshotCollector(store, min_interval_seconds=args.interval).collect(mints)
    print(result)
    return 0 if not result["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
