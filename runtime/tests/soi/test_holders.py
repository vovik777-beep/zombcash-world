from soi.sources.holders import collect_holder_snapshot


class FakeRpc:
    def token_supply(self, mint):
        return {"value": {"amount": "1000000", "decimals": 6}}

    def largest_token_accounts(self, mint):
        return {"value": [
            {"address": "AccountA", "amount": "400000", "decimals": 6},
            {"address": "AccountB", "amount": "200000", "decimals": 6},
            {"address": "AccountC", "amount": "100000", "decimals": 6},
        ]}

    def parsed_accounts(self, addresses):
        return [
            {"data": {"parsed": {"type": "account", "info": {"mint": "MintA", "owner": "Wallet1"}}}},
            {"data": {"parsed": {"type": "account", "info": {"mint": "MintA", "owner": "Wallet1"}}}},
            {"data": {"parsed": {"type": "account", "info": {"mint": "MintA", "owner": "Wallet2"}}}},
        ]


def test_holder_snapshot_aggregates_multiple_token_accounts_by_owner():
    obs = collect_holder_snapshot(FakeRpc(), "MintA", "2026-11-17T12:00:00+00:00")
    event = obs.payload["event"]
    assert obs.kind == "holder_snapshot"
    assert event["coverage"]["token_accounts_returned"] == 3
    assert event["coverage"]["unique_owners_in_top_accounts"] == 2
    assert event["top_owners"][0]["owner"] == "Wallet1"
    assert event["top_owners"][0]["amount_raw"] == "600000"
    assert event["top_owners"][0]["share_of_total_supply"] == 0.6
    assert event["coverage"]["full_holder_set"] is False
    assert event["methodology"]["not_a_historical_snapshot"] is True


def test_holder_snapshot_marks_unresolved_owner_without_guessing():
    class PartialRpc(FakeRpc):
        def parsed_accounts(self, addresses):
            return [None, None, None]
    obs = collect_holder_snapshot(PartialRpc(), "MintA", "2026-11-17T12:00:00+00:00")
    event = obs.payload["event"]
    assert event["coverage"]["unresolved_accounts"] == 3
    assert event["top_owners"] == []
    assert event["coverage"]["resolved_top_owners_share_of_supply"] is None
