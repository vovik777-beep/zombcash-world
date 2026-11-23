"""One-shot bounded SOI ingestion pipeline; each stage is independently reported."""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from soi.ingest_promotion import capture
from soi.snapshot_scheduler import SnapshotScheduler
from soi.analytics.snapshot_quality import build_snapshot_quality_report
from soi.analytics.wallets import analyze_early_wallets
from soi.analytics.funding_network import summarize_funding_network
from soi.analytics.temporal_chain import validate_funding_temporal_chain
from soi.analytics.event_integrity import audit_event_integrity
from soi.analytics.bonding_curve_progress import summarize_bonding_curve_progress
from soi.analytics.launch_cohorts import build_launch_cohorts
from soi.analytics.market_snapshot_audit import audit_market_snapshot_series
from soi.analytics.mint_supply_audit import audit_mint_supply
from soi.sources.funding import collect_wallet_funding
from soi.sources.funding_accounts import collect_funding_source_accounts
from soi.sources.funding_provenance import collect_funder_provenance
from soi.sources.holders import collect_holder_snapshot
from soi.sources.mint_state import collect_mint_supply_state
from soi.sources.bonding_curve import collect_bonding_curve_snapshot
from soi.sources.pumpfun import SolanaRpcClient, scan_recent
from soi.sources.pumpswap import scan_recent as scan_pumpswap
from soi.sources.dexscreener import DexScreenerClient
from soi.storage import ObservationStore
import json
import os
import time


def is_rate_limited_error(exc: Exception) -> bool:
    """Recognize HTTP/RPC 429 responses without depending on provider exception classes."""
    if getattr(exc, "code", None) == 429 or getattr(exc, "status", None) == 429:
        return True
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 429:
        return True
    message = str(exc).lower()
    return "http error 429" in message or "http 429" in message or "too many requests" in message


def collect_bonding_curve_stage(curve_rpc, store, selected_curves, pause_seconds=1.1,
                                  retry_seconds=300, now=None, sleep=None):
    """Collect current curve states with a persistent cooldown after public RPC 429s."""
    current_time = time.time() if now is None else now
    sleeper = time.sleep if sleep is None else sleep
    stage = {"requested": len(selected_curves), "saved": 0, "errors": 0,
             "deferred": 0, "snapshot_scope": "current_confirmed_account_state",
             "historical_state": False}
    retry_key = "bonding_curve_snapshot_global_retry_after"
    retry_after = float(store.get_collector_state(retry_key, "0") or 0)
    if current_time < retry_after:
        stage["deferred"] = len(selected_curves)
        stage["backoff_seconds_remaining"] = max(1, int(retry_after - current_time))
        return stage
    if retry_after:
        store.set_collector_state(retry_key, None)

    for index, (mint, address) in enumerate(selected_curves):
        try:
            snapshot = collect_bonding_curve_snapshot(curve_rpc, mint, address)
            stage["saved"] += int(store.add(snapshot))
        except Exception as exc:
            if is_rate_limited_error(exc):
                stage["errors"] += 1
                stage["rate_limited"] = True
                stage["deferred"] += len(selected_curves) - index - 1
                stage["backoff_seconds"] = retry_seconds
                store.set_collector_state(retry_key, current_time + retry_seconds)
            else:
                stage["errors"] += 1
            stage.setdefault("error_details", []).append(
                {"mint": mint, "error": str(exc)[:200]})
            if is_rate_limited_error(exc):
                break
        if pause_seconds > 0 and index < len(selected_curves) - 1:
            sleeper(pause_seconds)
    return stage


def has_collection_errors(stages: dict) -> bool:
    """Return true when any enabled collection stage reports partial or total failure."""
    for stage in stages.values():
        if not isinstance(stage, dict):
            continue
        if stage.get("error") or stage.get("errors"):
            return True
        if any(key.endswith(("_error", "_errors")) and value for key, value in stage.items()):
            return True
        if isinstance(stage.get("failures"), (int, float)) and stage["failures"] > 0:
            return True
        if isinstance(stage.get("unparsed_events"), (int, float)) and stage["unparsed_events"] > 0:
            return True
    return False


def main(argv=None):
    p = argparse.ArgumentParser(description="Run one bounded SOI collection cycle")
    p.add_argument("--db", required=True)
    p.add_argument("--rpc-limit", type=int, default=40)
    p.add_argument("--rpc-bootstrap-pages", type=int, default=10, help="Maximum initial Pump.fun history pages; reports truncation if reached")
    p.add_argument("--pumpswap-limit", type=int, default=40)
    p.add_argument("--pumpswap-bootstrap-pages", type=int, default=10, help="Maximum initial PumpSwap history pages")
    p.add_argument("--pool-state-limit", type=int, default=10, help="Current PumpSwap pool states to read; range 1..25")
    p.add_argument("--rpc-url", default=os.getenv("SOI_RPC_URL"), help="RPC URL; defaults to SOI_RPC_URL or public Solana RPC")
    p.add_argument("--rpc-pause", type=float, default=0.25)
    p.add_argument("--market-limit", type=int, default=25)
    p.add_argument("--holder-limit", type=int, default=10, help="Number of latest launches to snapshot; top-20 accounts only")
    p.add_argument("--mint-limit", type=int, default=10, help="Number of latest launch mint accounts to verify")
    p.add_argument("--curve-limit", type=int, default=10, help="Number of latest Pump bonding-curve accounts to snapshot")
    p.add_argument("--skip-holders", action="store_true")
    p.add_argument("--funding-wallet-limit", type=int, default=5)
    p.add_argument("--funding-signatures-per-wallet", type=int, default=10)
    p.add_argument("--funding-lookback-hours", type=int, default=24)
    p.add_argument("--funding-source-account-limit", type=int, default=20)
    p.add_argument("--funding-provenance-source-limit", type=int, default=10)
    p.add_argument("--funding-provenance-signatures", type=int, default=10)
    p.add_argument("--funding-provenance-lookback-hours", type=int, default=24)
    p.add_argument("--report", default="reports/soi-pipeline-report.json")
    p.add_argument("--skip-promotion", action="store_true")
    p.add_argument("--skip-market", action="store_true")
    a = p.parse_args(argv)
    if not 1 <= a.rpc_limit <= 100 or not 1 <= a.market_limit <= 50 or not 1 <= a.pumpswap_limit <= 100 or not 1 <= a.pool_state_limit <= 25 or not 1 <= a.holder_limit <= 25 or not 1 <= a.mint_limit <= 25 or not 1 <= a.curve_limit <= 25 or a.rpc_pause < .1 or a.rpc_bootstrap_pages < 1 or a.pumpswap_bootstrap_pages < 1 or not 1 <= a.funding_wallet_limit <= 25 or not 1 <= a.funding_signatures_per_wallet <= 25 or not 1 <= a.funding_lookback_hours <= 168 or not 1 <= a.funding_source_account_limit <= 100 or not 1 <= a.funding_provenance_source_limit <= 20 or not 1 <= a.funding_provenance_signatures <= 25 or not 1 <= a.funding_provenance_lookback_hours <= 168:
        p.error("Use rpc-limit/pumpswap-limit 1..100, bootstrap-pages >= 1, pool-state-limit 1..25, market-limit 1..50, holder-limit 1..25, curve-limit 1..25, funding-wallet-limit/signatures 1..25, funding-lookback-hours 1..168, funding-source-account-limit 1..100, funding-provenance-source-limit 1..20, funding-provenance-signatures 1..25, funding-provenance-lookback-hours 1..168, rpc-pause >= 0.1")
    store = ObservationStore(Path(a.db))
    result = {"stages": {}}
    try:
        result["stages"]["pumpfun_rpc"] = scan_recent(SolanaRpcClient(url=a.rpc_url) if a.rpc_url else SolanaRpcClient(), store, a.rpc_limit, a.rpc_pause, a.rpc_bootstrap_pages)
    except Exception as exc:
        result["stages"]["pumpfun_rpc"] = {"error": str(exc)[:300]}
    result["stages"]["snapshot_queue_seed"] = {"created": SnapshotScheduler(store).sync_launches()}
    try:
        result["stages"]["pumpswap_rpc"] = scan_pumpswap(SolanaRpcClient(url=a.rpc_url) if a.rpc_url else SolanaRpcClient(), store, a.pumpswap_limit, a.rpc_pause, a.pumpswap_bootstrap_pages)
    except Exception as exc:
        result["stages"]["pumpswap_rpc"] = {"error": str(exc)[:300]}
    try:
        from soi.sources.pumpswap_pool_state import collect_pool_states
        pool_state_rpc = SolanaRpcClient(url=a.rpc_url) if a.rpc_url else SolanaRpcClient()
        result["stages"]["pumpswap_pool_states"] = collect_pool_states(
            pool_state_rpc, store, limit=a.pool_state_limit, pause_seconds=a.rpc_pause)
    except Exception as exc:
        result["stages"]["pumpswap_pool_states"] = {"requested": 0, "saved": 0,
            "errors": 1, "error": str(exc)[:300]}
    if not a.skip_holders:
        holder_rpc = SolanaRpcClient(url=a.rpc_url) if a.rpc_url else SolanaRpcClient()
        launch_mints = list(dict.fromkeys(
            o.mint for o in store.observations(kind="token_created")
            if o.kind == "token_created" and o.mint
        ))
        # Rotate through every known launch by oldest holder snapshot first.
        # A mint in its per-mint cooldown must not occupy one of the bounded
        # RPC slots and starve older launches on every collection cycle.
        latest_holder_snapshot = {}
        for observation in store.observations(kind="holder_snapshot"):
            if not observation.mint:
                continue
            snapshot_at = observation.event_at or observation.observed_at or ""
            if snapshot_at > latest_holder_snapshot.get(observation.mint, ""):
                latest_holder_snapshot[observation.mint] = snapshot_at
        launch_order = {mint: index for index, mint in enumerate(launch_mints)}
        launch_mints.sort(key=lambda mint: (
            latest_holder_snapshot.get(mint, ""), launch_order[mint]
        ))
        holder_stage = {"requested": 0, "saved": 0, "errors": 0,
                        "deferred": 0, "cooldown_deferred": 0,
                        "rate_limited_deferred": 0,
                        "cooldown_seconds_remaining_max": 0,
                        "candidates_considered": 0,
                        "top_accounts_limit_per_mint": 20}
        holder_rpc_attempts = 0
        for mint in launch_mints:
            if holder_rpc_attempts >= a.holder_limit:
                break
            holder_stage["candidates_considered"] += 1
            retry_key = f"holder_snapshot_retry_after:{mint}"
            retry_after = float(store.get_collector_state(retry_key, "0") or 0)
            now = time.time()
            if now < retry_after:
                remaining = max(1, int(retry_after - now))
                holder_stage["deferred"] += 1
                holder_stage["cooldown_deferred"] += 1
                holder_stage["cooldown_seconds_remaining_max"] = max(
                    holder_stage["cooldown_seconds_remaining_max"], remaining
                )
                continue
            holder_rpc_attempts += 1
            holder_stage["requested"] += 1
            try:
                snapshot = collect_holder_snapshot(holder_rpc, mint)
                holder_stage["saved"] += int(store.add(snapshot))
                store.set_collector_state(retry_key, None)
            except Exception as exc:
                if is_rate_limited_error(exc):
                    # Public RPC 429s should not be retried every 60-second cycle.
                    # Defer only this mint; all other SOI stages continue normally.
                    store.set_collector_state(retry_key, time.time() + 900)
                    holder_stage["deferred"] += 1
                    holder_stage["rate_limited_deferred"] += 1
                else:
                    holder_stage["errors"] += 1
                    holder_stage.setdefault("error_details", []).append(
                        {"mint": mint, "error": str(exc)[:200]}
                    )
        result["stages"]["holder_snapshots"] = holder_stage
    try:
        mint_rpc = SolanaRpcClient(url=a.rpc_url) if a.rpc_url else SolanaRpcClient()
        mint_list = list(dict.fromkeys(
            observation.mint for observation in store.observations(kind="token_created")
            if observation.kind == "token_created" and observation.mint
        ))[-a.mint_limit:]
        mint_stage = {"requested": len(mint_list), "saved": 0, "errors": 0,
                      "supply_semantics": "total_mint_supply_only",
                      "circulating_supply_estimated": False}
        for mint in mint_list:
            try:
                state = collect_mint_supply_state(mint_rpc, mint)
                mint_stage["saved"] += int(store.add(state))
                if not state.payload["event"]["supply_matches_get_token_supply"]:
                    mint_stage.setdefault("supply_mismatches", []).append(mint)
            except Exception as exc:
                mint_stage["errors"] += 1
                mint_stage.setdefault("error_details", []).append(
                    {"mint": mint, "error": str(exc)[:200]}
                )
        result["stages"]["mint_supply_states"] = mint_stage
    except Exception as exc:
        result["stages"]["mint_supply_states"] = {"requested": 0, "saved": 0,
            "errors": 1, "error": str(exc)[:300]}
    try:
        curve_rpc = SolanaRpcClient(url=a.rpc_url) if a.rpc_url else SolanaRpcClient()
        launches_by_mint = {}
        for observation in store.observations(kind="token_created"):
            if observation.kind != "token_created" or not observation.mint:
                continue
            event = (observation.payload or {}).get("event", {})
            address = event.get("bonding_curve")
            if address:
                launches_by_mint[observation.mint] = address
        selected_curves = list(launches_by_mint.items())[-a.curve_limit:]
        curve_stage = collect_bonding_curve_stage(
            curve_rpc, store, selected_curves, pause_seconds=a.rpc_pause)
        result["stages"]["bonding_curve_snapshots"] = curve_stage
    except Exception as exc:
        result["stages"]["bonding_curve_snapshots"] = {"requested": 0, "saved": 0,
            "errors": 1, "error": str(exc)[:200]}
    if not a.skip_promotion:
        try:
            result["stages"]["promotion"] = capture(DexScreenerClient(), store)
        except Exception as exc:
            result["stages"]["promotion"] = {"error": str(exc)[:300]}
    if not a.skip_market:
        try:
            launches = [o.mint for o in store.observations(kind="token_created") if o.kind == "token_created" and o.mint]
            mints = list(dict.fromkeys(launches))[-a.market_limit:]
            from soi.sources.market_snapshots import MarketSnapshotCollector
            result["stages"]["market_snapshots"] = MarketSnapshotCollector(store).collect(mints)
        except Exception as exc:
            result["stages"]["market_snapshots"] = {"error": str(exc)[:300]}
    # Load the completed cycle's observation snapshot once for read-only analytics.
    # This avoids repeatedly materializing the entire SQLite history in RAM.
    all_observations = store.observations()
    try:
        event_integrity = audit_event_integrity(all_observations)
        result["event_integrity"] = event_integrity
        result["stages"]["event_integrity"] = {
            "status": event_integrity["status"],
            "observations_checked": event_integrity["observations_checked"],
            "findings": len(event_integrity["findings"]),
            "conflicting_launch_mints": event_integrity["conflicting_launch_mints"],
            "conflicting_pools": event_integrity["conflicting_pools"],
        }
    except Exception as exc:
        result["event_integrity"] = {"error": str(exc)[:300]}
        result["stages"]["event_integrity"] = {"error": str(exc)[:200]}
    try:
        progression_observations = all_observations
        progression_mints = list(dict.fromkeys(
            item.mint for item in progression_observations
            if item.kind in ("token_created", "bonding_curve_snapshot") and item.mint
        ))[-25:]
        result["bonding_curve_progress"] = [
            summarize_bonding_curve_progress(progression_observations, mint)
            for mint in progression_mints
        ]
        result["stages"]["bonding_curve_progress"] = {
            "mints_analyzed": len(result["bonding_curve_progress"]),
            "snapshots": sum(item["snapshot_count"] for item in result["bonding_curve_progress"]),
            "observed_false_to_true_transitions": sum(
                item["completion_progress"]["false_to_true_transition_observed"]
                for item in result["bonding_curve_progress"]),
            "historical_state_reconstructed": False,
        }
    except Exception as exc:
        result["bonding_curve_progress"] = {"error": str(exc)[:300]}
        result["stages"]["bonding_curve_progress"] = {"error": str(exc)[:200]}
    try:
        cohort_observations = all_observations
        result["launch_cohorts"] = build_launch_cohorts(cohort_observations, limit=100)
        result["stages"]["launch_cohorts"] = {
            "launches_considered": result["launch_cohorts"]["launches_considered"],
            "cohorts": result["launch_cohorts"]["cohort_count"],
            "timeline_errors": len(result["launch_cohorts"]["errors"]),
            "completion_requires_official_event": True,
            "migration_requires_confirmed_match": True,
            "market_cap_snapshot_count_first_hour": sum(
                row["market_cap_snapshot_count_first_hour"]
                for cohort in result["launch_cohorts"]["cohorts"]
                for row in cohort["launches"]),
            "observed_market_cap_milestones": {
                threshold: sum(
                    row["market_cap_milestones_first_hour"][threshold]["reached"]
                    for cohort in result["launch_cohorts"]["cohorts"]
                    for row in cohort["launches"])
                for threshold in ("100000", "500000", "1000000")
            },
        }
    except Exception as exc:
        result["launch_cohorts"] = {"error": str(exc)[:300]}
        result["stages"]["launch_cohorts"] = {"error": str(exc)[:200]}
    try:
        snapshot_audit = audit_market_snapshot_series(all_observations)
        result["market_snapshot_audit"] = snapshot_audit
        result["stages"]["market_snapshot_audit"] = {
            "mints": snapshot_audit["mints"],
            "snapshots": snapshot_audit["snapshot_count"],
            "invalid_timestamps": len(snapshot_audit["invalid_timestamps"]),
            "duplicate_groups": sum(row["duplicate_timestamp_pair_groups"] for row in snapshot_audit["items"]),
            "large_gaps": sum(len(row["large_gaps"]) for row in snapshot_audit["items"]),
            "pair_switches": sum(len(row["pair_switches"]) for row in snapshot_audit["items"]),
            "market_cap_jump_warnings": sum(len(row["market_cap_jump_warnings"]) for row in snapshot_audit["items"]),
            "same_pair_price_jump_warnings": sum(len(row["same_pair_price_jump_warnings"]) for row in snapshot_audit["items"]),
            "low_liquidity_warnings": sum(len(row["low_liquidity_warnings"]) for row in snapshot_audit["items"]),
            "reserve_matched_snapshots": snapshot_audit["reserve_matched_snapshots"],
            "reserve_account_state_matches": snapshot_audit["reserve_account_state_matches"],
            "reserve_price_warnings": snapshot_audit["reserve_price_warnings"],
            "invalid_reserve_observations": snapshot_audit["invalid_reserve_observations"],
            "ambiguous_pool_metadata": len(snapshot_audit["ambiguous_pool_metadata"]),
            "liquidity_valid_observations": sum(row["liquidity_valid_observations"] for row in snapshot_audit["items"]),
            "reserve_evidence": snapshot_audit["reserve_evidence"],
        }
    except Exception as exc:
        result["market_snapshot_audit"] = {"error": str(exc)[:300]}
        result["stages"]["market_snapshot_audit"] = {"error": str(exc)[:200]}
    result["queue"] = store.snapshot_task_summary()
    result["observations"] = store.count()
    result["first_hour_quality"] = build_snapshot_quality_report(store)
    try:
        launch_mints = list(dict.fromkeys(
            o.mint for o in all_observations
            if o.kind == "token_created" and o.mint
        ))[-25:]
        result["early_wallet_behavior"] = [
            analyze_early_wallets(all_observations, mint) for mint in launch_mints
        ]
    except Exception as exc:
        result["early_wallet_behavior"] = {"error": str(exc)[:300]}
    # Bounded direct-funding lookback for a small sample of early observed buyers.
    try:
        funding_rpc = SolanaRpcClient(url=a.rpc_url) if a.rpc_url else SolanaRpcClient()
        selected = {}
        for cohort in result.get("early_wallet_behavior", []) if isinstance(result.get("early_wallet_behavior"), list) else []:
            launch_at = datetime.fromisoformat(cohort["launch_at"].replace("Z", "+00:00"))
            for wallet_row in cohort.get("wallets", []):
                offset = wallet_row.get("first_buy_seconds")
                if offset is None:
                    continue
                first_buy_at = (launch_at + timedelta(seconds=offset)).isoformat()
                wallet = wallet_row["wallet"]
                first_buy_signature = wallet_row.get("first_buy_signature")
                if wallet not in selected or first_buy_at < selected[wallet]["at"]:
                    selected[wallet] = {"at": first_buy_at, "signature": first_buy_signature,
                        "anchor_conflict": wallet_row.get("first_buy_anchor_conflict", False)}
                elif first_buy_at == selected[wallet]["at"] and (
                    first_buy_signature != selected[wallet]["signature"] or wallet_row.get("first_buy_anchor_conflict", False)
                ):
                    selected[wallet]["anchor_conflict"] = True
        result["early_wallet_funding"] = []
        funding_errors = 0
        chosen_wallets = sorted(selected.items(), key=lambda item: (
            item[1]["at"], item[0]))[:a.funding_wallet_limit]
        for wallet, first_buy in chosen_wallets:
            first_buy_at = first_buy["at"]
            try:
                result["early_wallet_funding"].append(collect_wallet_funding(
                    funding_rpc, wallet, first_buy_at,
                    first_buy_signature=first_buy["signature"],
                    first_buy_anchor_conflict=first_buy.get("anchor_conflict", False),
                    signatures_limit=a.funding_signatures_per_wallet,
                    lookback_seconds=a.funding_lookback_hours * 3600))
            except Exception as exc:
                funding_errors += 1
                result["early_wallet_funding"].append({
                    "wallet": wallet, "first_observed_buy_at": first_buy_at,
                    "error": str(exc)[:200]})
        result["stages"]["early_wallet_funding"] = {
            "requested": len(chosen_wallets), "saved": len(chosen_wallets) - funding_errors,
            "errors": funding_errors,
            "coverage_status_counts": {
                status: sum(1 for item in result["early_wallet_funding"]
                    if isinstance(item.get("coverage"), dict) and item["coverage"].get("status") == status)
                for status in sorted({item["coverage"].get("status", "unknown")
                    for item in result["early_wallet_funding"] if isinstance(item.get("coverage"), dict)})
            },
            "coverage_complete": sum(1 for item in result["early_wallet_funding"]
                if isinstance(item.get("coverage"), dict) and item["coverage"].get("coverage_complete_within_lookback") is True),
        }
    except Exception as exc:
        result["early_wallet_funding"] = {"error": str(exc)[:300]}
        result["stages"]["early_wallet_funding"] = {"requested": 0, "saved": 0, "errors": 1, "error": str(exc)[:200]}
    funding_results = result.get("early_wallet_funding", [])
    try:
        from soi.analytics.creator_funding import analyze_creator_funding_links
        result["creator_funding_links"] = analyze_creator_funding_links(
            all_observations,
            funding_results if isinstance(funding_results, list) else [],
            result.get("early_wallet_behavior") if isinstance(result.get("early_wallet_behavior"), list) else [])
        result["stages"]["creator_funding_links"] = {
            "mints_analyzed": result["creator_funding_links"]["mints_analyzed"],
            "mints_with_direct_creator_funding_match": result["creator_funding_links"]["mints_with_direct_creator_funding_match"],
            "mints_with_direct_create_user_funding_match": result["creator_funding_links"]["mints_with_direct_create_user_funding_match"],
            "total_direct_creator_funding_transfers": result["creator_funding_links"]["total_direct_creator_funding_transfers"],
            "identity_or_common_control_inference": False,
            "mint_specific_buy_anchor_required": True,
        }
    except Exception as exc:
        result["creator_funding_links"] = {"error": str(exc)[:300]}
        result["stages"]["creator_funding_links"] = {"error": str(exc)[:200]}
    result["early_wallet_funding_network"] = summarize_funding_network(
        funding_results if isinstance(funding_results, list) else [])
    try:
        source_rpc = SolanaRpcClient(url=a.rpc_url) if a.rpc_url else SolanaRpcClient()
        source_accounts = collect_funding_source_accounts(source_rpc,
            funding_results if isinstance(funding_results, list) else [],
            limit=a.funding_source_account_limit)
        result["early_wallet_funding_source_accounts"] = source_accounts
        result["stages"]["funding_source_accounts"] = {
            "requested": source_accounts["requested"],
            "returned": source_accounts["returned"],
            "unique_sources_seen": source_accounts["unique_sources_seen"],
            "truncated": source_accounts["truncated"],
            "errors": source_accounts["errors"]}
    except Exception as exc:
        result["early_wallet_funding_source_accounts"] = {"error": str(exc)[:300]}
        result["stages"]["funding_source_accounts"] = {
            "requested": 0, "returned": 0, "errors": [{"error": str(exc)[:200]}]}
    try:
        provenance_rpc = SolanaRpcClient(url=a.rpc_url) if a.rpc_url else SolanaRpcClient()
        provenance = collect_funder_provenance(
            provenance_rpc, funding_results if isinstance(funding_results, list) else [],
            source_limit=a.funding_provenance_source_limit,
            signatures_per_source=a.funding_provenance_signatures,
            lookback_hours=a.funding_provenance_lookback_hours)
        result["early_wallet_funding_provenance"] = provenance
        result["early_wallet_funding_provenance_network"] = summarize_funding_network(
            provenance.get("funders", []))
        result["stages"]["funding_provenance"] = {
            "requested": provenance["requested"], "saved": provenance["saved"],
            "errors": provenance["errors"], "depth": provenance["depth"],
            "truncated": provenance["truncated"],
            "coverage_quality": provenance.get("quality", {}),
            "error_details": [
                {"error": item.get("error")}
                for item in provenance.get("funders", [])
                if isinstance(item, dict) and item.get("error")
            ]}
        result["funding_temporal_chain"] = validate_funding_temporal_chain(
            funding_results if isinstance(funding_results, list) else [], provenance)
        result["stages"]["funding_temporal_chain"] = {
            "status": result["funding_temporal_chain"]["status"],
            "violations": len(result["funding_temporal_chain"]["violations"]),
            "missing_or_invalid_timestamps": result["funding_temporal_chain"]["missing_or_invalid_timestamps"],
            "provenance_wallets_temporally_checked": result["funding_temporal_chain"]["provenance_wallets_temporally_checked"],
        }
    except Exception as exc:
        result["early_wallet_funding_provenance"] = {"error": str(exc)[:300]}
        result["stages"]["funding_provenance"] = {
            "requested": 0, "saved": 0, "errors": 1, "error": str(exc)[:200]}
    try:
        supply_audit = audit_mint_supply(all_observations)
        result["mint_supply_audit"] = supply_audit
        result["stages"]["mint_supply_audit"] = {
            "mints_checked": supply_audit["mints_checked"],
            "matched": supply_audit["matched"],
            "mismatches": supply_audit["mismatches"],
            "not_compared": supply_audit["not_compared"],
            "circulating_supply_estimated": False,
            "risk_signal_counts": supply_audit["risk_signal_counts"],
            "risk_signals_are_not_a_fraud_verdict": True,
        }
    except Exception as exc:
        result["mint_supply_audit"] = {"error": str(exc)[:300]}
        result["stages"]["mint_supply_audit"] = {"error": str(exc)[:200]}
    try:
        from soi.analytics.creator_holders import analyze_creator_holder_overlap
        result["creator_holder_analysis"] = analyze_creator_holder_overlap(all_observations)
        result["stages"]["creator_holder_analysis"] = {
            "mints_analyzed": result["creator_holder_analysis"]["mints_analyzed"],
            "creator_matches_in_top20": result["creator_holder_analysis"]["creator_matches_in_top20"],
            "creator_not_found_in_top20": result["creator_holder_analysis"]["creator_not_found_in_top20"],
            "ambiguous_launch_evidence": result["creator_holder_analysis"]["ambiguous_launch_evidence"],
            "identity_or_beneficial_ownership_inference": False,
            "full_holder_distribution": False,
        }
    except Exception as exc:
        result["creator_holder_analysis"] = {"error": str(exc)[:300]}
        result["stages"]["creator_holder_analysis"] = {"error": str(exc)[:200]}
    try:
        from soi.analytics.holder_trends import analyze_holder_trends
        result["holder_trends"] = analyze_holder_trends(all_observations)
        result["stages"]["holder_trends"] = {
            "mints_analyzed": result["holder_trends"]["mints_analyzed"],
            "snapshots_analyzed": result["holder_trends"]["snapshots_analyzed"],
            "creator_balance_changes_observed": result["holder_trends"]["creator_balance_changes_observed"],
            "absence_from_top20_means_zero_balance": False,
            "full_holder_distribution": False,
        }
    except Exception as exc:
        result["holder_trends"] = {"error": str(exc)[:300]}
        result["stages"]["holder_trends"] = {"error": str(exc)[:200]}
    try:
        from soi.analytics.token_profile import build_token_profiles
        result["token_profiles"] = build_token_profiles(
            all_observations,
            market_snapshot_audit=result.get("market_snapshot_audit"),
            mint_supply_audit=result.get("mint_supply_audit"),
            creator_holder_analysis=result.get("creator_holder_analysis"),
            holder_trends=result.get("holder_trends"),
            creator_funding_links=result.get("creator_funding_links"),
            early_wallet_behavior=result.get("early_wallet_behavior")
                if isinstance(result.get("early_wallet_behavior"), list) else [],
            early_wallet_funding=result.get("early_wallet_funding")
                if isinstance(result.get("early_wallet_funding"), list) else [],
            funding_temporal_chain=result.get("funding_temporal_chain"),
        )
        result["stages"]["token_profiles"] = {
            "mints_profiled": result["token_profiles"]["mints_profiled"],
            "profile_version": result["token_profiles"]["profile_version"],
            "composite_risk_score": False,
            "circulating_supply_estimated": False,
            "unknown_data_preserved": True,
        }
    except Exception as exc:
        result["token_profiles"] = {"error": str(exc)[:300]}
        result["stages"]["token_profiles"] = {"error": str(exc)[:200]}
    collection_failed = has_collection_errors(result["stages"])
    result["collection_status"] = "partial_or_failed" if collection_failed else "ok"
    report = Path(a.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"Pipeline report: {report}")
    return 2 if collection_failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
