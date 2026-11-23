from __future__ import annotations
import argparse, json
from pathlib import Path
from soi.analytics.snapshot_quality import build_snapshot_quality_report
from soi.storage import ObservationStore


def main(argv=None):
    parser=argparse.ArgumentParser(description='Export first-hour snapshot checkpoint quality')
    parser.add_argument('--db',required=True)
    parser.add_argument('--out',required=True)
    parser.add_argument('--mint')
    args=parser.parse_args(argv)
    report=build_snapshot_quality_report(ObservationStore(args.db),args.mint)
    path=Path(args.out); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2,sort_keys=True),encoding='utf-8')
    print(f"Exported first-hour quality for {report['tokens']} tokens to {path}")
    return 0

if __name__=='__main__':
    raise SystemExit(main())
