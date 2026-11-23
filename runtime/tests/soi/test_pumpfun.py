import base64
import struct

import pytest

from soi.sources.pumpfun import (
    CREATE_EVENT_DISCRIMINATOR, DecodeError, decode_create_event,
    extract_create_events, observations_from_transaction,
    classify_signature_error, failed_transaction_program_label,
    failed_transaction_program_diagnostic,
)


def _str(value):
    raw = value.encode()
    return struct.pack('<I', len(raw)) + raw


def _event_bytes():
    pk = bytes(range(32))
    return b''.join([
        CREATE_EVENT_DISCRIMINATOR,
        _str('Outbreak Coin'), _str('DEAD'), _str('https://example.test/meta.json'),
        pk, pk, pk, pk,
        struct.pack('<q', 1_800_000_000),
        struct.pack('<Q', 1), struct.pack('<Q', 2), struct.pack('<Q', 3),
        struct.pack('<Q', 4), pk,
        b'\x01', b'\x00', pk,
        struct.pack('<Q', 5), struct.pack('<Q', 600), b'\x01', b'\x02',
    ])


def test_decode_create_event_fields():
    event = decode_create_event(base64.b64encode(_event_bytes()).decode())
    assert event['name'] == 'Outbreak Coin'
    assert event['symbol'] == 'DEAD'
    assert event['timestamp'] == 1_800_000_000
    assert event['is_mayhem_mode'] is True
    assert event['is_cashback_enabled'] is False
    assert event['creator_fee_bps'] == 600
    assert event['depth'] == 2


def test_ignores_other_event_discriminator():
    assert decode_create_event(base64.b64encode(b'12345678payload').decode()) is None


def test_rejects_truncated_create_event():
    with pytest.raises(DecodeError):
        decode_create_event(base64.b64encode(CREATE_EVENT_DISCRIMINATOR + b'\x00').decode())


def test_extract_and_build_observation():
    encoded = base64.b64encode(_event_bytes()).decode()
    events = extract_create_events([f'Program data: {encoded}'])
    assert len(events) == 1
    tx = {'slot': 42, 'blockTime': 1_800_000_000,
          'meta': {'err': None, 'logMessages': [f'Program data: {encoded}']}}
    obs = observations_from_transaction('sig123', tx, '2026-11-17T10:00:00+00:00')
    assert len(obs) == 1
    assert obs[0].kind == 'token_created'
    assert obs[0].source_ref == 'sig123:0'
    assert obs[0].payload['slot'] == 42


def _b58decode(value):
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    number = 0
    for char in value:
        number = number * 58 + alphabet.index(char)
    raw = number.to_bytes((number.bit_length() + 7) // 8, 'big')
    return b'\x00' * (len(value) - len(value.lstrip('1'))) + raw


def _trade_event_bytes(*, is_buy=True, quote_mint=None):
    pk = bytes(range(32))
    quote = quote_mint if quote_mint is not None else bytes([9]) * 32
    return b''.join([
        bytes([189, 219, 127, 211, 78, 230, 97, 238]),
        pk, struct.pack('<Q', 2_500_000_000), struct.pack('<Q', 750),
        bytes([1 if is_buy else 0]), pk, struct.pack('<q', 1_800_000_030),
        *[struct.pack('<Q', n) for n in range(10, 14)],
        pk, struct.pack('<Q', 100), struct.pack('<Q', 25), pk,
        struct.pack('<Q', 50), struct.pack('<Q', 12), b'\x01',
        struct.pack('<Q', 1), struct.pack('<Q', 2), struct.pack('<Q', 3),
        struct.pack('<q', 1_800_000_031), _str('buy'), b'\x00',
        struct.pack('<Q', 0), struct.pack('<Q', 0), struct.pack('<Q', 0),
        struct.pack('<Q', 0), struct.pack('<I', 1), pk, struct.pack('<H', 2500),
        quote, struct.pack('<Q', 2_500_000_000), struct.pack('<Q', 20),
        struct.pack('<Q', 21), struct.pack('<Q', 0), struct.pack('<Q', 0),
        struct.pack('<Q', 0),
    ])


def test_decode_trade_event_and_side():
    from soi.sources.pumpfun import decode_trade_event
    event = decode_trade_event(base64.b64encode(_trade_event_bytes()).decode())
    assert event['is_buy'] is True
    assert event['sol_amount'] == 2_500_000_000
    assert event['ix_name'] == 'buy'
    assert len(event['shareholders']) == 1


def test_trade_observation_and_first_hour_summary():
    from soi.sources.pumpfun import trade_observations_from_transaction
    from soi.analytics.trades import summarize_first_hour_trades
    wsol = _b58decode('So11111111111111111111111111111111111111112')
    encoded = base64.b64encode(_trade_event_bytes(quote_mint=wsol)).decode()
    tx = {'slot': 77, 'blockTime': 1_800_000_030,
          'meta': {'err': None, 'logMessages': [f'Program data: {encoded}']}}
    obs = trade_observations_from_transaction('tradeSig', tx, '2026-11-17T10:00:00+00:00')
    assert len(obs) == 1 and obs[0].kind == 'trade_buy'
    summary = summarize_first_hour_trades(obs, '2027-01-15T08:00:00+00:00', mint=obs[0].mint)
    assert summary['buy_count'] == 1
    assert summary['sol_buy_volume'] == 2.5


def test_program_attribution_ignores_nested_cpi_data_and_preserves_log_index():
    from soi.sources.pumpfun import PROGRAM_ID, trade_observations_from_transaction
    create = base64.b64encode(_event_bytes()).decode()
    trade = base64.b64encode(_trade_event_bytes()).decode()
    logs = [
        f"Program {PROGRAM_ID} invoke [1]",
        "Program 11111111111111111111111111111111 invoke [2]",
        f"Program data: {create}",
        "Program 11111111111111111111111111111111 success",
        f"Program data: {create}",
        f"Program data: {trade}",
        f"Program {PROGRAM_ID} success",
    ]
    tx = {"slot": 88, "blockTime": 1_800_000_000,
          "meta": {"err": None, "logMessages": logs}}
    creates = observations_from_transaction("orderedSig", tx)
    trades = trade_observations_from_transaction("orderedSig", tx)
    assert len(creates) == 1
    assert len(trades) == 1
    assert creates[0].source_ref == "orderedSig:0"
    assert trades[0].source_ref == "orderedSig:0"
    assert creates[0].payload["log_index"] == 4
    assert trades[0].payload["log_index"] == 5


def test_decode_complete_pump_amm_migration_event():
    from soi.sources.pumpfun import COMPLETE_PUMP_AMM_MIGRATION_DISCRIMINATOR, decode_migration_event
    pk = bytes(range(32))
    payload = (COMPLETE_PUMP_AMM_MIGRATION_DISCRIMINATOR + pk + pk +
        struct.pack('<Q', 123) + struct.pack('<Q', 456) + struct.pack('<Q', 7) +
        pk + struct.pack('<q', 1_800_000_000) + pk + pk)
    event = decode_migration_event(base64.b64encode(payload).decode())
    assert event['mint_amount'] == 123
    assert event['sol_amount'] == 456
    assert event['pool_migration_fee'] == 7
    assert event['timestamp'] == 1_800_000_000

def test_decode_complete_event_is_distinct_from_migration():
    from soi.sources.pumpfun import COMPLETE_EVENT_DISCRIMINATOR, decode_complete_event
    pk = bytes(range(32))
    payload = (COMPLETE_EVENT_DISCRIMINATOR + pk + pk + pk +
               struct.pack('<q', 1_800_000_000) + pk)
    event = decode_complete_event(base64.b64encode(payload).decode())
    assert event['timestamp'] == 1_800_000_000
    assert event['mint'] == event['bonding_curve'] == event['user']
    assert event['quote_mint'] == event['user']


def test_completion_observation_is_emitted_separately():
    from soi.sources.pumpfun import COMPLETE_EVENT_DISCRIMINATOR, PROGRAM_ID, completion_observations_from_transaction
    pk = bytes(range(32))
    payload = (COMPLETE_EVENT_DISCRIMINATOR + pk + pk + pk +
               struct.pack('<q', 1_800_000_000) + pk)
    encoded = base64.b64encode(payload).decode()
    logs = [f'Program {PROGRAM_ID} invoke [1]', f'Program data: {encoded}',
            f'Program {PROGRAM_ID} success']
    tx = {'slot': 77, 'blockTime': 1_800_000_000,
          'meta': {'err': None, 'logMessages': logs}}
    result = completion_observations_from_transaction('completeSig', tx)
    assert len(result) == 1
    assert result[0].kind == 'bonding_curve_completed'
    assert result[0].payload['log_index'] == 1


def test_solana_rpc_transaction_requests_v1_support():
    from soi.sources.pumpfun import SolanaRpcClient
    client = SolanaRpcClient(url="https://rpc.invalid")
    captured = {}
    client.call = lambda method, params: captured.update(method=method, params=params) or {"meta": {}}
    client.transaction("signature")
    assert captured["method"] == "getTransaction"
    assert captured["params"][1]["maxSupportedTransactionVersion"] == 1


def test_decodes_known_pump_trade_fields_and_keeps_suffix_opaque():
    from soi.sources.pumpfun import trade_observations_from_transaction
    raw = _trade_event_bytes() + bytes.fromhex("0102030405060708")
    encoded = base64.b64encode(raw).decode()
    tx = {"slot": 99, "blockTime": 1_800_000_030,
          "meta": {"err": None, "logMessages": [f"Program data: {encoded}"]}}
    items = trade_observations_from_transaction("unknownTrade", tx)
    assert len(items) == 1
    assert items[0].kind == "trade_buy"
    assert items[0].confidence == "observed"
    assert items[0].payload["event"]["unparsed_trailing_bytes_length"] == 8


def test_pumpfun_scan_keeps_checkpoint_and_counts_opaque_trade_suffix():
    from soi.sources.pumpfun import scan_recent
    class Store:
        def __init__(self):
            self.state = {"pumpfun_watermark": "old"}
            self.items = []
        def get_collector_state(self, key, default=None):
            return self.state.get(key, default)
        def set_collector_state(self, key, value):
            if value is None:
                self.state.pop(key, None)
            else:
                self.state[key] = str(value)
        def add(self, item):
            self.items.append(item)
            return True
    class Rpc:
        def recent_signatures(self, limit=100, before=None):
            return [{"signature": "new", "err": None}, {"signature": "old", "err": None}]
        def transaction(self, signature):
            raw = _trade_event_bytes() + bytes.fromhex("0102030405060708")
            encoded = base64.b64encode(raw).decode()
            return {"slot": 99, "blockTime": 1_800_000_030,
                    "meta": {"err": None, "logMessages": [f"Program data: {encoded}"]}}
    store = Store()
    result = scan_recent(Rpc(), store, limit=2, pause_seconds=0)
    assert result["sweep_complete"] is True
    assert result["failures"] == 0
    assert result["unparsed_events"] == 0
    assert result["opaque_suffix_events"] == 1
    assert store.state["pumpfun_watermark"] == "new"
    assert store.items[0].kind == "trade_buy"
    assert store.items[0].confidence == "observed"


def test_pumpfun_scan_stops_after_first_rpc_rate_limit_without_advancing_cursor():
    from urllib.error import HTTPError
    from soi.sources.pumpfun import scan_recent

    class Store:
        def __init__(self):
            self.state = {"pumpfun_watermark": "old"}
            self.items = []
        def get_collector_state(self, key, default=None):
            return self.state.get(key, default)
        def set_collector_state(self, key, value):
            if value is None:
                self.state.pop(key, None)
            else:
                self.state[key] = str(value)
        def add(self, item):
            self.items.append(item)
            return True

    class Rpc:
        def __init__(self):
            self.calls = []
        def recent_signatures(self, limit=100, before=None):
            return [{"signature": "newest", "err": None},
                    {"signature": "older", "err": None},
                    {"signature": "old", "err": None}]
        def transaction(self, signature):
            self.calls.append(signature)
            if signature == "older":
                raise HTTPError("https://rpc.invalid", 429, "Too Many Requests", {}, None)
            return {"slot": 1, "blockTime": 1_800_000_000,
                    "meta": {"err": None, "logMessages": []}}

    store, rpc = Store(), Rpc()
    result = scan_recent(rpc, store, limit=3, pause_seconds=0)
    assert result["rate_limited"] is True
    assert result["failures"] == 1
    assert rpc.calls == ["newest", "older"]
    assert store.state["pumpfun_watermark"] == "old"
    assert "pumpfun_before" not in store.state



def test_signature_error_classifier_groups_only_safe_error_types():
    assert classify_signature_error(
        {"InstructionError": [7, {"Custom": 6001}]}
    ) == "InstructionError/Custom:6001"
    assert classify_signature_error(
        {"InstructionError": [2, "InvalidAccountData"]}
    ) == "InstructionError/InvalidAccountData"
    assert classify_signature_error({"BlockhashNotFound": None}) == "BlockhashNotFound"
    assert classify_signature_error("sensitive raw provider text") == "unknown_error_shape"



def test_failed_transaction_program_label_redacts_program_addresses():
    tx = {"meta": {"logMessages": [
        "Program TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA invoke [1]",
        "Program TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA failed: custom program error: 0x1",
        "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P failed: CPI failed",
    ]}}
    assert failed_transaction_program_label(tx) == "spl_token"
    assert "Tokenkeg" not in failed_transaction_program_label(tx)


def test_failed_transaction_program_label_handles_missing_logs():
    assert failed_transaction_program_label({"meta": {"logMessages": None}}) == "logs_unavailable"



def test_failed_transaction_program_label_identifies_lighthouse_assertion_program():
    tx = {
        "transaction": {
            "message": {
                "accountKeys": [
                    "L2TExMFKdjpN9kozasaurPirfHy9P8sbXoAN1qA3S95",
                ],
                "instructions": [{"programIdIndex": 0}],
            }
        },
        "meta": {
            "err": {"InstructionError": [0, {"Custom": 6001}]},
            "logMessages": [],
        },
    }
    assert failed_transaction_program_label(tx) == "lighthouse"



def test_failed_transaction_program_label_prefers_failed_top_level_instruction():
    tx = {
        "transaction": {
            "message": {
                "accountKeys": [
                    "11111111111111111111111111111111",
                    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
                ],
                "instructions": [{"programIdIndex": 0}, {"programIdIndex": 1}],
            }
        },
        "meta": {
            "err": {"InstructionError": [1, {"Custom": 6001}]},
            "logMessages": [
                "Program UnknownNestedCpi111111111111111111111111111 failed: custom program error",
                "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P failed: CPI failed",
            ],
        },
    }
    assert failed_transaction_program_label(tx) == "pumpfun"


def test_failed_transaction_program_label_resolves_v0_loaded_program_key():
    tx = {
        "transaction": {
            "message": {
                "accountKeys": ["11111111111111111111111111111111"],
                "instructions": [{"programIdIndex": 1}],
            }
        },
        "meta": {
            "err": {"InstructionError": [0, {"Custom": 1}]},
            "loadedAddresses": {
                "writable": [],
                "readonly": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"],
            },
            "logMessages": [],
        },
    }
    assert failed_transaction_program_label(tx) == "pumpswap"



def test_failed_transaction_program_diagnostic_exposes_only_short_unknown_hint():
    program_id = "UnknownProgram111111111111111111111111111111"
    tx = {
        "transaction": {
            "message": {
                "accountKeys": [program_id],
                "instructions": [{"programIdIndex": 0}],
            }
        },
        "meta": {"err": {"InstructionError": [0, {"Custom": 301}]}, "logMessages": []},
    }
    diagnostic = failed_transaction_program_diagnostic(tx)
    assert diagnostic == {"program": "other_program", "program_hint": program_id[:8]}
    assert program_id not in str(diagnostic)


def test_solana_rpc_client_uses_ordered_env_provider_list(monkeypatch):
    from soi.sources.pumpfun import SolanaRpcClient
    monkeypatch.setenv("SOI_RPC_URLS", "https://rpc-one.test, https://rpc-two.test,https://rpc-one.test")
    monkeypatch.delenv("SOI_RPC_URL", raising=False)
    client = SolanaRpcClient()
    assert client.urls == ["https://rpc-one.test", "https://rpc-two.test"]



def test_solana_rpc_client_adds_alchemy_after_existing_provider_list(monkeypatch):
    from soi.sources.pumpfun import SolanaRpcClient
    monkeypatch.setenv("SOI_RPC_URLS", "https://rpc-one.test,https://rpc-two.test")
    monkeypatch.setenv("SOI_ALCHEMY_RPC_URL", "https://solana-mainnet.g.alchemy.com/v2/test-key")
    monkeypatch.delenv("SOI_RPC_URL", raising=False)
    client = SolanaRpcClient()
    assert client.urls == [
        "https://rpc-one.test",
        "https://rpc-two.test",
        "https://solana-mainnet.g.alchemy.com/v2/test-key",
    ]

def test_solana_rpc_client_fails_over_without_leaking_provider_url(monkeypatch):
    import io
    import urllib.error
    import urllib.request
    from soi.sources.pumpfun import SolanaRpcClient

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"jsonrpc":"2.0","result":{"slot":123},"id":1}'

    calls = []
    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.URLError("offline")
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = SolanaRpcClient(url="https://first.example/key-secret")
    client.urls.append("https://second.example/key-secret")
    assert client.call("getSlot", []) == {"slot": 123}
    assert client.url == "https://second.example/key-secret"
    assert client.failover_count == 1
    assert calls == ["https://first.example/key-secret", "https://second.example/key-secret"]
