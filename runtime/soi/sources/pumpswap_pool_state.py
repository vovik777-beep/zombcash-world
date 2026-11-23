"""Read-only current PumpSwap pool and SPL vault state via Solana RPC."""
from __future__ import annotations

import base64
from datetime import datetime, timezone

from soi.models import Observation
from soi.sources.pumpfun import DecodeError, _Reader
from soi.sources.pumpswap import PROGRAM_ID

POOL_DISCRIMINATOR = bytes([241, 154, 109, 4, 17, 177, 109, 188])
TOKEN_PROGRAM_IDS = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
}


def decode_pool_account(account):
    """Decode the official PumpSwap Pool account and return its vault addresses."""
    if not isinstance(account, dict):
        raise DecodeError("Pool account response is missing")
    if account.get("owner") != PROGRAM_ID:
        raise DecodeError("Pool account owner does not match PumpSwap program")
    data = account.get("data")
    if not isinstance(data, list) or len(data) < 2 or data[1] != "base64":
        raise DecodeError("Pool account data is not base64")
    try:
        raw = base64.b64decode(data[0], validate=True)
    except Exception as exc:
        raise DecodeError("Invalid base64 PumpSwap Pool account") from exc
    if not raw.startswith(POOL_DISCRIMINATOR):
        raise DecodeError("PumpSwap Pool discriminator mismatch")
    r = _Reader(raw[8:])
    pool = {
        "pool_bump": r.integer(1),
        "index": r.integer(2),
        "creator": r.pubkey(),
        "base_mint": r.pubkey(),
        "quote_mint": r.pubkey(),
        "lp_mint": r.pubkey(),
        "pool_base_token_account": r.pubkey(),
        "pool_quote_token_account": r.pubkey(),
        "lp_supply_raw": r.integer(8),
        "coin_creator": r.pubkey(),
        "is_mayhem_mode": r.boolean(),
        "is_cashback_coin": r.boolean(),
        "virtual_quote_reserves": r.integer(16, signed=True),
        "creator_fee_bps": r.integer(8),
        "can_edit_creator_fee": r.boolean(),
        "is_holder_reward": r.boolean(),
        "protocol_fees_raw": r.integer(8),
        "creator_fees_raw": r.integer(8),
    }
    # Pool accounts may include appended fields as the on-chain PumpSwap
    # account layout evolves. The known prefix above contains all fields this
    # collector relies on; retain only the suffix length rather than rejecting
    # an otherwise valid account or guessing the meaning of new fields.
    pool["unparsed_trailing_bytes_length"] = len(r.data) - r.offset
    return pool


def _vault_balance(account, expected_mint):
    if not isinstance(account, dict):
        raise DecodeError("Pool vault account is missing")
    if account.get("owner") not in TOKEN_PROGRAM_IDS:
        raise DecodeError("Pool vault is not owned by SPL Token or Token-2022")
    parsed = ((account.get("data") or {}).get("parsed") or {})
    if parsed.get("type") != "account":
        raise DecodeError("Pool vault is not a parsed token account")
    info = parsed.get("info") or {}
    if info.get("mint") != expected_mint:
        raise DecodeError("Pool vault mint does not match Pool account")
    if info.get("state") != "initialized":
        raise DecodeError("Pool vault token account is not initialized")
    token_amount = info.get("tokenAmount") or {}
    raw = token_amount.get("amount")
    decimals = token_amount.get("decimals")
    if not isinstance(raw, str) or not raw.isdigit():
        raise DecodeError("Pool vault raw token amount is invalid")
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 18:
        raise DecodeError("Pool vault token decimals are invalid")
    return {"mint": expected_mint, "token_program": account["owner"],
            "token_authority": info.get("owner"), "amount_raw": int(raw),
            "decimals": decimals, "amount_ui": token_amount.get("uiAmountString")}


def collect_pool_state(rpc, pool_address, observed_at=None):
    """Read Pool account plus both vault accounts at confirmed commitment."""
    observed = observed_at or datetime.now(timezone.utc).isoformat()
    response = rpc.account_info(pool_address)
    if not isinstance(response, dict):
        raise RuntimeError("getAccountInfo returned no context")
    pool_account = response.get("value")
    context = response.get("context") or {}
    pool_slot = context.get("slot")
    if not isinstance(pool_slot, int) or pool_slot < 0:
        raise DecodeError("Pool account response has no valid context slot")
    pool = decode_pool_account(pool_account)
    vault_addresses = [pool["pool_base_token_account"], pool["pool_quote_token_account"]]
    vault_response = rpc.parsed_accounts(vault_addresses)
    if not isinstance(vault_response, list) or len(vault_response) != 2:
        raise DecodeError("getMultipleAccounts returned an unexpected vault count")
    base_vault = _vault_balance(vault_response[0], pool["base_mint"])
    quote_vault = _vault_balance(vault_response[1], pool["quote_mint"])
    if base_vault["decimals"] < 0 or quote_vault["decimals"] < 0:
        raise DecodeError("Invalid vault decimals")
    token_slot = (rpc.last_context_slot if hasattr(rpc, "last_context_slot") else None)
    payload = {
        "pool_address": pool_address,
        "pool_program_owner": PROGRAM_ID,
        "pool_account_slot": pool_slot,
        "vault_accounts_slot": token_slot,
        "base_mint": pool["base_mint"],
        "quote_mint": pool["quote_mint"],
        "base_decimals": base_vault["decimals"],
        "quote_decimals": quote_vault["decimals"],
        "base_vault": base_vault,
        "quote_vault": quote_vault,
        "base_reserves_raw": base_vault["amount_raw"],
        "quote_reserves_raw": quote_vault["amount_raw"],
        "base_reserves_ui": base_vault["amount_ui"],
        "quote_reserves_ui": quote_vault["amount_ui"],
        "lp_supply_raw": pool["lp_supply_raw"],
        "pool_creator": pool["creator"],
        "coin_creator": pool["coin_creator"],
        "virtual_quote_reserves_raw": pool["virtual_quote_reserves"],
        "creator_fee_bps": pool["creator_fee_bps"],
        "protocol_fees_raw": pool["protocol_fees_raw"],
        "creator_fees_raw": pool["creator_fees_raw"],
        "commitment": "confirmed",
        "state_kind": "current_account_state",
    }
    return Observation(
        source="pumpswap_rpc",
        source_ref=f"{pool_address}:pool-state:{pool_slot}:{token_slot if token_slot is not None else 'unknown'}",
        observed_at=observed,
        event_at=observed,
        kind="pumpswap_pool_state",
        mint=pool["base_mint"],
        payload={"event": payload},
        confidence="observed",
    )


def collect_pool_states(rpc, store, limit=10, pause_seconds=0.1):
    """Refresh a bounded set of most recently observed PumpSwap pools."""
    if not 1 <= limit <= 25:
        raise ValueError("pool state limit must be between 1 and 25")
    if pause_seconds < 0:
        raise ValueError("pause_seconds must not be negative")
    metadata = {}
    for item in store.observations():
        event = (item.payload or {}).get("event", {})
        pool = None
        if item.source == "pumpswap_rpc" and item.kind == "pool_created":
            pool = event.get("pool")
        elif item.source == "dexscreener" and item.kind == "market_snapshot":
            if str(event.get("dex_id") or "").lower() == "pumpswap":
                pool = event.get("pair_address")
        if pool:
            metadata[str(pool)] = item
    selected = sorted(metadata.items(), key=lambda row: (
        row[1].event_at or row[1].observed_at, row[0]))[-limit:]
    result = {"requested": len(selected), "saved": 0, "errors": 0,
              "pool_addresses": [], "error_details": []}
    import time
    for index, (address, _) in enumerate(selected):
        try:
            observation = collect_pool_state(rpc, address)
            result["saved"] += int(store.add(observation))
            result["pool_addresses"].append(address)
        except Exception as exc:
            result["errors"] += 1
            result["error_details"].append({"pool": address, "error": str(exc)[:200]})
        if index + 1 < len(selected) and pause_seconds:
            time.sleep(pause_seconds)
    return result
