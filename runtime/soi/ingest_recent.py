"""Bounded read-only Pump.fun RPC ingestion and launch queue seeding."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
from soi.sources.pumpfun import SolanaRpcClient, scan_recent
from soi.snapshot_scheduler import SnapshotScheduler
from soi.storage import ObservationStore


def main(argv=None):
    p = argparse.ArgumentParser(description="Ingest a bounded page of recent Pump.fun transactions")
    p.add_argument("--db", required=True)
    p.add_argument("--rpc-url", default=None, help="Optional RPC URL; default is public Solana RPC")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--pause", type=float, default=0.25)
    p.add_argument("--seed-snapshot-queue", action="store_true")
    a = p.parse_args(argv)
    if not 1 <= a.limit <= 100:
        p.error("--limit must be between 1 and 100 for this low-volume command")
    if a.pause < 0.1:
        p.error("--pause must be at least 0.1 seconds")
    store = ObservationStore(Path(a.db))
    rpc = SolanaRpcClient(url=a.rpc_url) if a.rpc_url else SolanaRpcClient()
    result = scan_recent(rpc, store, limit=a.limit, pause_seconds=a.pause)
    if a.seed_snapshot_queue:
        result["snapshot_tasks_created"] = SnapshotScheduler(store).sync_launches()
    result["observation_count"] = store.count()
    print(result)
    return 0 if result["failures"] == 0 else 2

if __name__ == "__main__":
    raise SystemExit(main())
