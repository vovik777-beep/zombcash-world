from soi.analytics.bonding_curve_progress import summarize_bonding_curve_progress
from soi.models import Observation

MINT = "MintProgress"
WSOL = "So11111111111111111111111111111111111111112"


def snapshot(at, slot, *, complete, virtual_quote=1000, real_quote=500):
    event = {
        "complete": complete,
        "quote_mint": WSOL,
        "virtual_token_reserves": 9000 - slot,
        "virtual_quote_reserves": virtual_quote,
        "real_token_reserves": 7000 - slot,
        "real_quote_reserves": real_quote,
    }
    return Observation("pumpfun_rpc", f"curve:{slot}", at, at,
        "bonding_curve_snapshot", MINT,
        {"account": "CurveProgress", "slot": slot, "event": event,
         "historical_state": False}, "observed")


def trade(at, signature, kind, amount):
    return Observation("pumpfun_rpc", f"{signature}:0", at, at, kind, MINT,
        {"signature": signature, "event": {
            "quote_mint": WSOL, "sol_amount": amount, "is_buy": kind == "trade_buy"}},
        "observed")


def test_progress_reports_reserve_deltas_and_interval_trade_activity():
    observations = [
        snapshot("2026-11-17T10:00:00+00:00", 100, complete=False),
        trade("2026-11-17T10:00:30+00:00", "buySig", "trade_buy", 2_000_000_000),
        trade("2026-11-17T10:01:00+00:00", "sellSig", "trade_sell", 500_000_000),
        snapshot("2026-11-17T10:02:00+00:00", 110, complete=False,
                 virtual_quote=1200, real_quote=450),
    ]
    result = summarize_bonding_curve_progress(observations, MINT)
    assert result["snapshot_count"] == 2
    assert result["transitions"][0]["reserve_deltas_raw"]["virtual_quote_reserves"] == 200
    assert result["transitions"][0]["reserve_deltas_raw"]["real_quote_reserves"] == -50
    assert result["interval_activity"][0]["pumpfun_trade_count"] == 2
    assert result["interval_activity"][0]["buys"] == 1
    assert result["interval_activity"][0]["sells"] == 1
    assert result["interval_activity"][0]["quote_volume_raw"] == 2_500_000_000
    assert result["interval_activity"][0]["quote_volume_unit"] == "lamports"


def test_completion_transition_is_bounded_by_last_false_and_first_true_snapshot():
    observations = [
        snapshot("2026-11-17T10:00:00+00:00", 100, complete=False),
        snapshot("2026-11-17T10:05:00+00:00", 120, complete=False),
        snapshot("2026-11-17T10:09:00+00:00", 130, complete=True),
    ]
    result = summarize_bonding_curve_progress(observations, MINT)
    completion = result["completion_progress"]
    assert completion["false_to_true_transition_observed"] is True
    assert completion["last_observed_incomplete_at"] == "2026-11-17T10:05:00+00:00"
    assert completion["first_observed_complete_at"] == "2026-11-17T10:09:00+00:00"
    assert completion["completion_time_bound_seconds"] == 240
    assert completion["completion_event_observed"] is False


def test_missing_snapshots_are_not_interpreted_as_zero_activity():
    result = summarize_bonding_curve_progress([], MINT)
    assert result["snapshot_count"] == 0
    assert result["transitions"] == []
    assert result["coverage"]["missing_snapshots_are_not_zero_activity"] is True
    assert result["completion_progress"]["false_to_true_transition_observed"] is False


def test_quote_units_are_not_assumed_to_be_sol():
    non_sol = "QuoteMint111"
    observations = [
        snapshot("2026-11-17T10:00:00+00:00", 100, complete=False),
        snapshot("2026-11-17T10:01:00+00:00", 101, complete=False),
        Observation("pumpfun_rpc", "trade:0", "2026-11-17T10:00:30+00:00",
            "2026-11-17T10:00:30+00:00", "trade_buy", MINT,
            {"event": {"quote_mint": non_sol, "quote_amount": 123}}, "observed"),
    ]
    result = summarize_bonding_curve_progress(observations, MINT)
    assert result["interval_activity"][0]["quote_volume_raw"] == 123
    assert result["interval_activity"][0]["quote_volume_unit"] == "raw_quote_units"
