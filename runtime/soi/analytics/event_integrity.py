"""Integrity checks for observed Pump.fun and PumpSwap event records.

These checks identify internal contradictions in the collected sample; they do
not claim complete chain coverage.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math


def _epoch(value):
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            number = float(value)
            return number if math.isfinite(number) else None
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            number = parsed.timestamp()
            return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return None


def audit_event_integrity(observations):
    """Summarize duplicate keys, launch conflicts, pool conflicts and time gaps."""
    items = list(observations)
    findings = []
    seen = set()
    duplicate_keys = 0
    launches = defaultdict(list)
    pools = defaultdict(list)
    tx_metadata = defaultdict(set)
    invalid_event_times = 0
    block_event_time_mismatches = 0

    for obs in items:
        key = (obs.source, obs.source_ref, obs.kind)
        if key in seen:
            duplicate_keys += 1
            findings.append({"type": "duplicate_observation_key",
                             "source": obs.source, "source_ref": obs.source_ref,
                             "kind": obs.kind})
        seen.add(key)
        payload = obs.payload if isinstance(obs.payload, dict) else {}
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        signature = payload.get("signature")
        if signature:
            tx_metadata[(obs.source, signature)].add(
                (payload.get("slot"), payload.get("block_time")))
        if obs.event_at and _epoch(obs.event_at) is None:
            invalid_event_times += 1
            findings.append({"type": "invalid_event_timestamp",
                             "source_ref": obs.source_ref, "kind": obs.kind})
        event_ts = _epoch(obs.event_at)
        block_ts = _epoch(payload.get("block_time"))
        if event_ts is not None and block_ts is not None and abs(event_ts - block_ts) > 120:
            block_event_time_mismatches += 1
            findings.append({"type": "event_block_time_mismatch",
                             "source_ref": obs.source_ref, "kind": obs.kind,
                             "difference_seconds": round(event_ts - block_ts, 3)})
        if obs.kind == "token_created" and obs.mint:
            launches[obs.mint].append({
                "event_at": obs.event_at, "signature": signature,
                "source_ref": obs.source_ref, "creator": event.get("creator"),
                "user": event.get("user"),
            })
        if obs.kind == "pool_created":
            pool = event.get("pool")
            if pool:
                pools[pool].append({
                    "mint": event.get("base_mint"), "quote_mint": event.get("quote_mint"),
                    "event_at": obs.event_at, "source_ref": obs.source_ref,
                })

    conflicting_launch_mints = 0
    multiple_launch_observations = 0
    for mint, rows in launches.items():
        signatures = {row["signature"] for row in rows if row["signature"]}
        times = {_epoch(row["event_at"]) for row in rows if _epoch(row["event_at"]) is not None}
        creators = {row["creator"] for row in rows if row["creator"]}
        users = {row["user"] for row in rows if row["user"]}
        if len(rows) > 1:
            multiple_launch_observations += 1
            if (len(signatures) > 1 or len(times) > 1
                    or len(creators) > 1 or len(users) > 1):
                conflicting_launch_mints += 1
                findings.append({"type": "multiple_launch_observations",
                                 "mint": mint, "observation_count": len(rows),
                                 "distinct_signatures": len(signatures),
                                 "distinct_event_times": len(times),
                                 "distinct_creators": len(creators),
                                 "distinct_users": len(users)})
    conflicting_pools = 0
    for pool, rows in pools.items():
        mappings = {(row["mint"], row["quote_mint"]) for row in rows}
        if len(mappings) > 1:
            conflicting_pools += 1
            findings.append({"type": "conflicting_pool_creation_metadata",
                             "pool": pool, "mappings": [list(x) for x in sorted(mappings, key=str)]})
    conflicting_transactions = 0
    for (source, signature), metadata in tx_metadata.items():
        if len(metadata) > 1:
            conflicting_transactions += 1
            findings.append({"type": "conflicting_transaction_metadata",
                             "source": source, "signature": signature})

    issue_count = len(findings)
    return {
        "status": "anomalies_or_gaps" if issue_count else "no_internal_anomalies_observed",
        "observations_checked": len(items),
        "duplicate_observation_keys": duplicate_keys,
        "mints_with_multiple_launch_observations": multiple_launch_observations,
        "conflicting_launch_mints": conflicting_launch_mints,
        "conflicting_pools": conflicting_pools,
        "conflicting_transaction_metadata": conflicting_transactions,
        "invalid_event_timestamps": invalid_event_times,
        "event_block_time_mismatches_over_120s": block_event_time_mismatches,
        "findings": findings,
        "interpretation": (
            "Checks only the observations currently stored. A clean result does not prove "
            "complete chain history; multiple launch records may reflect duplicate or "
            "conflicting source evidence and require review before selecting a canonical launch."
        ),
    }
