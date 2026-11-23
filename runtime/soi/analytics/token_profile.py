"""Unified, evidence-preserving per-mint profile assembled from SOI observations.

This is a deterministic evidence index, not a fraud score or investment rating.
Missing sources remain unknown and each evidence group retains its observation time.
"""
from __future__ import annotations

from typing import Iterable


def _event(item):
    payload = item.payload if isinstance(getattr(item, "payload", None), dict) else {}
    value = payload.get("event")
    return value if isinstance(value, dict) else {}


def _latest(items):
    return max(items, key=lambda row: (row.observed_at or "", row.source_ref or "")) if items else None


def _rows_by_mint(report):
    if not isinstance(report, dict):
        return {}
    rows = report.get("rows") if isinstance(report.get("rows"), list) else report.get("items")
    if not isinstance(rows, list):
        return {}
    return {row.get("mint"): row for row in rows
            if isinstance(row, dict) and row.get("mint")}


def _attach(target, report, key):
    rows = _rows_by_mint(report)
    for mint, row in rows.items():
        target.setdefault(mint, {})[key] = row


def build_token_profiles(
    observations: Iterable[object],
    *,
    market_snapshot_audit: dict | None = None,
    mint_supply_audit: dict | None = None,
    creator_holder_analysis: dict | None = None,
    holder_trends: dict | None = None,
    creator_funding_links: dict | None = None,
    early_wallet_behavior: list | None = None,
    early_wallet_funding: list | None = None,
    funding_temporal_chain: dict | None = None,
) -> dict:
    """Build a per-mint evidence profile while preserving unknown and partial states."""
    items = list(observations)
    grouped: dict[str, list] = {}
    for item in items:
        mint = getattr(item, "mint", None)
        if mint:
            grouped.setdefault(mint, []).append(item)

    profiles = []
    for mint in sorted(grouped):
        rows = grouped[mint]
        by_kind: dict[str, list] = {}
        for item in rows:
            by_kind.setdefault(item.kind, []).append(item)

        launch_rows = by_kind.get("token_created", [])
        launch_signatures = {
            ((item.payload or {}).get("signature"), item.event_at,
             _event(item).get("creator"), _event(item).get("user"))
            for item in launch_rows
        }
        launch_ambiguous = len(launch_signatures) > 1
        launch = _latest(launch_rows)
        launch_event = _event(launch) if launch else {}
        market = _latest(by_kind.get("market_snapshot", []))
        market_event = _event(market) if market else {}
        pool = _latest(by_kind.get("pumpswap_pool_state", []))
        pool_event = _event(pool) if pool else {}
        mint_state = _latest(by_kind.get("mint_supply_state", []))
        mint_event = _event(mint_state) if mint_state else {}
        holder = _latest(by_kind.get("holder_snapshot", []))
        holder_event = _event(holder) if holder else {}
        curve = _latest(by_kind.get("bonding_curve_snapshot", []))
        curve_event = _event(curve) if curve else {}

        profile = {
            "mint_address": mint,
            "profile_version": 1,
            "evidence_coverage": {
                "launch": "ambiguous" if launch_ambiguous else ("observed" if launch else "unknown"),
                "market": "observed" if market else "unknown",
                "pumpswap_pool_state": "observed" if pool else "unknown",
                "mint_state": "observed" if mint_state else "unknown",
                "holder_snapshot": "partial_top20" if holder else "unknown",
                "bonding_curve_state": "observed" if curve else "unknown",
            },
            "launch": {
                "observed_at": launch.observed_at if launch else None,
                "event_at": launch.event_at if launch else None,
                "source": launch.source if launch else None,
                "source_ref": launch.source_ref if launch else None,
                "confidence": launch.confidence if launch else None,
                "name": launch_event.get("name"),
                "symbol": launch_event.get("symbol"),
                "creator": None if launch_ambiguous else launch_event.get("creator"),
                "user": None if launch_ambiguous else launch_event.get("user"),
                "evidence_status": "ambiguous" if launch_ambiguous else ("observed" if launch else "unknown"),
            },
            "market": {
                "observed_at": market.observed_at if market else None,
                "source": market.source if market else None,
                "source_ref": market.source_ref if market else None,
                "confidence": market.confidence if market else None,
                "pair_address": market_event.get("pair_address"),
                "price_usd": market_event.get("price_usd"),
                "market_cap_usd": market_event.get("market_cap_usd"),
                "fdv_usd": market_event.get("fdv_usd"),
                "liquidity_usd": market_event.get("liquidity_usd"),
                "market_cap_and_fdv_are_separate": True,
                "circulating_market_cap_inferred": False,
            },
            "pool_state": {
                "observed_at": pool.observed_at if pool else None,
                "source": pool.source if pool else None,
                "source_ref": pool.source_ref if pool else None,
                "confidence": pool.confidence if pool else None,
                "pool_address": pool_event.get("pool_address"),
                "base_mint": pool_event.get("base_mint"),
                "quote_mint": pool_event.get("quote_mint"),
                "base_reserves_raw": pool_event.get("base_reserves_raw"),
                "quote_reserves_raw": pool_event.get("quote_reserves_raw"),
                "pool_account_slot": pool_event.get("pool_account_slot"),
                "vault_accounts_slot": pool_event.get("vault_accounts_slot"),
                "scope": "point_in_time_account_state" if pool else "unknown",
            },
            "mint_state": {
                "observed_at": mint_state.observed_at if mint_state else None,
                "source_ref": mint_state.source_ref if mint_state else None,
                "confidence": mint_state.confidence if mint_state else None,
                "token_program": mint_event.get("token_program"),
                "decimals": mint_event.get("decimals"),
                "total_supply_raw": (str(mint_event["supply_raw"])
                    if mint_event.get("supply_raw") is not None else None),
                "mint_authority": mint_event.get("mint_authority"),
                "freeze_authority": mint_event.get("freeze_authority"),
                "risk_indicators": mint_event.get("risk_indicators"),
                "circulating_supply_raw": None,
                "circulating_supply_status": "not_estimated",
            },
            "holders": {
                "observed_at": holder.observed_at if holder else None,
                "source_ref": holder.source_ref if holder else None,
                "confidence": holder.confidence if holder else None,
                "top_owners": holder_event.get("top_owners"),
                "coverage": holder_event.get("coverage"),
                "supply": holder_event.get("supply"),
                "distribution_scope": "partial_top20_only" if holder else "unknown",
                "absence_from_top20_means_zero": False,
            },
            "bonding_curve": {
                "observed_at": curve.observed_at if curve else None,
                "source_ref": curve.source_ref if curve else None,
                "confidence": curve.confidence if curve else None,
                "event": curve_event if curve else None,
                "scope": "point_in_time_account_state" if curve else "unknown",
            },
            "observation_counts": {
                "total": len(rows),
                "by_kind": {kind: len(values) for kind, values in sorted(by_kind.items())},
            },
        }
        profiles.append(profile)

    supplemental = {}
    _attach(supplemental, market_snapshot_audit, "market_snapshot_audit")
    _attach(supplemental, mint_supply_audit, "mint_supply_audit")
    _attach(supplemental, creator_holder_analysis, "creator_holder_analysis")
    _attach(supplemental, holder_trends, "holder_trends")
    _attach(supplemental, creator_funding_links, "creator_funding_links")
    if isinstance(early_wallet_behavior, list):
        for row in early_wallet_behavior:
            if isinstance(row, dict) and row.get("mint"):
                supplemental.setdefault(row["mint"], {})["early_wallet_behavior"] = row
    for profile in profiles:
        extra = supplemental.get(profile["mint_address"], {})
        profile["analysis"] = extra
        profile["funding_coverage"] = {
            "early_wallet_behavior_available": any(
                isinstance(row, dict) and row.get("mint") == profile["mint_address"]
                for row in (early_wallet_behavior or [])
            ),
            "global_sampled_wallet_results_count": sum(
                1 for row in (early_wallet_funding or [])
                if isinstance(row, dict) and not row.get("error")
            ),
            "direct_funding_sample_scope": "global_bounded_sample_not_mint_specific",
            "temporal_chain_status": (
                funding_temporal_chain.get("status")
                if isinstance(funding_temporal_chain, dict) else None
            ),
            "temporal_chain_scope": "global_bounded_sample_not_mint_specific",
            "interpretation": "bounded sampled-wallet lookbacks; direct sender is not ultimate funder",
        }

    return {
        "profile_version": 1,
        "mints_profiled": len(profiles),
        "profiles": profiles,
        "methodology": {
            "source": "append-only SOI observations plus explicitly linked analytics outputs",
            "missing_data": "unknown, never coerced to false or zero",
            "market_cap_and_fdv": "separate source fields; FDV is never substituted for market cap",
            "circulating_supply_estimated": False,
            "holder_distribution": "top-20 partial snapshot only",
            "pool_state": "point-in-time observation, not historical reconstruction",
            "funding": "bounded sampled-wallet evidence, not a complete wallet graph",
            "fraud_or_identity_verdict": False,
            "composite_risk_score": False,
        },
    }
