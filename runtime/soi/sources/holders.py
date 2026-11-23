"""Current top-token-account snapshot using standard Solana JSON-RPC only."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from soi.models import Observation


def collect_holder_snapshot(rpc: Any, mint: str,
                            observed_at: str | None = None) -> Observation:
    """Capture the RPC's current top-20 token accounts; never claim full holders."""
    supply_response = rpc.token_supply(mint)
    largest_response = rpc.largest_token_accounts(mint)
    supply_value = (supply_response or {}).get("value") or {}
    largest_value = (largest_response or {}).get("value") or []
    supply_raw = supply_value.get("amount")
    decimals = supply_value.get("decimals")
    if supply_raw is None or decimals is None:
        raise RuntimeError("getTokenSupply did not return amount and decimals")

    addresses = [item.get("address") for item in largest_value if item.get("address")]
    account_rows = rpc.parsed_accounts(addresses) if addresses else []
    parsed_by_address = dict(zip(addresses, account_rows))
    owners = {}
    accounts = []
    unresolved = 0

    for item in largest_value:
        address = item.get("address")
        if not address:
            continue
        account = parsed_by_address.get(address) or {}
        parsed = (account.get("data") or {}).get("parsed") or {}
        info = parsed.get("info") or {}
        owner = info.get("owner")
        token_amount = item.get("amount")
        try:
            raw_amount = int(token_amount)
        except (TypeError, ValueError):
            unresolved += 1
            continue
        if not owner or info.get("mint") not in (None, mint):
            unresolved += 1
            owner = None
        accounts.append({
            "token_account": address,
            "owner": owner,
            "amount_raw": str(raw_amount),
            "decimals": item.get("decimals", decimals),
        })
        if owner:
            owners[owner] = owners.get(owner, 0) + raw_amount

    supply = int(supply_raw)
    owner_rows = [
        {"owner": owner, "amount_raw": str(amount),
         "share_of_total_supply": amount / supply if supply else None}
        for owner, amount in owners.items()
    ]
    owner_rows.sort(key=lambda row: (-int(row["amount_raw"]), row["owner"]))
    top_account_raw = sum(int(row["amount_raw"]) for row in accounts)
    top_owner_raw = sum(owners.values())
    observed = observed_at or datetime.now(timezone.utc).isoformat()
    payload = {
        "mint": mint,
        "observed_at": observed,
        "supply": {"amount_raw": str(supply), "decimals": decimals},
        "coverage": {
            "method": "getTokenLargestAccounts",
            "maximum_token_accounts_returned": 20,
            "token_accounts_returned": len(accounts),
            "resolved_owner_accounts": sum(1 for row in accounts if row["owner"]),
            "unresolved_accounts": unresolved,
            "unique_owners_in_top_accounts": len(owner_rows),
            "top_accounts_raw_amount": str(top_account_raw),
            "top_accounts_share_of_supply": top_account_raw / supply if supply else None,
            "resolved_top_owner_raw_amount": str(top_owner_raw),
            "resolved_top_owners_share_of_supply": top_owner_raw / supply if supply and owners else None,
            "full_holder_set": False,
            "historical_balances": False,
        },
        "top_token_accounts": accounts,
        "top_owners": owner_rows,
        "methodology": {
            "holder_concentration": "top-20 token accounts only; token accounts with the same parsed owner are aggregated",
            "not_a_full_holder_distribution": True,
            "not_a_historical_snapshot": True,
            "not_wallet_identity_resolution": True,
            "warning": "This is a current point-in-time partial snapshot. It excludes accounts outside the RPC top-20 and does not reconstruct historical balances.",
        },
    }
    return Observation(
        source="solana_rpc_holder_snapshot",
        source_ref=f"{mint}:{observed}",
        observed_at=observed,
        event_at=observed,
        kind="holder_snapshot",
        mint=mint,
        payload={"event": payload},
        confidence="observed",
    )
