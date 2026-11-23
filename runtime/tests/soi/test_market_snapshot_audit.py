from soi.analytics.market_snapshot_audit import audit_market_snapshot_series
from soi.models import Observation


def snapshot(mint, ref, at, pair, cap, price=None, liquidity=None, fdv=None):
    return Observation("dexscreener", ref, at, at, "market_snapshot", mint,
        {"event": {"pair_address": pair, "market_cap_usd": cap, "price_usd": price,
                   "liquidity_usd": liquidity, "fdv_usd": fdv}}, "observed")


def test_audit_reports_gaps_pair_switches_and_large_market_cap_changes():
    rows = [snapshot("MintA", "a", "2026-11-17T10:00:00+00:00", "pair1", 100_000),
            snapshot("MintA", "b", "2026-11-17T10:20:00+00:00", "pair2", 900_000)]
    item = audit_market_snapshot_series(rows)["items"][0]
    assert item["large_gaps"][0]["gap_seconds"] == 1200
    assert item["pair_switches"][0]["to_pair"] == "pair2"
    assert item["market_cap_jump_warnings"][0]["ratio"] == 9
    assert item["market_cap_jump_warnings"][0]["same_pair"] is False


def test_duplicate_same_timestamp_and_pair_is_reported_not_deduplicated():
    rows = [snapshot("MintA", "a", "2026-11-17T10:00:00+00:00", "pair1", 100_000),
            snapshot("MintA", "b", "2026-11-17T10:00:00+00:00", "pair1", 100_000)]
    item = audit_market_snapshot_series(rows)["items"][0]
    assert item["snapshot_count"] == 2
    assert item["duplicate_timestamp_pair_groups"] == 1
    assert item["duplicates"][0]["count"] == 2


def test_missing_market_cap_is_not_zero_and_does_not_create_jump():
    rows = [snapshot("MintA", "a", "2026-11-17T10:00:00+00:00", "pair1", 100_000),
            snapshot("MintA", "b", "2026-11-17T10:05:00+00:00", "pair1", None),
            snapshot("MintA", "c", "2026-11-17T10:10:00+00:00", "pair1", 900_000)]
    item = audit_market_snapshot_series(rows)["items"][0]
    assert item["market_cap_valid_observations"] == 2
    assert item["market_cap_jump_warnings"] == []


def test_low_liquidity_relative_to_market_cap_is_flagged_not_rejected():
    rows = [snapshot("MintA", "a", "2026-11-17T10:00:00+00:00", "pair1",
                     1_000_000, price=0.01, liquidity=2_000, fdv=9_000_000)]
    item = audit_market_snapshot_series(rows)["items"][0]
    assert item["low_liquidity_warnings"][0]["liquidity_to_market_cap_ratio"] == 0.002
    assert item["snapshots"][0]["valuation_evidence_status"] == "low_liquidity_relative_to_market_cap"
    assert item["snapshots"][0]["fdv_usd"] == 9_000_000


def test_price_jumps_only_compared_within_same_pair():
    rows = [snapshot("MintA", "a", "2026-11-17T10:00:00+00:00", "pair1", 100_000, 0.01, 20_000),
            snapshot("MintA", "b", "2026-11-17T10:05:00+00:00", "pair1", 110_000, 0.10, 20_000),
            snapshot("MintA", "c", "2026-11-17T10:10:00+00:00", "pair2", 120_000, 1.00, 20_000)]
    item = audit_market_snapshot_series(rows)["items"][0]
    assert len(item["same_pair_price_jump_warnings"]) == 1
    assert item["same_pair_price_jump_warnings"][0]["ratio"] == 10


def test_missing_liquidity_remains_unknown():
    rows = [snapshot("MintA", "a", "2026-11-17T10:00:00+00:00", "pair1", 100_000, 0.01, None)]
    item = audit_market_snapshot_series(rows)["items"][0]
    assert item["low_liquidity_warnings"] == []
    assert item["snapshots"][0]["valuation_evidence_status"] == "liquidity_missing_or_invalid"


def test_empty_series_has_explicit_zero_coverage():
    result = audit_market_snapshot_series([])
    assert result["mints"] == 0 and result["snapshot_count"] == 0 and result["items"] == []



def test_pumpswap_event_reserves_match_exact_dexscreener_pair_and_price():
    from soi.analytics.market_snapshot_audit import audit_market_snapshot_series
    from soi.models import Observation

    mint, quote, pool = "MintA", "So11111111111111111111111111111111111111112", "PoolA"
    pool_created = Observation("pumpswap_rpc", "pool-create", "2026-11-17T10:00:00+00:00",
        "2026-11-17T10:00:00+00:00", "pool_created", mint,
        {"event": {"pool": pool, "base_mint": mint, "quote_mint": quote,
                   "base_mint_decimals": 6, "quote_mint_decimals": 9}}, "observed")
    trade = Observation("pumpswap_rpc", "trade-1", "2026-11-17T10:04:00+00:00",
        "2026-11-17T10:04:00+00:00", "trade_buy", mint,
        {"event": {"pool": pool, "pool_base_token_reserves": 1_000_000_000,
                   "pool_quote_token_reserves": 20_000_000_000}}, "observed")
    snap = Observation("dexscreener", "snapshot-1", "2026-11-17T10:05:00+00:00",
        "2026-11-17T10:05:00+00:00", "market_snapshot", mint,
        {"event": {"pair_address": pool, "base_token": mint, "quote_token": quote,
                   "market_cap_usd": 100_000, "price_usd": 0.02,
                   "price_native": 0.02, "liquidity_usd": 10_000}}, "observed")

    item = audit_market_snapshot_series([pool_created, trade, snap])["items"][0]["snapshots"][0]
    assert item["reserve_evidence_status"] == "matched_event_reserves"
    assert item["reserve_age_seconds"] == 60
    assert item["pair_base_reserves_ui"] == 1_000
    assert item["pair_quote_reserves_ui"] == 20
    assert item["reserve_implied_price_native"] == 0.02
    assert item["reserve_price_deviation_ratio"] == 1


def test_pumpswap_reserves_are_not_matched_across_different_pair_addresses():
    from soi.models import Observation

    mint, quote = "MintA", "So11111111111111111111111111111111111111112"
    pool_created = Observation("pumpswap_rpc", "pool-create", "2026-11-17T10:00:00+00:00",
        "2026-11-17T10:00:00+00:00", "pool_created", mint,
        {"event": {"pool": "PoolA", "base_mint": mint, "quote_mint": quote,
                   "base_mint_decimals": 6, "quote_mint_decimals": 9}}, "observed")
    trade = Observation("pumpswap_rpc", "trade-1", "2026-11-17T10:04:00+00:00",
        "2026-11-17T10:04:00+00:00", "trade_buy", mint,
        {"event": {"pool": "PoolA", "pool_base_token_reserves": 1_000_000_000,
                   "pool_quote_token_reserves": 20_000_000_000}}, "observed")
    snap = snapshot(mint, "snapshot-1", "2026-11-17T10:05:00+00:00", "DifferentPool",
                    100_000, price=0.02, liquidity=10_000)
    snap.payload["event"].update({"base_token": mint, "quote_token": quote, "price_native": 0.02})
    item = audit_market_snapshot_series([pool_created, trade, snap])["items"][0]["snapshots"][0]
    assert item["reserve_evidence_status"] == "pool_metadata_not_observed"



def test_reserve_match_supports_reversed_pair_orientation_and_rejects_stale_event():
    from soi.models import Observation

    mint, quote, pool = "MintB", "So11111111111111111111111111111111111111112", "PoolB"
    pool_created = Observation("pumpswap_rpc", "pool-create-b", "2026-11-17T10:00:00+00:00",
        "2026-11-17T10:00:00+00:00", "pool_created", mint,
        {"event": {"pool": pool, "base_mint": mint, "quote_mint": quote,
                   "base_mint_decimals": 6, "quote_mint_decimals": 9}}, "observed")
    trade = Observation("pumpswap_rpc", "trade-b", "2026-11-17T10:04:00+00:00",
        "2026-11-17T10:04:00+00:00", "trade_buy", mint,
        {"event": {"pool": pool, "pool_base_token_reserves": 1_000_000_000,
                   "pool_quote_token_reserves": 20_000_000_000}}, "observed")
    reversed_pair = Observation("dexscreener", "snapshot-b", "2026-11-17T10:05:00+00:00",
        "2026-11-17T10:05:00+00:00", "market_snapshot", mint,
        {"event": {"pair_address": pool, "base_token": quote, "quote_token": mint,
                   "market_cap_usd": 100_000, "price_usd": 0.02,
                   "price_native": 50, "liquidity_usd": 10_000}}, "observed")
    matched = audit_market_snapshot_series([pool_created, trade, reversed_pair])["items"][0]["snapshots"][0]
    assert matched["reserve_evidence_status"] == "matched_event_reserves"
    assert matched["pair_base_reserves_ui"] == 20
    assert matched["pair_quote_reserves_ui"] == 1_000
    assert matched["reserve_implied_price_native"] == 50

    stale = Observation("dexscreener", "snapshot-stale", "2026-11-17T10:20:00+00:00",
        "2026-11-17T10:20:00+00:00", "market_snapshot", mint,
        {"event": {"pair_address": pool, "base_token": mint, "quote_token": quote,
                   "market_cap_usd": 100_000, "price_usd": 0.02,
                   "price_native": 0.02, "liquidity_usd": 10_000}}, "observed")
    stale_item = audit_market_snapshot_series([pool_created, trade, stale])["items"][0]["snapshots"][0]
    assert stale_item["reserve_evidence_status"] == "reserve_event_not_observed_within_age"



def test_confirmed_pool_account_state_takes_precedence_over_event_reserves():
    from soi.models import Observation

    mint, quote, pool = "MintPriority", "QuotePriority", "PoolPriority"
    created = Observation("pumpswap_rpc", "created", "2026-11-17T10:00:00+00:00",
        "2026-11-17T10:00:00+00:00", "pool_created", mint,
        {"event": {"pool": pool, "base_mint": mint, "quote_mint": quote,
                   "base_mint_decimals": 6, "quote_mint_decimals": 6}}, "observed")
    event_reserve = Observation("pumpswap_rpc", "trade", "2026-11-17T10:04:00+00:00",
        "2026-11-17T10:04:00+00:00", "trade_buy", mint,
        {"event": {"pool": pool, "pool_base_token_reserves": 1_000_000,
                   "pool_quote_token_reserves": 10_000_000}}, "observed")
    account_state = Observation("pumpswap_rpc", "state", "2026-11-17T10:04:00+00:00",
        "2026-11-17T10:04:00+00:00", "pumpswap_pool_state", mint,
        {"event": {"pool_address": pool, "base_mint": mint, "quote_mint": quote,
                   "base_decimals": 6, "quote_decimals": 6,
                   "base_reserves_raw": 2_000_000, "quote_reserves_raw": 30_000_000,
                   "pool_account_slot": 100, "vault_accounts_slot": 101}}, "observed")
    snap = snapshot(mint, "snapshot", "2026-11-17T10:05:00+00:00", pool,
                    100_000, price=0.02, liquidity=10_000)
    snap.payload["event"].update({"base_token": mint, "quote_token": quote, "price_native": 15})
    item = audit_market_snapshot_series([created, event_reserve, account_state, snap])["items"][0]["snapshots"][0]
    assert item["reserve_evidence_status"] == "matched_account_state"
    assert item["pool_base_reserves_raw"] == 2_000_000
    assert item["pool_quote_reserves_raw"] == 30_000_000
