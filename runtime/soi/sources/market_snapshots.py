"""Read-only DexScreener market snapshot collector.

Snapshots are observations, not a complete historical feed. Market cap is only
accepted from the API's marketCap field; fdv is retained separately.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Callable
import time

from soi.models import Observation
from soi.sources.dexscreener import DexScreenerClient


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _number(value):
    try:
        n = float(value)
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None


def choose_primary_pair(pairs: list[dict], mint: str) -> dict | None:
    """Choose the most liquid Solana pair containing mint; deterministic tie-break."""
    candidates = []
    for pair in pairs or []:
        if pair.get("chainId") != "solana":
            continue
        base = (pair.get("baseToken") or {}).get("address")
        quote = (pair.get("quoteToken") or {}).get("address")
        if mint not in (base, quote):
            continue
        liquidity = _number((pair.get("liquidity") or {}).get("usd")) or 0.0
        candidates.append((liquidity, str(pair.get("pairAddress") or ""), pair))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2]


def normalize_pair_snapshot(pair: dict, mint: str, observed_at: str | None = None,
                            schedule_minute: int | None = None, scheduled_for: str | None = None) -> Observation:
    observed_at = observed_at or _utc_now()
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    event = {
        "mint": mint,
        "pair_address": pair.get("pairAddress"),
        "dex_id": pair.get("dexId"),
        "base_token": base.get("address"),
        "quote_token": quote.get("address"),
        "price_usd": _number(pair.get("priceUsd")),
        "price_native": _number(pair.get("priceNative")),
        "market_cap_usd": _number(pair.get("marketCap")),
        "fdv_usd": _number(pair.get("fdv")),
        "liquidity_usd": _number((pair.get("liquidity") or {}).get("usd")),
        "volume": pair.get("volume") or {},
        "transactions": pair.get("txns") or {},
        "price_change": pair.get("priceChange") or {},
        "pair_created_at_ms": pair.get("pairCreatedAt"),
        "source_url": pair.get("url"),
    }
    if schedule_minute is not None:
        event["scheduled_offset_minute"] = int(schedule_minute)
        event["scheduled_for"] = scheduled_for
    pair_address = str(pair.get("pairAddress") or "unknown-pair")
    # A source observation is unique per pair and fetch timestamp; repeated
    # scans are intentionally retained as separate market snapshots.
    ref = f"{pair_address}:{observed_at}"
    return Observation("dexscreener", ref, observed_at, observed_at,
                       "market_snapshot", mint, {"event": event}, "observed")


class MarketSnapshotCollector:
    def __init__(self, store, client: DexScreenerClient | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 min_interval_seconds: float = 1.1):
        self.store = store
        self.client = client or DexScreenerClient()
        self.sleep = sleep
        self.min_interval_seconds = max(0.0, min_interval_seconds)

    def collect(self, mints: list[str], task_metadata: dict[str, dict] | None = None) -> dict:
        unique_mints = list(dict.fromkeys(mints))
        started_at = _utc_now()
        run_id = self.store.start_collection_run("dexscreener", started_at, len(unique_mints))
        results = {"run_id": run_id, "requested": len(unique_mints), "saved": 0,
                   "no_pair": [], "errors": []}
        for index, mint in enumerate(unique_mints):
            observed_at = _utc_now()
            try:
                pair = choose_primary_pair(self.client.token_pairs(mint), mint)
                if pair is None:
                    results["no_pair"].append(mint)
                    self.store.record_collection_result(run_id, mint, "no_pair", observed_at)
                else:
                    meta = (task_metadata or {}).get(mint, {})
                    obs = normalize_pair_snapshot(pair, mint, observed_at,
                        meta.get("offset_minute"), meta.get("due_at"))
                    inserted = self.store.add(obs)
                    results["saved"] += int(inserted)
                    self.store.record_collection_result(run_id, mint, "saved", observed_at,
                        None if inserted else "duplicate observation")
            except Exception as exc:
                detail = str(exc)[:300]
                results["errors"].append({"mint": mint, "error": detail})
                self.store.record_collection_result(run_id, mint, "error", observed_at, detail)
            if index < len(unique_mints) - 1 and self.min_interval_seconds:
                self.sleep(self.min_interval_seconds)
        results["completed_at"] = _utc_now()
        results["unique_requested"] = len(unique_mints)
        results["coverage"] = self.store.finish_collection_run(run_id, results["completed_at"])
        return results
