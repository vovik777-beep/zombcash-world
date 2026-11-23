"""Cross-wallet aggregation of directly observed SOL funding edges; no clustering claims."""
from __future__ import annotations


def summarize_funding_network(wallet_funding: list[dict]) -> dict:
    """Aggregate observed sender-to-recipient transfers across the sampled wallets."""
    by_source: dict[str, dict] = {}
    by_signature: dict[str, dict] = {}
    observed_transfers = 0
    for wallet_result in wallet_funding:
        if not isinstance(wallet_result, dict) or wallet_result.get("error"):
            continue
        for transfer in wallet_result.get("transfers", []):
            source = transfer.get("source_wallet")
            destination = transfer.get("destination_wallet")
            signature = transfer.get("signature")
            if not source or not destination:
                continue
            observed_transfers += 1
            row = by_source.setdefault(source, {
                "source_wallet": source, "destination_wallets": set(),
                "transfer_count": 0, "total_sol": 0.0, "signatures": set(),
            })
            row["destination_wallets"].add(destination)
            row["transfer_count"] += 1
            row["total_sol"] += float(transfer.get("sol") or 0)
            if signature:
                row["signatures"].add(signature)
                tx = by_signature.setdefault(signature, {
                    "signature": signature, "source_wallets": set(),
                    "destination_wallets": set(), "total_sol": 0.0,
                })
                tx["source_wallets"].add(source)
                tx["destination_wallets"].add(destination)
                tx["total_sol"] += float(transfer.get("sol") or 0)
    sources = []
    for row in by_source.values():
        destinations = sorted(row["destination_wallets"])
        sources.append({
            "source_wallet": row["source_wallet"],
            "distinct_sampled_destinations": len(destinations),
            "destination_wallets": destinations,
            "transfer_count": row["transfer_count"],
            "total_observed_sol": row["total_sol"],
            "signatures": sorted(row["signatures"]),
            "pattern": "multiple_sampled_destinations_observed" if len(destinations) > 1 else "single_sampled_destination_observed",
        })
    sources.sort(key=lambda row: (-row["distinct_sampled_destinations"], -row["total_observed_sol"], row["source_wallet"]))
    transactions = [{
        "signature": row["signature"],
        "source_wallets": sorted(row["source_wallets"]),
        "destination_wallets": sorted(row["destination_wallets"]),
        "distinct_destinations": len(row["destination_wallets"]),
        "total_observed_sol": row["total_sol"],
    } for row in by_signature.values()]
    transactions.sort(key=lambda row: (-row["distinct_destinations"], row["signature"]))
    return {
        "sampled_wallet_count": len(wallet_funding),
        "observed_direct_transfer_count": observed_transfers,
        "distinct_direct_source_count": len(sources),
        "sources": sources,
        "transactions_funding_multiple_sampled_wallets": [
            row for row in transactions if row["distinct_destinations"] > 1
        ],
        "interpretation": "Shared senders and multi-recipient transactions are observable funding patterns only; they do not prove common ownership, coordination, bot activity, or intent.",
        "coverage_warning": "This network is limited to the sampled wallets and bounded RPC lookbacks. It is not a complete wallet graph or an identity attribution system.",
    }
