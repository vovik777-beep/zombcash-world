import base64
import struct

import pytest

from soi.sources.bonding_curve import (
    BONDING_CURVE_ACCOUNT_DISCRIMINATOR,
    collect_bonding_curve_snapshot,
    decode_bonding_curve_account,
)
from soi.sources.pumpfun import PROGRAM_ID, DecodeError


def _curve_bytes(*, complete=False):
    pk = bytes(range(32))
    return BONDING_CURVE_ACCOUNT_DISCRIMINATOR + b"".join([
        *[struct.pack("<Q", n) for n in (1000, 2000, 300, 400, 1_000_000)],
        bytes([int(complete)]), pk, b"\x00", b"\x01", pk,
        struct.pack("<Q", 250), b"\x01", b"\x00",
        struct.pack("<Q", 11), struct.pack("<Q", 12), b"\x02",
        struct.pack("<Q", 2000), struct.pack("<Q", 13), struct.pack("<Q", 14),
    ])


def test_decode_official_bonding_curve_account_layout():
    encoded = base64.b64encode(_curve_bytes(complete=True)).decode()
    event = decode_bonding_curve_account(encoded)
    assert event["virtual_token_reserves"] == 1000
    assert event["virtual_quote_reserves"] == 2000
    assert event["real_token_reserves"] == 300
    assert event["real_quote_reserves"] == 400
    assert event["token_total_supply"] == 1_000_000
    assert event["complete"] is True
    assert event["creator_fee_bps"] == 250
    assert event["depth"] == 2
    assert event["post_complete_quote_in"] == 14


def test_rejects_wrong_discriminator_and_trailing_bytes():
    with pytest.raises(DecodeError, match="discriminator"):
        decode_bonding_curve_account(base64.b64encode(b"12345678").decode())
    with pytest.raises(DecodeError, match="trailing"):
        decode_bonding_curve_account(base64.b64encode(_curve_bytes() + b"x").decode())


class FakeRpc:
    def __init__(self, owner=PROGRAM_ID, data=None):
        self.owner = owner
        self.data = data or base64.b64encode(_curve_bytes()).decode()

    def account_info(self, address):
        return {"context": {"slot": 12345}, "value": {
            "owner": self.owner, "executable": False, "lamports": 999,
            "space": len(base64.b64decode(self.data)),
            "data": [self.data, "base64"],
        }}


def test_collects_current_snapshot_with_slot_and_non_historical_scope():
    rpc = FakeRpc()
    result = collect_bonding_curve_snapshot(
        rpc, "Mint111", "Curve111", "2026-11-17T12:00:00+00:00")
    assert result.kind == "bonding_curve_snapshot"
    assert result.mint == "Mint111"
    assert result.payload["slot"] == 12345
    assert result.payload["event"]["complete"] is False
    assert result.payload["historical_state"] is False
    assert result.event_at == result.observed_at


def test_rejects_account_not_owned_by_pump_program():
    with pytest.raises(RuntimeError, match="owner does not match"):
        collect_bonding_curve_snapshot(FakeRpc(owner="OtherProgram"), "Mint111", "Curve111")
