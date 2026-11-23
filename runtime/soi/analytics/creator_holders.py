"""Evidence-based comparison of launch creator addresses with current top holders."""
from __future__ import annotations

from typing import Iterable


def analyze_creator_holder_overlap(observations: Iterable[object]) -> dict:
    """Match Pump.fun create-event creator/user addresses to latest top-20 owners.

    A match means only that the exact event address equals a parsed token-account
    owner in the current partial holder snapshot. It is not proof of beneficial
    ownership, control, or identity.
    """
    items = list(observations)
    launches: dict[str, set[tuple]] = {}
    for item in items:
        if item.kind != "token_created" or not item.mint or not item.event_at:
            continue
        payload = item.payload if isinstance(item.payload, dict) else {}
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        launches.setdefault(item.mint, set()).add((
            payload.get("signature"),
            item.event_at,
            event.get("creator"),
            event.get("user"),
        ))

    latest_snapshots = {}
    for item in items:
        if item.kind != "holder_snapshot" or not item.mint:
            continue
        current = latest_snapshots.get(item.mint)
        if current is None or item.observed_at > current.observed_at:
            latest_snapshots[item.mint] = item

    rows = []
    for mint in sorted(set(launches) | set(latest_snapshots)):
        evidence = launches.get(mint, set())
        snapshot = latest_snapshots.get(mint)
        launch_conflict = len(evidence) > 1
        launch = next(iter(evidence)) if len(evidence) == 1 else None
        creator = launch[2] if launch else None
        user = launch[3] if launch else None
        event = ((snapshot.payload or {}).get("event", {}) if snapshot else {})
        owner_rows = event.get("top_owners") or []
        owner_by_address = {
            row.get("owner"): row for row in owner_rows
            if isinstance(row, dict) and row.get("owner")
        }
        coverage = event.get("coverage") or {}

        def match_role(address):
            if launch_conflict:
                status = "ambiguous_launch"
            elif not address:
                status = "unknown_address"
            elif snapshot is None:
                status = "no_holder_snapshot"
            elif address in owner_by_address:
                status = "matched_in_top20_owner_set"
            else:
                status = "not_found_in_top20_owner_set"
            matched = owner_by_address.get(address) if status == "matched_in_top20_owner_set" else None
            return {
                "address": address,
                "status": status,
                "amount_raw": matched.get("amount_raw") if matched else None,
                "share_of_total_supply": matched.get("share_of_total_supply") if matched else None,
            }

        supply = (event.get("supply") or {}).get("amount_raw")
        supply_int = int(supply) if isinstance(supply, str) and supply.isdigit() else None
        ranked = sorted(
            (row for row in owner_rows if isinstance(row, dict) and
             isinstance(row.get("amount_raw"), str) and row["amount_raw"].isdigit()),
            key=lambda row: (-int(row["amount_raw"]), row.get("owner") or ""),
        )
        concentration = {}
        for count in (1, 5, 10, 20):
            total = sum(int(row["amount_raw"]) for row in ranked[:count])
            concentration[f"top{count}_known_owner_share_of_total_supply"] = (
                total / supply_int if supply_int else None
            )
            concentration[f"top{count}_owner_count_observed"] = min(count, len(ranked))

        rows.append({
            "mint": mint,
            "launch_creator": match_role(creator),
            "launch_user": match_role(user),
            "creator_and_user_are_same_address": (
                creator == user if creator and user and not launch_conflict else None
            ),
            "launch_evidence_status": "ambiguous" if launch_conflict else (
                "observed" if launch else "not_observed"
            ),
            "launch_signature": launch[0] if launch else None,
            "launch_event_at": launch[1] if launch else None,
            "holder_snapshot_observed_at": snapshot.observed_at if snapshot else None,
            "holder_snapshot_coverage": coverage if snapshot else None,
            "top_owner_concentration": concentration if snapshot else None,
            "interpretation": {
                "creator_match_is_identity_or_control_proof": False,
                "holder_snapshot_is_full_distribution": False,
                "historical_creator_balance_reconstructed": False,
            },
        })

    return {
        "mints_analyzed": len(rows),
        "creator_matches_in_top20": sum(
            row["launch_creator"]["status"] == "matched_in_top20_owner_set"
            for row in rows
        ),
        "creator_not_found_in_top20": sum(
            row["launch_creator"]["status"] == "not_found_in_top20_owner_set"
            for row in rows
        ),
        "ambiguous_launch_evidence": sum(row["launch_evidence_status"] == "ambiguous" for row in rows),
        "rows": rows,
        "methodology": {
            "creator_source": "creator field in observed Pump.fun CreateEvent",
            "holder_source": "parsed owner field from getMultipleAccounts for getTokenLargestAccounts results",
            "comparison": "exact address equality only",
            "top_owner_concentration_basis": "ranked owners represented within the top-20 token-account response divided by observed total mint supply",
            "full_holder_distribution": False,
            "beneficial_owner_or_identity_inference": False,
            "historical_balances": False,
        },
    }
