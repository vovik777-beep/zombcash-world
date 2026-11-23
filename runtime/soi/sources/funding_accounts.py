"""Bounded read-only account metadata for immediate funding senders."""
from __future__ import annotations

SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


def classify_account(address: str, account: dict | None) -> dict:
    """Describe RPC account ownership/type without inferring a real-world identity."""
    if account is None:
        return {"address": address, "account_status": "not_found_or_unavailable",
                "classification": "unknown", "owner_program": None}
    owner_program = account.get("owner")
    executable = bool(account.get("executable", False))
    data = account.get("data") or {}
    parsed = data.get("parsed") if isinstance(data, dict) else None
    parsed_type = parsed.get("type") if isinstance(parsed, dict) else None
    info = parsed.get("info") if isinstance(parsed, dict) else None
    if executable:
        classification = "executable_program_account"
    elif owner_program == SYSTEM_PROGRAM:
        classification = "system_owned_account"
    elif owner_program in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
        classification = "token_program_owned_account"
    elif owner_program:
        classification = "other_program_owned_account"
    else:
        classification = "unknown"
    result = {
        "address": address, "account_status": "present",
        "classification": classification, "owner_program": owner_program,
        "executable": executable, "lamports": account.get("lamports"),
        "space": account.get("space"), "parsed_account_type": parsed_type,
    }
    if classification == "token_program_owned_account" and isinstance(info, dict):
        result["token_mint"] = info.get("mint")
        result["token_authority"] = info.get("owner")
        result["token_state"] = info.get("state")
    result["interpretation"] = (
        "Program ownership and parsed account fields are technical metadata, not proof of a person's identity or control."
    )
    return result


def collect_funding_source_accounts(rpc, funding_results: list[dict],
                                    limit: int = 20) -> dict:
    """Inspect a deduplicated, bounded set of immediate funding sender accounts."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    addresses = []
    seen = set()
    for wallet_result in funding_results:
        if not isinstance(wallet_result, dict):
            continue
        for transfer in wallet_result.get("transfers", []):
            address = transfer.get("source_wallet") if isinstance(transfer, dict) else None
            if address and address not in seen:
                seen.add(address)
                addresses.append(address)
    selected = addresses[:limit]
    if not selected:
        return {"requested": 0, "returned": 0, "unique_sources_seen": len(addresses),
                "limit": limit, "truncated": False, "accounts": [], "errors": []}
    values = rpc.parsed_accounts(selected)
    errors = []
    if len(values) != len(selected):
        errors.append({"error": "RPC account result count did not match requested addresses"})
    accounts = [
        classify_account(address, values[index] if index < len(values) else None)
        for index, address in enumerate(selected)
    ]
    return {"requested": len(selected), "returned": len(values),
            "unique_sources_seen": len(addresses), "limit": limit,
            "truncated": len(addresses) > limit, "accounts": accounts,
            "errors": errors}
