"""One-hop provenance of immediate SOL funders; bounded and read-only."""
from __future__ import annotations

from datetime import datetime, timezone
from soi.sources.funding import collect_wallet_funding


def collect_funder_provenance(rpc, funding_results: list[dict], *,
                              source_limit: int = 10,
                              signatures_per_source: int = 10,
                              lookback_hours: int = 24) -> dict:
    """Trace immediate funders one hop further back, anchored before their outgoing transfer."""
    if not 1 <= source_limit <= 20:
        raise ValueError("source_limit must be between 1 and 20")
    if not 1 <= signatures_per_source <= 25:
        raise ValueError("signatures_per_source must be between 1 and 25")
    if not 1 <= lookback_hours <= 168:
        raise ValueError("lookback_hours must be between 1 and 168")
    anchors = {}
    conflicting_anchors = set()
    for wallet_result in funding_results:
        if not isinstance(wallet_result, dict):
            continue
        for transfer in wallet_result.get("transfers", []):
            if not isinstance(transfer, dict):
                continue
            address, signature, block_time = (
                transfer.get("source_wallet"), transfer.get("signature"),
                transfer.get("block_time"))
            if not address or not signature or block_time is None:
                continue
            try:
                when = datetime.fromtimestamp(float(block_time), timezone.utc)
            except (TypeError, ValueError, OSError):
                continue
            current = anchors.get(address)
            if current is None or when < current["when"]:
                anchors[address] = {"when": when, "signature": signature}
                conflicting_anchors.discard(address)
            elif when == current["when"] and signature != current["signature"]:
                conflicting_anchors.add(address)
    for address in conflicting_anchors:
        anchors.pop(address, None)
    chosen = sorted(anchors.items(), key=lambda item: (item[1]["when"], item[0]))[:source_limit]
    results = []
    errors = 0
    for address, anchor in chosen:
        try:
            results.append(collect_wallet_funding(
                rpc, address, anchor["when"].isoformat(),
                first_buy_signature=anchor["signature"],
                signatures_limit=signatures_per_source,
                lookback_seconds=lookback_hours * 3600))
        except Exception as exc:
            errors += 1
            results.append({"wallet": address, "anchor_signature": anchor["signature"],
                            "error": str(exc)[:200]})
    coverage_counts = {}
    for item in results:
        coverage = item.get("coverage") if isinstance(item, dict) else None
        if isinstance(coverage, dict):
            status = coverage.get("status", "unknown")
        else:
            status = "collection_error" if item.get("error") else "unknown"
        coverage_counts[status] = coverage_counts.get(status, 0) + 1
    complete_count = sum(
        1 for item in results
        if isinstance(item.get("coverage"), dict)
        and item["coverage"].get("coverage_complete_within_lookback") is True
    )
    return {
        "depth": 1,
        "requested": len(chosen),
        "saved": len(results) - errors,
        "errors": errors,
        "unique_immediate_funders_seen": len(anchors) + len(conflicting_anchors),
        "conflicting_earliest_anchors": len(conflicting_anchors),
        "skipped_conflicting_anchor_addresses": sorted(conflicting_anchors),
        "truncated": len(anchors) > source_limit,
        "signatures_per_source": signatures_per_source,
        "lookback_hours": lookback_hours,
        "funders": results,
        "quality": {
            "complete_within_lookback": complete_count,
            "incomplete_or_unknown": len(results) - complete_count,
            "status_counts": coverage_counts,
            "interpretation": "Completeness applies only to the configured bounded window as represented by the RPC page, not global historical completeness.",
        },
        "interpretation": (
            "One additional observed transfer hop only. Each lookup is anchored before "
            "the sampled funder's outgoing transfer; no ultimate-origin, identity, "
            "common-control, or intent inference is made."
        ),
    }
