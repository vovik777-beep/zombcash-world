"""Exact-address matches between launch creator and observed early-buyer funders."""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Iterable


def analyze_creator_funding_links(observations: Iterable[object],
                                  direct_funding: list[dict],
                                  early_wallet_behavior: list[dict] | None = None) -> dict:
    """Find observed direct SOL transfers from a CreateEvent address to sampled buyers.

    A funding row is usable for a mint only when its first-buy signature and
    timestamp exactly match that mint's first observed buy for the buyer.
    """
    items = list(observations)
    launch_evidence: dict[str, set[tuple]] = {}
    for item in items:
        if item.kind != "token_created" or not item.mint or not item.event_at:
            continue
        payload = item.payload if isinstance(item.payload, dict) else {}
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        launch_evidence.setdefault(item.mint, set()).add((
            payload.get("signature"), item.event_at,
            event.get("creator"), event.get("user"),
        ))

    results_by_wallet = {}
    for row in direct_funding if isinstance(direct_funding, list) else []:
        if isinstance(row, dict) and row.get("wallet"):
            results_by_wallet[row["wallet"]] = row
    cohorts = {
        row.get("mint"): row for row in (early_wallet_behavior or [])
        if isinstance(row, dict) and row.get("mint")
    }

    output = []
    for mint in sorted(launch_evidence):
        evidence = launch_evidence[mint]
        ambiguous = len(evidence) > 1
        launch = next(iter(evidence)) if len(evidence) == 1 else None
        creator = launch[2] if launch else None
        launch_user = launch[3] if launch else None
        matched, checked_buyers, incomplete_buyers, unaligned_buyers = [], 0, 0, 0
        cohort = cohorts.get(mint) or {}
        for buyer_row in cohort.get("wallets", []) if isinstance(cohort.get("wallets"), list) else []:
            wallet = buyer_row.get("wallet")
            funding = results_by_wallet.get(wallet)
            if not funding:
                continue
            coverage = funding.get("coverage") if isinstance(funding.get("coverage"), dict) else {}
            expected_signature = buyer_row.get("first_buy_signature")
            actual_signature = coverage.get("anchor_signature")
            expected_seconds = buyer_row.get("first_buy_seconds")
            launch_at = cohort.get("launch_at")
            aligned = bool(expected_signature and actual_signature == expected_signature)
            if aligned and expected_seconds is not None and launch_at:
                expected_at = (
                    datetime.fromisoformat(launch_at.replace("Z", "+00:00")) +
                    timedelta(seconds=float(expected_seconds))
                ).isoformat()
                aligned = expected_at == funding.get("first_observed_buy_at")
            if not aligned:
                unaligned_buyers += 1
                continue
            checked_buyers += 1
            if coverage.get("coverage_complete_within_lookback") is not True:
                incomplete_buyers += 1
            for transfer in funding.get("transfers", []) if isinstance(funding.get("transfers"), list) else []:
                if not isinstance(transfer, dict):
                    continue
                source = transfer.get("source_wallet")
                roles = []
                if creator and source == creator:
                    roles.append("launch_creator")
                if launch_user and source == launch_user:
                    roles.append("create_event_user")
                if roles:
                    matched.append({
                        "matched_roles": roles, "source_wallet": source,
                        "buyer_wallet": wallet, "sol": transfer.get("sol"),
                        "lamports": transfer.get("lamports"),
                        "block_time": transfer.get("block_time"),
                        "signature": transfer.get("signature"),
                        "instruction_index": transfer.get("instruction_index"),
                        "evidence": transfer.get("evidence"),
                    })

        matched.sort(key=lambda row: (
            str(row.get("block_time")), row["buyer_wallet"],
            str(row.get("signature")), str(row.get("instruction_index")),
        ))
        output.append({
            "mint": mint,
            "launch_evidence_status": "ambiguous" if ambiguous else "observed",
            "creator_address": creator,
            "create_event_user_address": launch_user,
            "direct_creator_funding_transfer_count": sum(
                "launch_creator" in row["matched_roles"] for row in matched),
            "direct_create_user_funding_transfer_count": sum(
                "create_event_user" in row["matched_roles"] for row in matched),
            "distinct_sampled_buyers_funded_by_creator": len({
                row["buyer_wallet"] for row in matched
                if "launch_creator" in row["matched_roles"]}),
            "distinct_sampled_buyers_funded_by_create_user": len({
                row["buyer_wallet"] for row in matched
                if "create_event_user" in row["matched_roles"]}),
            "mint_aligned_buyer_funding_anchors": checked_buyers,
            "funding_rows_not_aligned_to_this_mint_buy": unaligned_buyers,
            "incomplete_or_unknown_buyer_lookbacks": incomplete_buyers,
            "creator_funding_links": matched,
            "interpretation": {
                "match_basis": "exact source_wallet address equals Pump.fun CreateEvent creator or user address",
                "mint_anchor_alignment": "funding anchor signature and first-buy timestamp must exactly match this mint's early-wallet record",
                "only_observed_direct_native_sol_transfers": True,
                "absence_is_not_proof_of_no_creator_funding": True,
                "identity_or_common_control_inference": False,
                "coordination_or_intent_inference": False,
            },
        })

    return {
        "mints_analyzed": len(output),
        "mints_with_direct_creator_funding_match": sum(
            row["direct_creator_funding_transfer_count"] > 0 for row in output),
        "mints_with_direct_create_user_funding_match": sum(
            row["direct_create_user_funding_transfer_count"] > 0 for row in output),
        "total_direct_creator_funding_transfers": sum(
            row["direct_creator_funding_transfer_count"] for row in output),
        "rows": output,
        "methodology": {
            "creator_source": "creator field in observed Pump.fun CreateEvent",
            "funding_source": "bounded parsed System Program native-SOL transfers into sampled early buyers",
            "match": "exact address equality plus exact mint-specific first-buy anchor alignment",
            "historical_or_complete_funding_graph": False,
            "identity_or_common_control_inference": False,
        },
    }
