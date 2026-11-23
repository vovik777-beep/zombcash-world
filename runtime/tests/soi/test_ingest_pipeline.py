from soi.ingest_promotion import capture
from soi.models import Observation
from soi.storage import ObservationStore


class FakePromotionClient:
    def latest_profiles(self):
        return [{"chainId": "solana", "tokenAddress": "MintA", "url": "https://example/profile"}]
    def latest_ads(self):
        return [{"chainId": "solana", "tokenAddress": "MintA", "type": "tokenAd"}]
    def latest_boosts(self):
        return [{"chainId": "solana", "tokenAddress": "MintA", "amount": 100}]
    def token_orders(self, mint):
        return [{"type": "tokenAd", "status": "approved"}]


def test_promotion_capture_records_distinct_observation_types(tmp_path):
    store = ObservationStore(tmp_path / "soi.sqlite")
    store.add(Observation("pumpfun_rpc", "sig:0", "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:00:00+00:00", "token_created", "MintA", {"event": {}}, "observed"))
    result = capture(FakePromotionClient(), store)
    assert result["profiles"] == 1
    assert result["ads"] == 1
    assert result["boosts"] == "existing boost_monitor"
    assert result["orders"] == 1
    kinds = {o.kind for o in store.observations("MintA")}
    assert {"profile_observed", "paid_ad_observed", "paid_order_observed"} <= kinds
    assert "boost_observed" not in kinds
