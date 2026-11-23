import base64
import struct

import pytest

from soi.sources.pumpfun import b58encode
from soi.sources.pumpswap import PROGRAM_ID
from soi.sources.pumpswap_pool_state import (
    POOL_DISCRIMINATOR, collect_pool_state, decode_pool_account,
)
from soi.models import Observation

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def pk(value):
    return bytes([value]) * 32


def u64(value):
    return struct.pack("<Q", value)


def pool_data():
    payload = (
        b"\x01" + struct.pack("<H", 7)
        + pk(1) + pk(2) + pk(3) + pk(4) + pk(5) + pk(6)
        + u64(900) + pk(7) + b"\x00\x00"
        + (123).to_bytes(16, "little", signed=True)
        + u64(250) + b"\x01\x00" + u64(12) + u64(13)
    )
    return POOL_DISCRIMINATOR + payload


def token_account(mint, amount, decimals):
    return {
        "owner": TOKEN_PROGRAM,
        "data": {"program": "spl-token", "parsed": {"type": "account", "info": {
            "mint": mint, "owner": b58encode(pk(8)), "state": "initialized",
            "tokenAmount": {"amount": str(amount), "decimals": decimals,
                            "uiAmountString": str(amount / 10**decimals)},
        }}},
    }


class FakeRpc:
    def __init__(self):
        self.last_context_slot = None

    def account_info(self, address):
        assert address == b58encode(pk(9))
        return {"context": {"slot": 100}, "value": {
            "owner": PROGRAM_ID,
            "data": [base64.b64encode(pool_data()).decode(), "base64"],
        }}

    def parsed_accounts(self, addresses):
        assert addresses == [b58encode(pk(5)), b58encode(pk(6))]
        self.last_context_slot = 101
        return [
            token_account(b58encode(pk(2)), 2_500_000, 6),
            token_account(b58encode(pk(3)), 9_000_000_000, 9),
        ]


def test_decode_pool_account_uses_official_discriminator_and_vault_fields():
    decoded = decode_pool_account({
        "owner": PROGRAM_ID,
        "data": [base64.b64encode(pool_data()).decode(), "base64"],
    })
    assert decoded["base_mint"] == b58encode(pk(2))
    assert decoded["quote_mint"] == b58encode(pk(3))
    assert decoded["pool_base_token_account"] == b58encode(pk(5))
    assert decoded["pool_quote_token_account"] == b58encode(pk(6))
    assert decoded["lp_supply_raw"] == 900


def test_decode_pool_account_preserves_unknown_appended_fields():
    extended = pool_data() + b"\xAA\xBB\xCC\xDD"
    decoded = decode_pool_account({
        "owner": PROGRAM_ID,
        "data": [base64.b64encode(extended).decode(), "base64"],
    })
    assert decoded["base_mint"] == b58encode(pk(2))
    assert decoded["pool_quote_token_account"] == b58encode(pk(6))
    assert decoded["unparsed_trailing_bytes_length"] == 4


def test_decode_pool_account_rejects_wrong_program_owner():
    with pytest.raises(ValueError, match="owner"):
        decode_pool_account({
            "owner": "11111111111111111111111111111111",
            "data": [base64.b64encode(pool_data()).decode(), "base64"],
        })


def test_collect_pool_state_reads_both_vaults_at_confirmed_slots():
    rpc = FakeRpc()
    observation = collect_pool_state(rpc, b58encode(pk(9)),
                                     "2026-11-17T20:00:00+00:00")
    event = observation.payload["event"]
    assert observation.kind == "pumpswap_pool_state"
    assert observation.mint == b58encode(pk(2))
    assert event["pool_account_slot"] == 100
    assert event["vault_accounts_slot"] == 101
    assert event["base_reserves_raw"] == 2_500_000
    assert event["quote_reserves_raw"] == 9_000_000_000
    assert event["base_decimals"] == 6
    assert event["quote_decimals"] == 9
    assert event["state_kind"] == "current_account_state"


def test_collect_pool_state_rejects_vault_mint_mismatch():
    class BadRpc(FakeRpc):
        def parsed_accounts(self, addresses):
            self.last_context_slot = 101
            return [
                token_account(b58encode(pk(22)), 2_500_000, 6),
                token_account(b58encode(pk(3)), 9_000_000_000, 9),
            ]
    with pytest.raises(ValueError, match="mint"):
        collect_pool_state(BadRpc(), b58encode(pk(9)))


def test_pool_state_observation_is_compatible_with_snapshot_audit():
    from soi.analytics.market_snapshot_audit import audit_market_snapshot_series

    state = collect_pool_state(FakeRpc(), b58encode(pk(9)),
                               "2026-11-17T10:04:00+00:00")
    snap = Observation("dexscreener", "snapshot", "2026-11-17T10:05:00+00:00",
        "2026-11-17T10:05:00+00:00", "market_snapshot", state.mint,
        {"event": {"pair_address": b58encode(pk(9)),
                   "base_token": b58encode(pk(2)), "quote_token": b58encode(pk(3)),
                   "market_cap_usd": 100_000, "price_usd": 0.02,
                   "price_native": 0.02, "liquidity_usd": 10_000}}, "observed")
    result = audit_market_snapshot_series([state, snap])
    item = result["items"][0]["snapshots"][0]
    assert item["reserve_evidence_status"] == "matched_account_state"
    assert item["reserve_evidence_source"] == "solana_rpc_pool_and_vault_account_state"
    assert item["pool_account_slot"] == 100


def test_pool_state_collector_discovers_pumpswap_pair_from_dexscreener_snapshot():
    from soi.sources.pumpswap_pool_state import collect_pool_states

    pool = b58encode(pk(9))
    pair = Observation("dexscreener", "pair-snapshot",
        "2026-11-17T10:00:00+00:00", "2026-11-17T10:00:00+00:00",
        "market_snapshot", b58encode(pk(2)),
        {"event": {"dex_id": "pumpswap", "pair_address": pool}}, "observed")

    class Store:
        def __init__(self):
            self.items = [pair]
        def observations(self):
            return list(self.items)
        def add(self, item):
            self.items.append(item)
            return True

    store = Store()
    result = collect_pool_states(FakeRpc(), store, limit=5, pause_seconds=0)
    assert result["requested"] == 1
    assert result["saved"] == 1
    assert result["errors"] == 0
    assert store.items[-1].kind == "pumpswap_pool_state"
