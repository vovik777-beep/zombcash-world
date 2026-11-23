"""Historical trends from observed partial holder snapshots; never infer absent balances."""
from __future__ import annotations

from typing import Iterable


def analyze_holder_trends(observations: Iterable[object]) -> dict:
    items = list(observations)
    launches: dict[str, set[tuple]] = {}
    snapshots: dict[str, list] = {}
    for item in items:
        payload = item.payload if isinstance(item.payload, dict) else {}
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        if item.kind == "token_created" and item.mint and item.event_at:
            launches.setdefault(item.mint, set()).add((
                payload.get("signature"), item.event_at,
                event.get("creator"), event.get("user")))
        elif item.kind == "holder_snapshot" and item.mint:
            snapshots.setdefault(item.mint, []).append(item)

    rows = []
    for mint in sorted(set(launches) | set(snapshots)):
        evidence = launches.get(mint, set())
        ambiguous = len(evidence) > 1
        creator = next(iter(evidence))[2] if len(evidence) == 1 else None
        ordered = sorted(snapshots.get(mint, []), key=lambda x: (x.observed_at, x.source_ref))
        history = []
        previous = None
        for snapshot in ordered:
            event = ((snapshot.payload or {}).get("event", {}) if isinstance(snapshot.payload, dict) else {})
            owners = [row for row in (event.get("top_owners") or [])
                      if isinstance(row, dict) and row.get("owner")]
            owner_map = {row["owner"]: row for row in owners}
            creator_row = owner_map.get(creator) if creator and not ambiguous else None
            supply_raw = (event.get("supply") or {}).get("amount_raw")
            supply = int(supply_raw) if isinstance(supply_raw, str) and supply_raw.isdigit() else None
            ranked = sorted(
                (row for row in owners if isinstance(row.get("amount_raw"), str)
                 and row["amount_raw"].isdigit()),
                key=lambda row: (-int(row["amount_raw"]), row["owner"]),
            )
            concentration = {}
            for count in (1, 5, 10, 20):
                total = sum(int(row["amount_raw"]) for row in ranked[:count])
                concentration[f"top{count}_known_owner_share_of_total_supply"] = (
                    total / supply if supply else None)
            creator_raw = creator_row.get("amount_raw") if creator_row else None
            creator_share = creator_row.get("share_of_total_supply") if creator_row else None
            if creator_share is None and creator_raw is not None and supply:
                creator_share = int(creator_raw) / supply
            delta = None
            delta_status = "no_previous_snapshot"
            if previous is not None:
                if creator_raw is not None and previous["creator_amount_raw"] is not None:
                    delta = int(creator_raw) - int(previous["creator_amount_raw"])
                    delta_status = "observed_top20_owner_balance_change"
                else:
                    delta_status = "creator_not_visible_in_both_top20_snapshots"
            row = {
                "observed_at": snapshot.observed_at,
                "creator_status": (
                    "ambiguous_launch" if ambiguous else
                    "unknown_creator_address" if not creator else
                    "matched_in_top20_owner_set" if creator_row else
                    "not_found_in_top20_owner_set"),
                "creator_amount_raw": creator_raw,
                "creator_share_of_total_supply": creator_share,
                "creator_balance_delta_raw_from_previous_snapshot": delta,
                "creator_balance_change_status": delta_status,
                "top_owner_concentration": concentration,
                "token_accounts_returned": (event.get("coverage") or {}).get("token_accounts_returned"),
                "resolved_owner_accounts": (event.get("coverage") or {}).get("resolved_owner_accounts"),
                "total_supply_raw": str(supply) if supply is not None else None,
            }
            history.append(row)
            previous = {"creator_amount_raw": creator_raw}
        known_deltas = [row["creator_balance_delta_raw_from_previous_snapshot"]
                        for row in history
                        if row["creator_balance_delta_raw_from_previous_snapshot"] is not None]
        rows.append({
            "mint": mint,
            "launch_evidence_status": "ambiguous" if ambiguous else (
                "observed" if evidence else "not_observed"),
            "creator_address": creator,
            "snapshot_count": len(history),
            "creator_visible_snapshot_count": sum(
                row["creator_status"] == "matched_in_top20_owner_set" for row in history),
            "observed_creator_balance_delta_count": len(known_deltas),
            "latest_observed_creator_balance_delta_raw": known_deltas[-1] if known_deltas else None,
            "history": history,
            "interpretation": {
                "absence_from_top20_means_zero_balance": False,
                "balance_delta_only_when_creator_visible_in_both_adjacent_snapshots": True,
                "historical_snapshots_are_full_holder_distribution": False,
                "supply_basis": "total mint supply",
                "identity_or_control_inference": False,
            },
        })
    return {
        "mints_analyzed": len(rows),
        "snapshots_analyzed": sum(row["snapshot_count"] for row in rows),
        "creator_balance_changes_observed": sum(
            row["observed_creator_balance_delta_count"] for row in rows),
        "rows": rows,
        "methodology": {
            "source": "append-only holder_snapshot observations and Pump.fun CreateEvent creator address",
            "comparison": "adjacent snapshots ordered by observed_at; exact creator-owner address match",
            "missing_creator_from_top20": "unknown/not visible, never treated as zero",
            "full_holder_distribution": False,
            "historical_balance_reconstruction": False,
        },
    }
