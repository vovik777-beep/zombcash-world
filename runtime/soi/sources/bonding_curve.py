"""Read-only current Pump.fun bonding-curve account snapshots."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any

from soi.models import Observation
from soi.sources.pumpfun import PROGRAM_ID, DecodeError, _Reader

BONDING_CURVE_ACCOUNT_DISCRIMINATOR = bytes([23, 183, 248, 55, 96, 216, 172, 96])


def decode_bonding_curve_account(encoded: str) -> dict[str, Any]:
    """Decode the official Pump IDL BondingCurve account Borsh layout."""
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise DecodeError("Invalid base64 BondingCurve account data") from exc
    if not raw.startswith(BONDING_CURVE_ACCOUNT_DISCRIMINATOR):
        raise DecodeError("Account discriminator is not Pump BondingCurve")
    r = _Reader(raw[len(BONDING_CURVE_ACCOUNT_DISCRIMINATOR):])
    event = {
        "virtual_token_reserves": r.integer(8),
        "virtual_quote_reserves": r.integer(8),
        "real_token_reserves": r.integer(8),
        "real_quote_reserves": r.integer(8),
        "token_total_supply": r.integer(8),
        "complete": r.boolean(),
        "creator": r.pubkey(),
        "is_mayhem_mode": r.boolean(),
        "is_cashback_coin": r.boolean(),
        "quote_mint": r.pubkey(),
        "creator_fee_bps": r.integer(8),
        "can_edit_creator_fee": r.boolean(),
        "is_holder_reward": r.boolean(),
        "creator_fee": r.integer(8),
        "protocol_fees": r.integer(8),
        "depth": r.integer(1),
        "initial_virtual_quote_reserves": r.integer(8),
        "post_complete_base_out": r.integer(8),
        "post_complete_quote_in": r.integer(8),
    }
    if r.offset != len(r.data):
        raise DecodeError("Unexpected trailing bytes in BondingCurve account")
    return event


def collect_bonding_curve_snapshot(rpc: Any, mint: str, address: str,
                                   observed_at: str | None = None) -> Observation:
    """Fetch one current account state; this is not a historical state reconstruction."""
    response = rpc.account_info(address)
    if not isinstance(response, dict):
        raise RuntimeError("RPC getAccountInfo returned no response")
    value = response.get("value")
    if not isinstance(value, dict):
        raise RuntimeError("BondingCurve account does not exist at confirmed commitment")
    if value.get("owner") != PROGRAM_ID:
        raise RuntimeError("BondingCurve account owner does not match Pump program")
    if value.get("executable") is True:
        raise RuntimeError("BondingCurve account unexpectedly marked executable")
    data = value.get("data")
    if not isinstance(data, list) or len(data) < 2 or data[1] != "base64":
        raise RuntimeError("RPC did not return base64 account data")
    event = decode_bonding_curve_account(data[0])
    observed = observed_at or datetime.now(timezone.utc).isoformat()
    slot = (response.get("context") or {}).get("slot")
    return Observation(
        source="pumpfun_rpc",
        source_ref=f"{address}:{observed}",
        observed_at=observed,
        event_at=observed,
        kind="bonding_curve_snapshot",
        mint=mint,
        payload={
            "account": address,
            "slot": slot,
            "owner_program": value.get("owner"),
            "lamports": value.get("lamports"),
            "space": value.get("space"),
            "event": event,
            "snapshot_scope": "current_confirmed_account_state",
            "historical_state": False,
        },
        confidence="observed",
    )
