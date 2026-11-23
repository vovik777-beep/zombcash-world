import base64
import struct

import pytest

from soi.sources.mint_state import (MintDecodeError, _inspect_transfer_hook_program,
                                     collect_mint_supply_state, decode_mint_account)

TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


def _mint_bytes(supply=123456789, decimals=6, initialized=1,
                mint_authority_tag=0, freeze_authority_tag=0, extra=b""):
    data = bytearray(82)
    data[0:4] = mint_authority_tag.to_bytes(4, "little")
    data[4:36] = bytes(range(1, 33)) if mint_authority_tag else bytes(32)
    data[36:44] = supply.to_bytes(8, "little")
    data[44] = decimals
    data[45] = initialized
    data[46:50] = freeze_authority_tag.to_bytes(4, "little")
    data[50:82] = bytes(range(33, 65)) if freeze_authority_tag else bytes(32)
    return bytes(data) + extra


def _account(raw, owner=TOKEN):
    return {"owner": owner, "data": [base64.b64encode(raw).decode(), "base64"]}


def test_decode_mint_account_reads_supply_decimals_and_authority_flags():
    decoded = decode_mint_account(_account(_mint_bytes(mint_authority_tag=1, freeze_authority_tag=1)))
    assert decoded["supply_raw"] == 123456789
    assert decoded["decimals"] == 6
    assert decoded["initialized"] is True
    assert decoded["mint_authority"] is not None
    assert decoded["freeze_authority"] is not None


def test_decode_token_2022_detects_but_does_not_decode_extensions():
    hook_data = bytes(range(1, 65))
    extension_data = bytes(83) + bytes([1, 14, 0, 64, 0]) + hook_data
    decoded = decode_mint_account(_account(_mint_bytes(extra=extension_data), TOKEN_2022))
    assert decoded["token_2022_extensions_present"] is True
    assert decoded["account_data_length"] == 234
    hook = decoded["mint_extensions"][0]
    assert hook["type_id"] == 14
    assert hook["name"] == "TransferHook"
    assert hook["decoded"]["transfer_hook_program_id"] is not None
    assert decoded["risk_indicators"]["transfer_hook_present"] is True


def _token_2022_account(*extensions):
    tlv = b"".join(
        int(kind).to_bytes(2, "little") + len(payload).to_bytes(2, "little") + payload
        for kind, payload in extensions
    )
    return _account(_mint_bytes(extra=bytes(83) + bytes([1]) + tlv), TOKEN_2022)


def test_decode_transfer_fee_config_and_admin_controls():
    older = (1).to_bytes(8, "little") + (500).to_bytes(8, "little") + (100).to_bytes(2, "little")
    newer = (10).to_bytes(8, "little") + (900).to_bytes(8, "little") + (250).to_bytes(2, "little")
    fee = bytes([7]) * 32 + bytes([8]) * 32 + (42).to_bytes(8, "little") + older + newer
    decoded = decode_mint_account(_token_2022_account((1, fee)))
    config = decoded["extension_signals"]["TransferFeeConfig"]
    assert config["withheld_amount_raw"] == "42"
    assert config["older_transfer_fee"]["basis_points"] == 100
    assert config["newer_transfer_fee"]["basis_points"] == 250
    assert decoded["risk_indicators"]["newer_transfer_fee_basis_points"] == 250


def test_decode_permanent_delegate_pausable_and_frozen_default_state():
    delegate = bytes([9]) * 32
    pausable = bytes([10]) * 32 + bytes([1])
    decoded = decode_mint_account(_token_2022_account(
        (12, delegate), (26, pausable), (6, bytes([2]))
    ))
    assert decoded["risk_indicators"]["permanent_delegate_present"] is True
    assert decoded["risk_indicators"]["paused"] is True
    assert decoded["risk_indicators"]["default_account_state"] == "frozen"


def test_decode_confidential_non_transferable_and_scaled_ui_extensions():
    confidential = bytes([11]) * 32 + bytes([1]) + bytes([12]) * 32
    scaled = bytes([13]) * 32 + struct.pack("<dqd", 1.5, 1700000000, 2.0)
    interest = (bytes([14]) * 32 + (1600000000).to_bytes(8, "little", signed=True)
                + (125).to_bytes(2, "little", signed=True)
                + (1700000000).to_bytes(8, "little", signed=True)
                + (250).to_bytes(2, "little", signed=True))
    decoded = decode_mint_account(_token_2022_account(
        (4, confidential), (9, b""), (10, interest), (25, scaled)
    ))
    assert decoded["risk_indicators"]["confidential_transfer_enabled"] is True
    assert decoded["risk_indicators"]["non_transferable"] is True
    assert decoded["risk_indicators"]["scaled_ui_amount_present"] is True
    assert decoded["risk_indicators"]["interest_bearing_ui_amount"] is True
    interest_config = decoded["extension_signals"]["InterestBearingConfig"]
    assert interest_config["pre_update_average_rate_basis_points"] == 125
    assert interest_config["current_rate_basis_points"] == 250
    scaled_config = decoded["extension_signals"]["ScaledUiAmount"]
    assert scaled_config["multiplier"] == 1.5
    assert scaled_config["new_multiplier_effective_timestamp"] == 1700000000
    assert scaled_config["new_multiplier"] == 2.0


def test_decode_rejects_non_finite_scaled_ui_multiplier():
    scaled = bytes(32) + struct.pack("<dqd", float("nan"), 0, 1.0)
    with pytest.raises(MintDecodeError):
        decode_mint_account(_token_2022_account((25, scaled)))


def test_decode_rejects_invalid_transfer_fee_basis_points():
    fee = bytes(72) + bytes(16) + (10001).to_bytes(2, "little") + bytes(18)
    with pytest.raises(MintDecodeError):
        decode_mint_account(_token_2022_account((1, fee)))


def test_decode_rejects_malformed_token_2022_tlv():
    malformed = bytes(83) + bytes([1, 14, 0, 20, 0, 1])
    with pytest.raises(MintDecodeError):
        decode_mint_account(_account(_mint_bytes(extra=malformed), TOKEN_2022))


def test_decode_rejects_wrong_owner_and_short_data():
    with pytest.raises(MintDecodeError):
        decode_mint_account(_account(_mint_bytes(), "BadOwner"))
    with pytest.raises(MintDecodeError):
        decode_mint_account(_account(b"short"))


def test_collect_mint_state_compares_rpc_supply_but_never_calls_it_circulating():
    class Rpc:
        def account_info(self, mint):
            return {"context": {"slot": 77}, "value": _account(_mint_bytes())}

        def token_supply(self, mint):
            return {"context": {"slot": 78},
                    "value": {"amount": "123456789", "decimals": 6}}

    obs = collect_mint_supply_state(Rpc(), "MintA", "2026-11-17T12:00:00+00:00")
    event = obs.payload["event"]
    assert obs.kind == "mint_supply_state"
    assert event["supply_matches_get_token_supply"] is True
    assert event["mint_account_slot"] == 77
    assert event["token_supply_context_slot"] == 78
    assert event["supply_semantics"] == "total_mint_supply"
    assert event["circulating_supply_raw"] is None
    assert event["circulating_supply_status"] == "not_estimated"


def test_collect_mint_state_records_supply_mismatch_without_silent_reconciliation():
    class Rpc:
        def account_info(self, mint):
            return {"context": {"slot": 77}, "value": _account(_mint_bytes())}

        def token_supply(self, mint):
            return {"value": {"amount": "123456788", "decimals": 6}}

    obs = collect_mint_supply_state(Rpc(), "MintA", "2026-11-17T12:00:00+00:00")
    event = obs.payload["event"]
    assert event["supply_matches_get_token_supply"] is False
    assert event["supply_raw"] == 123456789
    assert event["get_token_supply_raw"] == 123456788


def test_collect_mint_state_rejects_uninitialized_mint():
    class Rpc:
        def account_info(self, mint):
            return {"context": {"slot": 77}, "value": _account(_mint_bytes(initialized=0))}

        def token_supply(self, mint):
            return {"value": {"amount": "123456789", "decimals": 6}}

    with pytest.raises(MintDecodeError):
        collect_mint_supply_state(Rpc(), "MintA")


def test_collect_mint_state_selects_epoch_active_transfer_fee():
    older = (1).to_bytes(8, "little") + (500).to_bytes(8, "little") + (100).to_bytes(2, "little")
    newer = (20).to_bytes(8, "little") + (900).to_bytes(8, "little") + (250).to_bytes(2, "little")
    fee = bytes(72) + older + newer
    account = _token_2022_account((1, fee))

    class Rpc:
        def account_info(self, mint):
            return {"context": {"slot": 77}, "value": account}

        def epoch_info(self):
            return {"epoch": 15}

        def token_supply(self, mint):
            return {"value": {"amount": "123456789", "decimals": 6}}

    event = collect_mint_supply_state(Rpc(), "MintA").payload["event"]
    assert event["current_epoch"] == 15
    assert event["active_transfer_fee"]["basis_points"] == 100
    assert event["active_transfer_fee"]["selection_basis"] == "getEpochInfo"


def test_transfer_hook_upgradeable_program_authority_is_inspected():
    import base64

    loader = "BPFLoaderUpgradeab1e11111111111111111111111"
    program_data = (2).to_bytes(4, "little") + bytes([4]) * 32
    programdata_data = (3).to_bytes(4, "little") + (1234).to_bytes(8, "little") + bytes([1]) + bytes([5]) * 32

    class Rpc:
        def account_info(self, address):
            if address == "HookProgram":
                return {"context": {"slot": 100}, "value": {
                    "owner": loader, "executable": True,
                    "data": [base64.b64encode(program_data).decode(), "base64"],
                }}
            if address != "HookProgram":
                return {"context": {"slot": 101}, "value": {
                    "owner": loader, "executable": False,
                    "data": [base64.b64encode(programdata_data).decode(), "base64"],
                }}
            raise AssertionError(address)

    state = _inspect_transfer_hook_program(Rpc(), "HookProgram")
    assert state["status"] == "account_found"
    assert state["executable"] is True
    assert state["upgradeability"] == "upgradeable"
    assert state["deployment_slot"] == 1234
    assert state["upgrade_authority_present"] is True
