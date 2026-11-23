"""Export snapshot collection history and latest per-token coverage."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from soi.storage import ObservationStore


def build_coverage_report(store: ObservationStore, limit: int = 200) -> dict:
    launches = [o for o in store.observations() if o.kind == "token_created" and o.mint]
    mints = list(dict.fromkeys(o.mint for o in launches))[-limit:]
    return {
        "schema_version": "1.0",
        "source": "local_soi_database",
        "launches_checked": len(mints),
        "snapshot_coverage": store.latest_snapshot_coverage(mints),
        "recent_collection_runs": store.collection_history(20),
        "interpretation": {
            "missing_snapshot": "unknown; not zero volume, zero liquidity, or proof of no market",
            "coverage_scope": "latest snapshot existence only; not continuous first-hour completeness"
        }
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export SOI snapshot coverage and collection history")
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 10000:
        parser.error("--limit must be between 1 and 10000")
    report = build_coverage_report(ObservationStore(args.db), args.limit)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Exported coverage for {report['launches_checked']} launches to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
