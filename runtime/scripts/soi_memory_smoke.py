"""Bounded synthetic, offline-only SOI memory smoke test for GitHub Actions.

Measures 1k, 5k and 10k synthetic observations with repeated SQLite reads.
This is a CI-runner comparison, not a FadeHost capacity estimate. No RPC,
HTTP, wallet, Telegram, or publishing operations are performed.
"""
from __future__ import annotations

import json
import resource
import tempfile
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from soi.models import Observation
from soi.storage import ObservationStore


def rss_peak_mib() -> float:
    # Linux reports ru_maxrss in KiB. This is a process high-water mark.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def make_observation(i: int, now: str) -> Observation:
    return Observation(
        source="synthetic_ci",
        source_ref=f"event-{i:06d}",
        observed_at=now,
        event_at=now,
        kind="token_created",
        mint=f"synthetic-mint-{i:06d}",
        payload={
            "event": {
                "name": f"Synthetic token {i}",
                "symbol": f"SYN{i % 100}",
                "metadata_uri": "https://example.invalid/metadata",
                "synthetic": True,
            },
            "ci_fixture": "not_chain_data",
            "padding": "x" * 512,
        },
        confidence="synthetic",
    )


def main() -> int:
    tracemalloc.start()
    milestones = (1_000, 5_000, 10_000, 25_000, 50_000)
    results = []
    rss_initial = rss_peak_mib()

    with tempfile.TemporaryDirectory(prefix="soi-memory-smoke-") as tmp:
        db_path = Path(tmp) / "synthetic.sqlite"
        store = ObservationStore(db_path)
        now = datetime.now(timezone.utc).isoformat()
        written = 0

        for target in milestones:
            tracemalloc.reset_peak()
            current_before, _ = tracemalloc.get_traced_memory()
            batch = [make_observation(i, now) for i in range(written, target)]
            saved = store.add_many(batch)
            written += saved

            loaded = []
            for _ in range(3):
                loaded = store.observations()

            current_after, peak_after = tracemalloc.get_traced_memory()
            assert written == target, (written, target)
            assert len(loaded) == target, (len(loaded), target)

            results.append({
                "observations": target,
                "written_in_stage": saved,
                "loaded_after_each_of_3_reads": len(loaded),
                "tracemalloc_current_stage_delta_mib": round(
                    (current_after - current_before) / 1048576, 2
                ),
                "tracemalloc_peak_stage_delta_mib": round(
                    (peak_after - current_before) / 1048576, 2
                ),
                "sqlite_db_mib": round(db_path.stat().st_size / 1048576, 2),
                # High-water RSS is cumulative across milestones, not a
                # per-stage allocation measurement.
                "process_rss_high_water_mib": round(rss_peak_mib(), 2),
            })
            del batch, loaded

    print(json.dumps({
        "measurement_scope": "GitHub Actions Linux runner only; not FadeHost sizing evidence",
        "network_calls": 0,
        "wallet_operations": 0,
        "publishing_operations": 0,
        "rss_initial_high_water_mib": round(rss_initial, 2),
        "rss_note": "ru_maxrss is a cumulative process high-water mark; do not sum or treat stage values as isolated deltas",
        "repeated_full_database_reads_per_milestone": 3,
        "milestones": results,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
