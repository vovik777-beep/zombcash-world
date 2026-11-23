import base64
import struct

import pytest

from soi.sources.pumpswap import (
    BUY_EVENT, SELL_EVENT, CREATE_POOL_EVENT, decode_pumpswap_event,
    observations_from_transaction, attach_pool_mints,
)
from soi.sources.pumpfun import DecodeError


def pk(byte):
    return bytes([byte]) * 32


def u64(n=1):
    return struct.pack('<Q', n)


def i64(n=1_800_000_000):
    return struct.pack('<q', n)


def u128(n=123):
    return n.to_bytes(16, 'little', signed=True)


def string(s):
    b = s.encode()
    return struct.pack('<I', len(b)) + b


def enc(discriminator, payload):
    return base64.b64encode(discriminator + payload).decode()


def pool_event():
    # Timestamp, index, creator, base/quote mint, decimals, reserves/liquidity,
    # bump, pool, LP mint, user ATAs, coin creator, mode and fee flags.
    payload = (i64() + struct.pack('<H', 0) + pk(1) + pk(2) + pk(3) +
        bytes([6, 9]) + b''.join(u64(i) for i in range(1, 8)) + bytes([7]) +
        pk(4) + pk(5) + pk(6) + pk(7) + pk(8) + b'\x00' + u64(250) + b'\x01\x00')
    return enc(CREATE_POOL_EVENT, payload)


def buy_event():
    # 13 u64s before pool/user/account pubkeys; then creator fee and flags.
    payload = i64() + b''.join(u64(i) for i in range(1, 14))
    payload += pk(4) + pk(9) + pk(10) + pk(11) + pk(12) + pk(13) + pk(14)
    payload += u64(250) + u64(25) + b'\x01' + u64(0) + u64(0) + u64(0) + i64()
    payload += u64(1) + string('buy') + u64(0) + u64(0) + u64(0) + u64(0)
    payload += u128(42) + b'\x01' + u64(1) + u64(0) + u64(0) + u64(0)
    return enc(BUY_EVENT, payload)


def sell_event():
    payload = i64() + b''.join(u64(i) for i in range(1, 14))
    payload += pk(4) + pk(9) + pk(10) + pk(11) + pk(12) + pk(13) + pk(14)
    payload += u64(250) + u64(25) + u64(0) + u64(0) + u64(0) + u64(0)
    payload += u128(42) + b'\x01' + u64(1) + u64(0) + u64(0) + u64(0)
    return enc(SELL_EVENT, payload)


def test_decode_pool_creation_fields():
    kind, event = decode_pumpswap_event(pool_event())
    assert kind == 'pool_created'
    assert event['base_mint']
    assert event['pool']
    assert event['creator_fee_bps'] == 250


def test_decode_buy_and_sell_events():
    buy_kind, buy = decode_pumpswap_event(buy_event())
    sell_kind, sell = decode_pumpswap_event(sell_event())
    assert buy_kind == 'trade_buy' and buy['user']
    assert buy['ix_name'] == 'buy'
    assert buy['virtual_quote_reserves'] == 42
    assert sell_kind == 'trade_sell' and sell['quote_amount_out'] == 7


def test_transaction_observations_and_pool_mint_link():
    logs = [f'Program data: {pool_event()}', f'Program data: {buy_event()}']
    tx = {'slot': 5, 'blockTime': 1_800_000_000,
          'meta': {'err': None, 'logMessages': logs}}
    observations = observations_from_transaction('sig', tx, '2026-11-17T10:00:00+00:00')
    assert [o.kind for o in observations] == ['pool_created', 'trade_buy']
    linked = attach_pool_mints(observations)
    assert linked[1].mint == linked[0].mint


def test_rejects_truncated_pumpswap_event():
    with pytest.raises(DecodeError):
        decode_pumpswap_event(base64.b64encode(BUY_EVENT + b'\x00').decode())


def test_ignores_unrecognized_event():
    assert decode_pumpswap_event(base64.b64encode(b'12345678x').decode()) is None



class _Store:
    def __init__(self):
        self.state = {}
        self.items = []

    def get_collector_state(self, key, default=None):
        return self.state.get(key, default)

    def set_collector_state(self, key, value):
        if value is None:
            self.state.pop(key, None)
        else:
            self.state[key] = str(value)

    def observations(self):
        return list(self.items)

    def add(self, item):
        if any(x.source_ref == item.source_ref and x.kind == item.kind for x in self.items):
            return False
        self.items.append(item)
        return True


class _Rpc:
    def __init__(self, pages, transactions=None):
        self.pages = pages
        self.transactions = transactions or {}
        self.calls = []

    def recent_signatures_for_address(self, address, limit=100, before=None):
        assert address == "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
        self.calls.append(before)
        return self.pages[before][:limit]

    def transaction(self, signature):
        return self.transactions.get(signature, {
            "meta": {"err": None, "logMessages": []},
            "blockTime": 1_800_000_000,
        })


def _sig(value):
    return {"signature": value, "err": None}


def test_pumpswap_bootstrap_paginates_and_persists_watermark():
    from soi.sources.pumpswap import scan_recent
    store = _Store()
    rpc = _Rpc({None: [_sig("p3"), _sig("p2")], "p2": [_sig("p1")]})
    first = scan_recent(rpc, store, limit=2, pause_seconds=0, bootstrap_max_pages=4)
    assert first["sweep_complete"] is False
    assert first["bootstrap_status"] == "in_progress"
    assert store.state["pumpswap_before"] == "p2"
    second = scan_recent(rpc, store, limit=2, pause_seconds=0, bootstrap_max_pages=4)
    assert second["sweep_complete"] is True
    assert second["bootstrap_status"] == "complete"
    assert store.state["pumpswap_watermark"] == "p3"
    assert "pumpswap_before" not in store.state


def test_pumpswap_missing_transaction_does_not_advance_cursor():
    from soi.sources.pumpswap import scan_recent
    store = _Store()
    store.state["pumpswap_watermark"] = "old"
    rpc = _Rpc({None: [_sig("new"), _sig("old")]}, {"new": None})
    result = scan_recent(rpc, store, limit=2, pause_seconds=0)
    assert result["failures"] == 1
    assert result["missing_transactions"] == 1
    assert result["sweep_complete"] is False
    assert store.state["pumpswap_watermark"] == "old"
    assert store.state["pumpswap_anchor"] == "new"



def test_pumpswap_scanner_resolves_trade_mint_from_previously_observed_pool():
    from soi.sources.pumpswap import scan_recent, observations_from_transaction
    store = _Store()
    pool_tx = {"meta": {"err": None, "logMessages": [f"Program data: {pool_event()}"]},
               "blockTime": 1_800_000_000}
    pool_observations = observations_from_transaction("poolSig", pool_tx)
    assert pool_observations[0].kind == "pool_created"
    store.add(pool_observations[0])
    trade_tx = {"meta": {"err": None, "logMessages": [f"Program data: {buy_event()}"]},
                "blockTime": 1_800_000_001}
    rpc = _Rpc({None: [_sig("tradeSig"), _sig("old")]}, {"tradeSig": trade_tx})
    store.state["pumpswap_watermark"] = "old"
    result = scan_recent(rpc, store, limit=2, pause_seconds=0)
    assert result["sweep_complete"] is True
    trade = next(o for o in store.items if o.kind == "trade_buy")
    assert trade.mint == pool_observations[0].mint



def test_pumpswap_trade_amounts_are_normalized_using_pool_decimals():
    from soi.sources.pumpswap import resolve_pool_metadata, normalize_trade_amounts
    pool = observations_from_transaction("pool", {
        "meta": {"err": None, "logMessages": [f"Program data: {pool_event()}"]},
        "blockTime": 1_800_000_000,
    })[0]
    trade = observations_from_transaction("buy", {
        "meta": {"err": None, "logMessages": [f"Program data: {buy_event()}"]},
        "blockTime": 1_800_000_001,
    })[0]
    metadata = resolve_pool_metadata([pool])[pool.payload["event"]["pool"]]
    normalize_trade_amounts(trade, metadata)
    event = trade.payload["event"]
    assert trade.mint == pool.mint
    assert event["quote_mint"] == pool.payload["event"]["quote_mint"]
    assert event["raw_quote_amount"] == 7
    assert event["quote_amount_ui"] == pytest.approx(7 / 10**9)
    assert event["raw_base_amount"] == 1
    assert event["base_amount_ui"] == pytest.approx(1 / 10**6)
    assert event["quote_amount_usd"] is None
    assert event["fee_quote_units"]["denomination"] == "quote_token_raw_units_unverified"


def test_pumpswap_program_attribution_ignores_nested_cpi_and_preserves_log_index():
    from soi.sources.pumpswap import PROGRAM_ID
    logs = [
        f"Program {PROGRAM_ID} invoke [1]",
        "Program 11111111111111111111111111111111 invoke [2]",
        f"Program data: {pool_event()}",
        "Program 11111111111111111111111111111111 success",
        f"Program data: {pool_event()}",
        f"Program data: {buy_event()}",
        f"Program {PROGRAM_ID} success",
    ]
    tx = {"slot": 91, "blockTime": 1_800_000_000,
          "meta": {"err": None, "logMessages": logs}}
    observations = observations_from_transaction("orderedSwap", tx)
    assert [item.kind for item in observations] == ["pool_created", "trade_buy"]
    assert [item.source_ref for item in observations] == [
        "orderedSwap:0", "orderedSwap:1"
    ]
    assert [item.payload["log_index"] for item in observations] == [4, 5]


def test_decodes_known_pumpswap_trade_fields_and_keeps_suffix_opaque():
    from soi.sources.pumpswap import scan_recent
    store = _Store()
    raw = base64.b64decode(buy_event()) + bytes.fromhex("0102030405060708")
    encoded = base64.b64encode(raw).decode()
    tx = {"slot": 42, "blockTime": 1_800_000_000,
          "meta": {"err": None, "logMessages": [f"Program data: {encoded}"]}}
    rpc = _Rpc({None: [_sig("tradeSig"), _sig("old")]}, {"tradeSig": tx})
    store.state["pumpswap_watermark"] = "old"
    result = scan_recent(rpc, store, limit=2, pause_seconds=0)
    assert result["sweep_complete"] is True
    assert result["failures"] == 0
    assert result["unparsed_events"] == 0
    assert result["opaque_suffix_events"] == 1
    item = next(x for x in store.items if x.kind == "trade_buy")
    assert item.confidence == "observed"
    assert item.payload["event"]["unparsed_trailing_bytes_length"] == 8


def test_pumpswap_scan_stops_after_first_rpc_rate_limit_without_advancing_cursor():
    from urllib.error import HTTPError
    from soi.sources.pumpswap import scan_recent

    class RateLimitedRpc(_Rpc):
        def __init__(self):
            super().__init__({None: [_sig("newest"), _sig("older"), _sig("old")]})
            self.transaction_calls = []
        def transaction(self, signature):
            self.transaction_calls.append(signature)
            if signature == "older":
                raise HTTPError("https://rpc.invalid", 429, "Too Many Requests", {}, None)
            return {"meta": {"err": None, "logMessages": []}, "blockTime": 1_800_000_000}

    store, rpc = _Store(), RateLimitedRpc()
    store.state["pumpswap_watermark"] = "old"
    result = scan_recent(rpc, store, limit=3, pause_seconds=0)
    assert result["rate_limited"] is True
    assert result["failures"] == 1
    assert rpc.transaction_calls == ["newest", "older"]
    assert store.state["pumpswap_watermark"] == "old"
    assert "pumpswap_before" not in store.state


def test_pumpswap_failed_signature_diagnostic_includes_unknown_program_hint():
    from soi.sources.pumpswap import scan_recent
    store = _Store()
    store.state["pumpswap_watermark"] = "old"
    unknown_program = "UnknownProgram111111111111111111111111111111"
    failed_tx = {
        "transaction": {
            "message": {
                "accountKeys": [unknown_program],
                "instructions": [{"programIdIndex": 0}],
            }
        },
        "meta": {
            "err": {"InstructionError": [0, {"Custom": 6001}]},
            "logMessages": [],
        },
    }
    rpc = _Rpc({None: [
        {"signature": "failed", "err": {"InstructionError": [0, {"Custom": 6001}]}},
        _sig("old"),
    ]}, {"failed": failed_tx})
    result = scan_recent(rpc, store, limit=2, pause_seconds=0)
    assert result["failed_signature_program_samples"] == [{
        "error_group": "InstructionError/Custom:6001",
        "program": "other_program",
        "program_hint": unknown_program[:8],
    }]
