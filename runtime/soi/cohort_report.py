"""Command-line exporter for daily SOI launch cohorts."""
from __future__ import annotations
import argparse
from soi.analytics.cohorts import analyze_cohorts
from soi.report import write_cohort_report
from soi.storage import ObservationStore


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export a daily Solana launch cohort report")
    parser.add_argument("--db", required=True, help="Path to SOI SQLite database")
    parser.add_argument("--date", required=True, help="UTC launch cohort date YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="Output JSON path")
    args = parser.parse_args(argv)
    store = ObservationStore(args.db)
    report = analyze_cohorts(store.observations(), args.date)
    write_cohort_report(args.out, report)
    print(f"Exported {report['token_count']} launches to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
