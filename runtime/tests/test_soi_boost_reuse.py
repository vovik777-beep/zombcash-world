from pathlib import Path

import boost_monitor


RUNTIME = Path(__file__).resolve().parents[1]


def test_soi_is_disabled_and_boost_monitor_is_not_coupled_to_it():
    bot_source = (RUNTIME / "bot.py").read_text(encoding="utf-8")
    boost_source = (RUNTIME / "boost_monitor.py").read_text(encoding="utf-8")

    assert "SOI_ENABLED = False" in bot_source
    assert "record_soi_boost_observations" not in boost_source
    assert not hasattr(boost_monitor, "record_soi_boost_observations")


def test_promotion_capture_does_not_poll_boost_endpoint_again():
    from soi.ingest_promotion import capture

    class FakeClient:
        def latest_profiles(self):
            return []
        def latest_ads(self):
            return []
        def latest_boosts(self):
            raise AssertionError("boost_monitor owns this endpoint")
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
