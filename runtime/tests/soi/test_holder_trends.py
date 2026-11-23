from types import SimpleNamespace

from soi.analytics.holder_trends import analyze_holder_trends


def ob(kind, mint, at, event, signature=None):
    return SimpleNamespace(kind=kind, mint=mint, observed_at=at, event_at=at,
                           source_ref=f"{mint}:{at}", payload={"event": event, "signature": signature})


def test_creator_balance_delta_requires_visibility_in_adjacent_snapshots():
    items = [
        ob("token_created", "MintA", "2026-11-17T10:00:00Z", {"creator": "Creator"}, "launch"),
        ob("holder_snapshot", "MintA", "2026-11-17T11:00:00Z", {
            "supply": {"amount_raw": "1000"},
            "top_owners": [{"owner": "Creator", "amount_raw": "200"}],
            "coverage": {"token_accounts_returned": 20, "resolved_owner_accounts": 20},
        }),
        ob("holder_snapshot", "MintA", "2026-11-17T12:00:00Z", {
            "supply": {"amount_raw": "1000"},
            "top_owners": [{"owner": "Creator", "amount_raw": "150"}],
            "coverage": {"token_accounts_returned": 20, "resolved_owner_accounts": 20},
        }),
    ]
    row = analyze_holder_trends(items)["rows"][0]
    assert row["creator_visible_snapshot_count"] == 2
    assert row["observed_creator_balance_delta_count"] == 1
    assert row["latest_observed_creator_balance_delta_raw"] == -50
    assert row["history"][1]["creator_balance_change_status"] == "observed_top20_owner_balance_change"


def test_creator_absence_from_top20_is_unknown_not_zero():
    items = [
        ob("token_created", "MintA", "2026-11-17T10:00:00Z", {"creator": "Creator"}, "launch"),
        ob("holder_snapshot", "MintA", "2026-11-17T11:00:00Z", {
            "supply": {"amount_raw": "1000"},
            "top_owners": [{"owner": "Other", "amount_raw": "300"}],
        }),
        ob("holder_snapshot", "MintA", "2026-11-17T12:00:00Z", {
            "supply": {"amount_raw": "1000"},
            "top_owners": [{"owner": "Creator", "amount_raw": "100"}],
        }),
    ]
    row = analyze_holder_trends(items)["rows"][0]
    assert row["history"][0]["creator_status"] == "not_found_in_top20_owner_set"
    assert row["history"][0]["creator_amount_raw"] is None
    assert row["history"][1]["creator_balance_delta_raw_from_previous_snapshot"] is None
    assert row["history"][1]["creator_balance_change_status"] == "creator_not_visible_in_both_top20_snapshots"


def test_ambiguous_launch_suppresses_creator_balance_attribution():
    items = [
        ob("token_created", "MintA", "2026-11-17T10:00:00Z", {"creator": "CreatorA"}, "launch-a"),
        ob("token_created", "MintA", "2026-11-17T10:01:00Z", {"creator": "CreatorB"}, "launch-b"),
        ob("holder_snapshot", "MintA", "2026-11-17T11:00:00Z", {
            "supply": {"amount_raw": "1000"},
            "top_owners": [{"owner": "CreatorA", "amount_raw": "200"}],
        }),
    ]
    row = analyze_holder_trends(items)["rows"][0]
    assert row["launch_evidence_status"] == "ambiguous"
    assert row["history"][0]["creator_status"] == "ambiguous_launch"
    assert row["history"][0]["creator_amount_raw"] is None
