from types import SimpleNamespace

from soi.analytics.creator_holders import analyze_creator_holder_overlap


def obs(kind, mint, observed_at, event=None, signature=None, event_at=None):
    return SimpleNamespace(
        kind=kind, mint=mint, observed_at=observed_at, event_at=event_at,
        payload={"event": event or {}, "signature": signature},
    )


def test_creator_is_matched_only_by_exact_parsed_owner_address():
    items = [
        obs("token_created", "MintA", "2026-11-17T10:00:00Z",
            {"creator": "CreatorWallet", "user": "UserWallet"},
            "sig-a", "2026-11-17T10:00:00Z"),
        obs("holder_snapshot", "MintA", "2026-11-17T11:00:00Z", {
            "supply": {"amount_raw": "1000", "decimals": 0},
            "coverage": {"full_holder_set": False, "token_accounts_returned": 2},
            "top_owners": [
                {"owner": "CreatorWallet", "amount_raw": "200", "share_of_total_supply": 0.2},
                {"owner": "OtherWallet", "amount_raw": "100", "share_of_total_supply": 0.1},
            ],
        }),
    ]
    row = analyze_creator_holder_overlap(items)["rows"][0]
    assert row["launch_creator"]["status"] == "matched_in_top20_owner_set"
    assert row["launch_creator"]["amount_raw"] == "200"
    assert row["launch_user"]["status"] == "not_found_in_top20_owner_set"
    assert row["top_owner_concentration"]["top1_known_owner_share_of_total_supply"] == 0.2
    assert row["interpretation"]["holder_snapshot_is_full_distribution"] is False


def test_conflicting_launch_creator_evidence_is_not_resolved_by_guessing():
    items = [
        obs("token_created", "MintA", "2026-11-17T10:00:00Z",
            {"creator": "CreatorA", "user": "UserA"}, "sig-a", "2026-11-17T10:00:00Z"),
        obs("token_created", "MintA", "2026-11-17T10:00:01Z",
            {"creator": "CreatorB", "user": "UserB"}, "sig-b", "2026-11-17T10:00:01Z"),
        obs("holder_snapshot", "MintA", "2026-11-17T11:00:00Z", {
            "supply": {"amount_raw": "1000"},
            "top_owners": [{"owner": "CreatorA", "amount_raw": "200"}],
        }),
    ]
    row = analyze_creator_holder_overlap(items)["rows"][0]
    assert row["launch_evidence_status"] == "ambiguous"
    assert row["launch_creator"]["status"] == "ambiguous_launch"
    assert row["launch_creator"]["amount_raw"] is None


def test_missing_snapshot_and_supply_remain_unknown():
    result = analyze_creator_holder_overlap([
        obs("token_created", "MintA", "2026-11-17T10:00:00Z",
            {"creator": "CreatorA"}, "sig-a", "2026-11-17T10:00:00Z"),
        obs("holder_snapshot", "MintB", "2026-11-17T11:00:00Z", {
            "top_owners": [{"owner": "WalletB", "amount_raw": "100"}],
        }),
    ])
    by_mint = {row["mint"]: row for row in result["rows"]}
    assert by_mint["MintA"]["launch_creator"]["status"] == "no_holder_snapshot"
    assert by_mint["MintB"]["launch_creator"]["status"] == "unknown_address"
    assert by_mint["MintB"]["top_owner_concentration"]["top1_known_owner_share_of_total_supply"] is None
