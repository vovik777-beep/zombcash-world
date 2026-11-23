from soi.analytics.launch_cohorts import build_launch_cohorts
from soi.models import Observation

MINT_A = "CohortMintA"
MINT_B = "CohortMintB"
WSOL = "So11111111111111111111111111111111111111112"


def obs(source, ref, kind, mint, at, event, **extra):
    payload = {"signature": ref.split(":")[0], "event": event, **extra}
    return Observation(source, ref, at, at, kind, mint, payload, "observed")


def launch(mint, sig, at):
    return obs("pumpfun_rpc", f"{sig}:0", "token_created", mint, at,
               {"mint": mint, "creator": "creator"})


def test_groups_launches_by_utc_day_and_reports_completion_censoring():
    rows = [
        launch(MINT_A, "launchA", "2026-11-17T23:50:00+00:00"),
        obs("pumpfun_rpc", "completeA:0", "bonding_curve_completed", MINT_A,
            "2026-11-18T00:00:00+00:00", {"bonding_curve": "curveA"}),
        launch(MINT_B, "launchB", "2026-11-18T01:00:00+00:00"),
    ]
    result = build_launch_cohorts(rows)
    assert [c["launch_day_utc"] for c in result["cohorts"]] == ["2026-11-17", "2026-11-18"]
    first = result["cohorts"][0]
    assert first["completion"]["median"] == 600
    second = result["cohorts"][1]
    assert second["completion"]["observed"] == 0
    assert second["completion"]["not_observed_or_unknown"] == 1
    assert second["completion"]["not_observed_is_not_proof_of_non_completion"] is True


def test_migration_duration_requires_confirmed_pool_match_and_order():
    mint = MINT_A
    rows = [
        launch(mint, "launch", "2026-11-17T10:00:00+00:00"),
        obs("pumpfun_rpc", "complete:0", "bonding_curve_completed", mint,
            "2026-11-17T10:10:00+00:00", {"bonding_curve": "curve"}, log_index=4),
        obs("pumpswap_rpc", "migration:0", "pool_created", mint,
            "2026-11-17T10:11:00+00:00",
            {"pool": "poolA", "base_mint": mint, "quote_mint": WSOL}, log_index=5),
        obs("pumpfun_rpc", "migration:0", "migration_completed", mint,
            "2026-11-17T10:11:00+00:00",
            {"pool": "poolA", "quote_mint": WSOL}, log_index=6),
    ]
    result = build_launch_cohorts(rows)
    row = result["cohorts"][0]["launches"][0]
    assert row["migration_status"] == "confirmed"
    assert row["migration_elapsed_seconds"] == 660


def test_first_hour_volume_and_buyers_are_pumpfun_only_and_wsol_only():
    mint = MINT_A
    rows = [
        launch(mint, "launch", "2026-11-17T10:00:00+00:00"),
        obs("pumpfun_rpc", "buy:0", "trade_buy", mint,
            "2026-11-17T10:01:00+00:00",
            {"user": "walletA", "sol_amount": 2_000_000_000, "quote_mint": WSOL}),
        obs("pumpfun_rpc", "buy2:0", "trade_buy", mint,
            "2026-11-17T10:02:00+00:00",
            {"user": "walletB", "sol_amount": 500_000, "quote_mint": "USDC"}),
        obs("pumpswap_rpc", "amm:0", "trade_buy", mint,
            "2026-11-17T10:03:00+00:00",
            {"user": "walletC", "quote_amount_in": 9_000_000_000, "pool": "pool"}),
    ]
    result = build_launch_cohorts(rows)
    row = result["cohorts"][0]["launches"][0]
    assert row["pumpfun_first_hour_buyers"] == 2
    assert row["pumpfun_first_hour_trade_count"] == 2
    # One non-WSOL quote trade makes the full-window SOL volume incomplete.
    # The observed WSOL subtotal must not be presented as a complete total.
    assert row["pumpfun_first_hour_sol_volume"] is None
    assert row["pumpfun_quote_coverage_complete"] is False


def test_conflicting_launch_records_are_reported_not_arbitrarily_selected():
    rows = [
        launch(MINT_A, "launchA", "2026-11-17T10:00:00+00:00"),
        launch(MINT_A, "launchB", "2026-11-17T10:01:00+00:00"),
    ]
    result = build_launch_cohorts(rows)
    assert result["cohort_count"] == 0
    assert result["errors"][0]["mint"] == MINT_A
    assert result["coverage"]["launches_with_ambiguous_or_invalid_timeline"] == 1


def test_empty_input_has_explicit_empty_coverage():
    result = build_launch_cohorts([])
    assert result["cohorts"] == []
    assert result["launches_considered"] == 0

def test_missing_first_hour_trade_observations_remain_unknown_not_zero():
    rows = [launch(MINT_A, "launch", "2026-11-17T10:00:00+00:00")]
    result = build_launch_cohorts(rows)
    row = result["cohorts"][0]["launches"][0]
    assert row["pumpfun_first_hour_trade_count"] is None
    assert row["pumpfun_first_hour_buyers"] is None
    assert row["pumpfun_first_hour_sol_volume"] is None
    summary = result["cohorts"][0]["pumpfun_first_hour_sol_volume_per_launch"]
    assert summary["n"] == 0
    assert summary["unknown_or_incomplete"] == 1


def test_non_wsol_trade_makes_sol_volume_unknown_not_zero():
    rows = [
        launch(MINT_A, "launch", "2026-11-17T10:00:00+00:00"),
        obs("pumpfun_rpc", "buy:0", "trade_buy", MINT_A,
            "2026-11-17T10:01:00+00:00",
            {"user": "wallet", "quote_mint": "USDC", "quote_amount": 123}),
    ]
    result = build_launch_cohorts(rows)
    row = result["cohorts"][0]["launches"][0]
    assert row["pumpfun_first_hour_trade_count"] == 1
    assert row["pumpfun_first_hour_sol_volume"] is None
    assert result["cohorts"][0]["pumpfun_first_hour_sol_volume_per_launch"]["unknown_or_incomplete"] == 1



def market_snapshot(mint, ref, at, market_cap, fdv=None):
    return obs("dexscreener", ref, "market_snapshot", mint, at,
        {"market_cap_usd": market_cap, "fdv_usd": fdv,
         "pair_address": "pair-" + mint})


def test_market_cap_milestones_use_marketcap_not_fdv_and_measure_first_observation():
    mint = MINT_A
    rows = [
        launch(mint, "launch", "2026-11-17T10:00:00+00:00"),
        market_snapshot(mint, "s1", "2026-11-17T10:05:00+00:00", 120_000, 900_000),
        market_snapshot(mint, "s2", "2026-11-17T10:20:00+00:00", 600_000, 2_000_000),
        market_snapshot(mint, "s3", "2026-11-17T10:40:00+00:00", 1_100_000, 4_000_000),
    ]
    result = build_launch_cohorts(rows)
    item = result["cohorts"][0]["launches"][0]
    assert item["market_cap_milestones_first_hour"]["100000"]["elapsed_seconds"] == 300
    assert item["market_cap_milestones_first_hour"]["500000"]["elapsed_seconds"] == 1200
    assert item["market_cap_milestones_first_hour"]["1000000"]["elapsed_seconds"] == 2400
    assert item["market_cap_first_hour_max_observed_usd"] == 1_100_000


def test_fdv_only_and_missing_marketcap_do_not_count_as_milestone():
    mint = MINT_A
    rows = [
        launch(mint, "launch", "2026-11-17T10:00:00+00:00"),
        market_snapshot(mint, "fdvOnly", "2026-11-17T10:05:00+00:00", None, 5_000_000),
        market_snapshot(mint, "outside", "2026-11-17T11:01:00+00:00", 2_000_000, 2_000_000),
    ]
    result = build_launch_cohorts(rows)
    item = result["cohorts"][0]["launches"][0]
    assert item["market_cap_snapshot_count_first_hour"] == 0
    assert item["market_cap_milestones_first_hour"]["1000000"]["reached"] is False
    summary = result["cohorts"][0]["market_cap_milestones_first_hour"]["1000000"]
    assert summary["reached"] == 0
    assert summary["not_observed_or_unknown"] == 1


def test_market_cap_cohort_coverage_reports_missing_snapshots():
    rows = [launch(MINT_A, "launchA", "2026-11-17T10:00:00+00:00"),
            launch(MINT_B, "launchB", "2026-11-17T10:10:00+00:00"),
            market_snapshot(MINT_A, "snapA", "2026-11-17T10:05:00+00:00", 150_000)]
    result = build_launch_cohorts(rows)
    coverage = result["cohorts"][0]["market_cap_snapshot_coverage"]
    assert coverage["launches_with_at_least_one_market_cap_snapshot"] == 1
    assert coverage["launches_without_market_cap_snapshot"] == 1
