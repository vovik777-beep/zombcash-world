from soi.models import Observation
import boost_monitor


class FakeStore:
    def __init__(self):
        self.refs = set()
        self.rows = []

    def add(self, observation):
        if observation.source_ref in self.refs:
            return False
        self.refs.add(observation.source_ref)
        self.rows.append(observation)
        return True


def test_existing_boost_monitor_archives_distinct_states_without_duplicate_rows():
    store = FakeStore()
    rows = [
        {"chainId": "solana", "tokenAddress": "mint-A", "amount": 10, "totalAmount": 20},
        {"chainId": "solana", "tokenAddress": "mint-A", "amount": 10, "totalAmount": 20},
        {"chainId": "solana", "tokenAddress": "mint-B", "amount": 5, "totalAmount": 5},
        {"chainId": "ethereum", "tokenAddress": "mint-C", "amount": 1, "totalAmount": 1},
    ]

    saved = boost_monitor.record_soi_boost_observations(rows, store)

    assert saved == 2
    assert len(store.rows) == 2
    assert all(row.kind == "boost_observed" for row in store.rows)
    assert all(row.source == "boost_monitor" for row in store.rows)
    assert all(row.event_at is None for row in store.rows)


def test_promotion_capture_does_not_poll_boost_endpoint_again():
    from soi.ingest_promotion import capture

    class FakeClient:
        def latest_profiles(self):
            return []
        def latest_ads(self):
            return []
        def latest_boosts(self):
            raise AssertionError("boost_monitor already owns this endpoint")
        def token_orders(self, mint):
            return []

    class EmptyStore:
        def observations(self):
            return []
        def count(self):
            return 0

    result = capture(FakeClient(), EmptyStore())

    assert result["boosts"] == "existing boost_monitor"
    assert result["profiles"] == 0
    assert result["ads"] == 0
