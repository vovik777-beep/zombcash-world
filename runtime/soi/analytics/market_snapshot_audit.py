"""Audit DexScreener snapshots against observed PumpSwap reserve events."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
from typing import Iterable


def _utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _valid_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _valid_raw_reserve(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _valid_decimals(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 18:
        return None
    return value


def _pool_evidence(observations):
    metadata_rows = defaultdict(set)
    reserves = defaultdict(list)
    invalid_reserves = 0
    for item in observations:
        event = (item.payload or {}).get("event", {})
        if item.kind == "pool_created" and event.get("pool"):
            metadata_rows[str(event["pool"])].add((
                event.get("base_mint"), event.get("quote_mint"),
                _valid_decimals(event.get("base_mint_decimals")),
                _valid_decimals(event.get("quote_mint_decimals")),
            ))
        if item.kind == "pumpswap_pool_state" and event.get("pool_address"):
            metadata_rows[str(event["pool_address"])].add((
                event.get("base_mint"), event.get("quote_mint"),
                _valid_decimals(event.get("base_decimals")),
                _valid_decimals(event.get("quote_decimals")),
            ))
            base_raw = _valid_raw_reserve(event.get("base_reserves_raw"))
            quote_raw = _valid_raw_reserve(event.get("quote_reserves_raw"))
            try:
                observed = _utc(item.observed_at)
            except (TypeError, ValueError, OverflowError, AttributeError):
                observed = None
            if base_raw is None or quote_raw is None or observed is None:
                invalid_reserves += 1
            else:
                reserves[str(event["pool_address"])].append({
                    "observed_at": observed,
                    "base_reserves_raw": base_raw,
                    "quote_reserves_raw": quote_raw,
                    "source_ref": item.source_ref,
                    "evidence_source": "solana_rpc_pool_and_vault_account_state",
                    "pool_account_slot": event.get("pool_account_slot"),
                    "vault_accounts_slot": event.get("vault_accounts_slot"),
                })
        if item.source != "pumpswap_rpc" or item.kind not in ("trade_buy", "trade_sell"):
            continue
        pool = event.get("pool")
        if not pool:
            continue
        base_raw = _valid_raw_reserve(event.get("pool_base_token_reserves"))
        quote_raw = _valid_raw_reserve(event.get("pool_quote_token_reserves"))
        if base_raw is None or quote_raw is None:
            invalid_reserves += 1
            continue
        try:
            observed = _utc(item.event_at or item.observed_at)
        except (TypeError, ValueError, OverflowError, AttributeError):
            invalid_reserves += 1
            continue
        reserves[str(pool)].append({
            "observed_at": observed,
            "base_reserves_raw": base_raw,
            "quote_reserves_raw": quote_raw,
            "source_ref": item.source_ref,
            "evidence_source": "pumpswap_trade_event_reserve_fields",
            "pool_account_slot": None,
            "vault_accounts_slot": None,
        })
    metadata = {
        pool: next(iter(rows)) for pool, rows in metadata_rows.items() if len(rows) == 1
    }
    ambiguous_metadata = {pool for pool, rows in metadata_rows.items() if len(rows) > 1}
    for pool_rows in reserves.values():
        pool_rows.sort(key=lambda row: (row["observed_at"], row["evidence_source"] == "solana_rpc_pool_and_vault_account_state", row["source_ref"]))
    return metadata, ambiguous_metadata, reserves, invalid_reserves


def audit_market_snapshot_series(
    observations: Iterable[object],
    gap_warning_seconds: int = 900,
    jump_ratio_warning: float = 5.0,
    low_liquidity_ratio_warning: float = 0.01,
    reserve_max_age_seconds: int = 900,
    reserve_price_deviation_warning: float = 2.0,
) -> dict:
    """Describe snapshot quality and correlate snapshots with confirmed pool state.

    Direct confirmed Pool/vault account observations take precedence over
    event-reported reserves. Both require exact pair/pool and token-mint matches.
    """
    items = list(observations)
    pool_metadata, ambiguous_pools, pool_reserves, invalid_reserve_observations = _pool_evidence(items)
    grouped = defaultdict(list)
    invalid = []
    for item in items:
        if item.kind != "market_snapshot" or not item.mint:
            continue
        event = (item.payload or {}).get("event", {})
        try:
            observed = _utc(item.observed_at)
        except (TypeError, ValueError, OverflowError, AttributeError):
            invalid.append({"mint": item.mint, "source_ref": item.source_ref,
                            "reason": "invalid_observed_at"})
            continue
        pair = event.get("pair_address")
        grouped[item.mint].append({
            "observed_at": observed,
            "pair_address": str(pair) if pair else None,
            "base_token": event.get("base_token"),
            "quote_token": event.get("quote_token"),
            "market_cap_usd": _valid_number(event.get("market_cap_usd")),
            "fdv_usd": _valid_number(event.get("fdv_usd")),
            "price_usd": _valid_number(event.get("price_usd")),
            "price_native": _valid_number(event.get("price_native")),
            "liquidity_usd": _valid_number(event.get("liquidity_usd")),
            "source_ref": item.source_ref,
        })

    rows = []
    reserve_matched = reserve_price_warnings = 0
    for mint, entries in sorted(grouped.items()):
        entries.sort(key=lambda x: (x["observed_at"], x["pair_address"] or "", x["source_ref"]))
        duplicate_groups = defaultdict(list)
        liquidity_warnings, price_jumps, pair_switches, gaps, cap_jumps = [], [], [], [], []
        for entry in entries:
            duplicate_groups[(entry["observed_at"].isoformat(), entry["pair_address"])].append(entry)
            cap, liquidity = entry["market_cap_usd"], entry["liquidity_usd"]
            ratio = liquidity / cap if cap is not None and cap > 0 and liquidity is not None else None
            entry["liquidity_to_market_cap_ratio"] = ratio
            if cap is None:
                entry["valuation_evidence_status"] = "market_cap_missing_or_invalid"
            elif liquidity is None:
                entry["valuation_evidence_status"] = "liquidity_missing_or_invalid"
            elif cap == 0:
                entry["valuation_evidence_status"] = "market_cap_zero_ratio_unavailable"
            elif ratio is not None and ratio < low_liquidity_ratio_warning:
                entry["valuation_evidence_status"] = "low_liquidity_relative_to_market_cap"
                liquidity_warnings.append({
                    "observed_at": entry["observed_at"].isoformat(),
                    "pair_address": entry["pair_address"],
                    "market_cap_usd": cap, "liquidity_usd": liquidity,
                    "liquidity_to_market_cap_ratio": ratio,
                    "warning": "low_liquidity_relative_to_market_cap",
                })
            else:
                entry["valuation_evidence_status"] = "market_cap_and_liquidity_observed"

            entry["reserve_evidence_status"] = "pair_address_missing"
            pair = entry["pair_address"]
            if pair:
                if pair in ambiguous_pools:
                    entry["reserve_evidence_status"] = "pool_metadata_ambiguous"
                elif pair not in pool_metadata:
                    entry["reserve_evidence_status"] = "pool_metadata_not_observed"
                else:
                    base_mint, quote_mint, base_decimals, quote_decimals = pool_metadata[pair]
                    direct = (entry["base_token"] == base_mint and entry["quote_token"] == quote_mint)
                    reverse = (entry["base_token"] == quote_mint and entry["quote_token"] == base_mint)
                    if not direct and not reverse:
                        entry["reserve_evidence_status"] = "pair_token_orientation_mismatch"
                    elif base_decimals is None or quote_decimals is None:
                        entry["reserve_evidence_status"] = "pool_token_decimals_missing_or_invalid"
                    else:
                        eligible = [sample for sample in pool_reserves.get(pair, [])
                                    if sample["observed_at"] <= entry["observed_at"]]
                        sample = eligible[-1] if eligible else None
                        age = ((entry["observed_at"] - sample["observed_at"]).total_seconds()
                               if sample else None)
                        if sample is None or age > reserve_max_age_seconds:
                            entry["reserve_evidence_status"] = "reserve_event_not_observed_within_age"
                        else:
                            if direct:
                                base_raw, quote_raw = sample["base_reserves_raw"], sample["quote_reserves_raw"]
                                base_dec, quote_dec = base_decimals, quote_decimals
                            else:
                                base_raw, quote_raw = sample["quote_reserves_raw"], sample["base_reserves_raw"]
                                base_dec, quote_dec = quote_decimals, base_decimals
                            base_ui = base_raw / (10 ** base_dec)
                            quote_ui = quote_raw / (10 ** quote_dec)
                            reserve_price = quote_ui / base_ui if base_ui > 0 else None
                            dex_price = entry["price_native"]
                            deviation = (max(reserve_price, dex_price) / min(reserve_price, dex_price)
                                         if reserve_price is not None and dex_price is not None
                                         and reserve_price > 0 and dex_price > 0 else None)
                            entry.update({
                                "reserve_evidence_status": (
                                    "matched_account_state"
                                    if sample["evidence_source"] == "solana_rpc_pool_and_vault_account_state"
                                    else "matched_event_reserves"
                                ),
                                "reserve_evidence_source": sample["evidence_source"],
                                "pool_account_slot": sample["pool_account_slot"],
                                "vault_accounts_slot": sample["vault_accounts_slot"],
                                "reserve_observed_at": sample["observed_at"].isoformat(),
                                "reserve_age_seconds": age,
                                "reserve_source_ref": sample["source_ref"],
                                "pool_base_reserves_raw": sample["base_reserves_raw"],
                                "pool_quote_reserves_raw": sample["quote_reserves_raw"],
                                "pair_base_reserves_ui": base_ui,
                                "pair_quote_reserves_ui": quote_ui,
                                "reserve_implied_price_native": reserve_price,
                                "dexscreener_price_native": dex_price,
                                "reserve_price_deviation_ratio": deviation,
                            })
                            reserve_matched += 1
                            if deviation is not None and deviation >= reserve_price_deviation_warning:
                                entry["reserve_price_warning"] = "reserve_price_differs_from_dexscreener_price_native"
                                reserve_price_warnings += 1

        duplicates = [
            {"observed_at": key[0], "pair_address": key[1], "count": len(values),
             "source_refs": [v["source_ref"] for v in values]}
            for key, values in duplicate_groups.items() if len(values) > 1
        ]
        intervals = []
        for previous, current in zip(entries, entries[1:]):
            seconds = (current["observed_at"] - previous["observed_at"]).total_seconds()
            if seconds > 0:
                intervals.append(seconds)
                if seconds > gap_warning_seconds:
                    gaps.append({"from": previous["observed_at"].isoformat(),
                        "to": current["observed_at"].isoformat(), "gap_seconds": seconds,
                        "warning": "observation_gap_exceeds_threshold"})
            same_pair = bool(previous["pair_address"] and current["pair_address"]
                             and previous["pair_address"] == current["pair_address"])
            if previous["pair_address"] and current["pair_address"] and not same_pair:
                pair_switches.append({"from_pair": previous["pair_address"],
                    "to_pair": current["pair_address"], "at": current["observed_at"].isoformat(),
                    "warning": "primary_pair_changed"})
            old, new = previous["market_cap_usd"], current["market_cap_usd"]
            if old is not None and new is not None and old > 0 and new > 0 and seconds > 0:
                ratio = max(old, new) / min(old, new)
                if ratio >= jump_ratio_warning:
                    cap_jumps.append({"from_market_cap_usd": old, "to_market_cap_usd": new,
                        "ratio": ratio, "from": previous["observed_at"].isoformat(),
                        "to": current["observed_at"].isoformat(), "same_pair": same_pair,
                        "warning": "large_observed_market_cap_change_requires_review"})
            old_price, new_price = previous["price_usd"], current["price_usd"]
            if same_pair and old_price is not None and new_price is not None and old_price > 0 and new_price > 0 and seconds > 0:
                ratio = max(old_price, new_price) / min(old_price, new_price)
                if ratio >= jump_ratio_warning:
                    price_jumps.append({"from_price_usd": old_price, "to_price_usd": new_price,
                        "ratio": ratio, "from": previous["observed_at"].isoformat(),
                        "to": current["observed_at"].isoformat(), "pair_address": current["pair_address"],
                        "warning": "large_same_pair_price_change_requires_review"})
        rows.append({
            "mint": mint, "snapshot_count": len(entries),
            "first_observed_at": entries[0]["observed_at"].isoformat(),
            "last_observed_at": entries[-1]["observed_at"].isoformat(),
            "duplicate_timestamp_pair_groups": len(duplicates), "duplicates": duplicates,
            "median_interval_seconds": sorted(intervals)[len(intervals)//2] if intervals else None,
            "max_interval_seconds": max(intervals) if intervals else None,
            "large_gaps": gaps, "pair_switches": pair_switches,
            "market_cap_jump_warnings": cap_jumps,
            "same_pair_price_jump_warnings": price_jumps,
            "low_liquidity_warnings": liquidity_warnings,
            "market_cap_valid_observations": sum(e["market_cap_usd"] is not None for e in entries),
            "liquidity_valid_observations": sum(e["liquidity_usd"] is not None for e in entries),
            "reserve_matched_snapshots": sum(e["reserve_evidence_status"] in ("matched_event_reserves", "matched_account_state") for e in entries),
            "reserve_account_state_matches": sum(e["reserve_evidence_status"] == "matched_account_state" for e in entries),
            "reserve_price_warnings": sum(bool(e.get("reserve_price_warning")) for e in entries),
            "snapshots": [{k: v for k, v in e.items() if k != "observed_at"} |
                          {"observed_at": e["observed_at"].isoformat()} for e in entries],
        })
    return {
        "mints": len(rows), "snapshot_count": sum(row["snapshot_count"] for row in rows),
        "invalid_timestamps": invalid, "items": rows,
        "reserve_evidence": "Confirmed PumpSwap Pool and SPL vault account reads, with PumpSwap trade-event fallback; exact pair/pool and token-mint match",
        "reserve_matched_snapshots": reserve_matched,
        "reserve_account_state_matches": sum(row["reserve_account_state_matches"] for row in rows),
        "reserve_price_warnings": reserve_price_warnings,
        "invalid_reserve_observations": invalid_reserve_observations,
        "ambiguous_pool_metadata": sorted(ambiguous_pools),
        "thresholds": {"gap_warning_seconds": gap_warning_seconds,
            "market_cap_jump_ratio_warning": jump_ratio_warning,
            "same_pair_price_jump_ratio_warning": jump_ratio_warning,
            "low_liquidity_to_market_cap_ratio_warning": low_liquidity_ratio_warning,
            "reserve_max_age_seconds": reserve_max_age_seconds,
            "reserve_price_deviation_ratio_warning": reserve_price_deviation_warning},
        "interpretation": (
            "Coverage and valuation-evidence diagnostics only. Low liquidity relative to market cap "
            "signals weaker market depth, not a false market cap. Price jumps are compared only "
            "within the same observed pair. Direct reserve evidence comes from confirmed Pool and "
            "SPL vault account reads at recorded RPC slots; event reserve fields are a labelled fallback. "
            "Only exact pool and token matches are accepted, and evidence must precede the snapshot "
            "within the configured age. Current account state is not historical state. Pair changes "
            "can be legitimate. FDV is retained separately and never substituted for marketCap. "
            "No warning proves manipulation or bad data."
        ),
    }
