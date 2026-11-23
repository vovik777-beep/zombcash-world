from dataclasses import dataclass
from soi.analytics.token_profile import build_token_profiles


@dataclass
class Obs:
    source: str
    source_ref: str
    observed_at: str
    event_at: str | None
    kind: str
    mint: str | None
    payload: dict
    confidence: str = "observed"


def test_profile_keeps_market_cap_fdv_and_supply_separate():
    observations = [
        Obs("pumpfun", "launch1", "2026-11-17T10:00:00Z", "2026-11-17T10:00:00Z",
            "token_created", "MINT", {"event": {"name": "Test", "symbol": "T",
            "creator": "CREATOR", "user": "USER"}}),
        Obs("dex", "market1", "2026-11-17T10:01:00Z", None, "market_snapshot", "MINT",
            {"event": {"market_cap_usd": 1000, "fdv_usd": 9000, "liquidity_usd": 100}}),
        Obs("rpc", "mint1", "2026-11-17T10:02:00Z", None, "mint_supply_state", "MINT",
            {"event": {"supply_raw": 12345, "decimals": 6, "risk_indicators": {"mint_authority_present": True}}}),
    ]
    result = build_token_profiles(observations)
    profile = result["profiles"][0]
    assert profile["market"]["market_cap_usd"] == 1000
    assert profile["market"]["fdv_usd"] == 9000
    assert profile["mint_state"]["total_supply_raw"] == "12345"
    assert profile["mint_state"]["circulating_supply_raw"] is None
    assert profile["mint_state"]["risk_indicators"]["mint_authority_present"] is True


def test_missing_sources_remain_unknown_not_false_or_zero():
    result = build_token_profiles([
        Obs("pumpfun", "launch1", "2026-11-17T10:00:00Z", None,
            "token_created", "MINT", {"event": {"creator": "C"}})
    ])
    profile = result["profiles"][0]
    assert profile["evidence_coverage"]["market"] == "unknown"
    assert profile["market"]["market_cap_usd"] is None
    assert profile["evidence_coverage"]["holder_snapshot"] == "unknown"
    assert profile["holders"]["top_owners"] is None
    assert profile["holders"]["distribution_scope"] == "unknown"


def test_ambiguous_launch_suppresses_creator_and_analytics_rows_attach_by_mint():
    observations = [
        Obs("pumpfun", "launch1", "2026-11-17T10:00:00Z", None,
            "token_created", "MINT", {"event": {"creator": "A"}}),
        Obs("pumpfun", "launch2", "2026-11-17T10:00:01Z", None,
            "token_created", "MINT", {"event": {"creator": "B"}}),
    ]
    result = build_token_profiles(observations, creator_holder_analysis={
        "rows": [{"mint": "MINT", "launch_creator": {"status": "ambiguous_launch"}}]
    })
    profile = result["profiles"][0]
    assert profile["launch"]["evidence_status"] == "ambiguous"
    assert profile["launch"]["creator"] is None
    assert profile["analysis"]["creator_holder_analysis"]["launch_creator"]["status"] == "ambiguous_launch"


def test_pool_state_is_labeled_point_in_time_and_holder_snapshot_partial():
    observations = [
        Obs("rpc", "pool1", "2026-11-17T10:00:00Z", None, "pumpswap_pool_state", "MINT",
            {"event": {"pool_address": "POOL", "base_reserves_raw": 500, "quote_reserves_raw": 600,
            "pool_account_slot": 44, "vault_accounts_slot": 45}}),
        Obs("rpc", "holders1", "2026-11-17T10:01:00Z", None, "holder_snapshot", "MINT",
            {"event": {"top_owners": [{"owner": "A"}], "coverage": {"token_accounts_returned": 20}}}),
    ]
    profile = build_token_profiles(observations)["profiles"][0]
    assert profile["pool_state"]["scope"] == "point_in_time_account_state"
    assert profile["pool_state"]["base_reserves_raw"] == 500
    assert profile["holders"]["distribution_scope"] == "partial_top20_only"
    assert profile["holders"]["absence_from_top20_means_zero"] is False

 

def test_duplicate_observation_of_same_launch_does_not_create_conflict():
    same_event = {"signature": "SIG", "event": {"creator": "CREATOR", "user": "USER"}}
    observations = [
        Obs("pumpfun", "source-a", "2026-11-17T10:00:00Z", "2026-11-17T10:00:00Z",
            "token_created", "MINT", same_event),
        Obs("pumpfun", "source-b", "2026-11-17T10:00:02Z", "2026-11-17T10:00:00Z",
            "token_created", "MINT", same_event),
    ]
    profile = build_token_profiles(observations)["profiles"][0]
    assert profile["launch"]["evidence_status"] == "observed"
    assert profile["launch"]["creator"] == "CREATOR"


def test_mint_specific_early_wallet_behavior_attaches_to_profile():
    observation = Obs("pumpfun", "launch", "2026-11-17T10:00:00Z", None,
                      "token_created", "MINT", {"event": {"creator": "C"}})
    result = build_token_profiles(
        [observation],
        early_wallet_behavior=[{"mint": "MINT", "wallet_count": 3}],
    )
    profile = result["profiles"][0]
    assert profile["analysis"]["early_wallet_behavior"]["wallet_count"] == 3
    assert profile["funding_coverage"]["early_wallet_behavior_available"] is True


def test_profile_attaches_all_mint_keyed_analysis_rows_without_cross_mint_leakage():
    observations = [
        Obs("pumpfun", "launch-a", "2026-11-17T10:00:00Z", None,
            "token_created", "MINT-A", {"event": {"creator": "A"}}),
        Obs("pumpfun", "launch-b", "2026-11-17T10:00:00Z", None,
            "token_created", "MINT-B", {"event": {"creator": "B"}}),
    ]
    result = build_token_profiles(
        observations,
        mint_supply_audit={"rows": [{"mint": "MINT-A", "status": "matched"}]},
        holder_trends={"rows": [{"mint": "MINT-B", "snapshot_count": 2}]},
        creator_funding_links={"rows": [{"mint": "MINT-A", "direct_creator_funding_transfer_count": 1}]},
    )
    profiles = {row["mint_address"]: row for row in result["profiles"]}
    assert profiles["MINT-A"]["analysis"]["mint_supply_audit"]["status"] == "matched"
    assert profiles["MINT-A"]["analysis"]["creator_funding_links"]["direct_creator_funding_transfer_count"] == 1
    assert "holder_trends" not in profiles["MINT-A"]["analysis"]
    assert profiles["MINT-B"]["analysis"]["holder_trends"]["snapshot_count"] == 2
    assert "mint_supply_audit" not in profiles["MINT-B"]["analysis"]
