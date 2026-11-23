from types import SimpleNamespace

from soi.analytics.creator_funding import analyze_creator_funding_links


def obs(kind, mint, event, signature="sig-create"):
    return SimpleNamespace(
        kind=kind, mint=mint, event_at="2026-11-17T10:00:00Z",
        observed_at="2026-11-17T10:00:00Z",
        payload={"event": event, "signature": signature},
    )


def cohort(signature="buy-a"):
    return [{
        "mint": "MintA", "launch_at": "2026-11-17T10:00:00+00:00",
        "wallets": [{"wallet": "BuyerA", "first_buy_seconds": 60,
                     "first_buy_signature": signature}],
    }]


def funding(source="Creator", anchor="buy-a"):
    return [{
        "wallet": "BuyerA",
        "first_observed_buy_at": "2026-11-17T10:01:00+00:00",
        "coverage": {"coverage_complete_within_lookback": True,
                     "anchor_signature": anchor},
        "transfers": [{
            "source_wallet": source, "destination_wallet": "BuyerA",
            "sol": 1.25, "lamports": 1_250_000_000, "block_time": 1794909500,
            "signature": "funding-sig", "instruction_index": 2,
            "evidence": "parsed_system_transfer",
        }],
    }]


def test_exact_creator_address_as_direct_funder_is_reported():
    observations = [obs("token_created", "MintA", {
        "creator": "Creator", "user": "LaunchUser",
    })]
    row = analyze_creator_funding_links(observations, funding(), cohort())["rows"][0]
    assert row["direct_creator_funding_transfer_count"] == 1
    assert row["distinct_sampled_buyers_funded_by_creator"] == 1
    assert row["creator_funding_links"][0]["buyer_wallet"] == "BuyerA"
    assert row["creator_funding_links"][0]["matched_roles"] == ["launch_creator"]
    assert row["interpretation"]["identity_or_common_control_inference"] is False


def test_create_event_user_and_creator_are_distinguished():
    observations = [obs("token_created", "MintA", {
        "creator": "Creator", "user": "DifferentUser",
    })]
    row = analyze_creator_funding_links(
        observations, funding("DifferentUser"), cohort())["rows"][0]
    assert row["direct_creator_funding_transfer_count"] == 0
    assert row["direct_create_user_funding_transfer_count"] == 1


def test_ambiguous_launch_evidence_does_not_guess_creator():
    observations = [
        obs("token_created", "MintA", {"creator": "CreatorA", "user": "UserA"}, "sig-a"),
        obs("token_created", "MintA", {"creator": "CreatorB", "user": "UserB"}, "sig-b"),
    ]
    row = analyze_creator_funding_links(observations, funding("CreatorA"), cohort())["rows"][0]
    assert row["launch_evidence_status"] == "ambiguous"
    assert row["creator_address"] is None
    assert row["direct_creator_funding_transfer_count"] == 0


def test_funding_anchor_must_match_this_mints_first_buy():
    observations = [obs("token_created", "MintA", {"creator": "Creator", "user": "User"})]
    row = analyze_creator_funding_links(
        observations, funding("Creator", anchor="another-mint-buy"), cohort())["rows"][0]
    assert row["direct_creator_funding_transfer_count"] == 0
    assert row["funding_rows_not_aligned_to_this_mint_buy"] == 1


def test_no_matching_transfer_is_not_claimed_as_no_funding():
    observations = [obs("token_created", "MintA", {"creator": "Creator", "user": "User"})]
    row = analyze_creator_funding_links(observations, [], cohort())["rows"][0]
    assert row["direct_creator_funding_transfer_count"] == 0
    assert row["interpretation"]["absence_is_not_proof_of_no_creator_funding"] is True
