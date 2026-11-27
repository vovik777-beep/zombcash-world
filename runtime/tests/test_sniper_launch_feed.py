import asyncio
import unittest
from unittest.mock import patch

from sniper_launch_feed import _event_time_ms, _normalize_launch_event, _normalize_pumpdev_trade_event, _restore_paper_monitoring_opt_in, _safe_number


MINT = "5qErNMKHiYvixh69V1aw8Dazzg6bvUN41ojcozUppump"


class SniperLaunchFeedTests(unittest.TestCase):
    def test_normalizes_new_token_event(self):
        event = _normalize_launch_event({
            "txType": "create", "mint": MINT, "name": "Test Launch",
            "symbol": "TEST", "traderPublicKey": "CreatorPublicKey",
            "signature": "TestSignature", "marketCapSol": 25.5,
        })
        self.assertIsNotNone(event)
        self.assertEqual(event["mint"], MINT)
        self.assertEqual(event["eventType"], "create")
        self.assertEqual(event["creator"], "CreatorPublicKey")

    def test_accepts_migration_event(self):
        event = _normalize_launch_event({"txType": "complete", "mint": MINT, "pool": "PoolAddress"})
        self.assertEqual(event["eventType"], "complete")
        self.assertEqual(event["pool"], "PoolAddress")

    def test_rejects_invalid_mint_and_non_launch_events(self):
        self.assertIsNone(_normalize_launch_event({"txType": "create", "mint": "bad"}))
        self.assertIsNone(_normalize_launch_event({"txType": "buy", "mint": MINT}))

    def test_normalizes_seconds_and_milliseconds(self):
        with patch("sniper_launch_feed.time.time", return_value=1700000050):
            self.assertEqual(_event_time_ms({"timestamp": 1700000000}), 1700000000000)
            self.assertEqual(_event_time_ms({"timestamp": 1700000000000}), 1700000000000)

    def test_rebases_stale_launch_timestamp_to_receive_time(self):
        with patch("sniper_launch_feed.time.time", return_value=1700000200):
            event = _normalize_launch_event({"txType": "create", "mint": MINT, "timestamp": 1700000000})
        self.assertEqual(event["eventAt"], 1700000200000)

    def test_preserves_recent_launch_timestamp(self):
        with patch("sniper_launch_feed.time.time", return_value=1700000050):
            event = _normalize_launch_event({"txType": "create", "mint": MINT, "timestamp": 1700000000})
        self.assertEqual(event["eventAt"], 1700000000000)

    def test_discovery_module_has_no_paid_trade_or_helius_stream(self):
        from pathlib import Path
        source = Path("sniper_launch_feed.py").read_text(encoding="utf-8")
        self.assertNotIn("logsSubscribe", source)
        self.assertIn("subscribeTokenTrade", source)  # PumpDev only; PumpPortal remains launch-only
        self.assertNotIn("HELIUS_API_KEY", source)
        self.assertNotIn("_helius_trade_worker", source)

    def test_rejects_non_finite_numeric_values(self):
        self.assertIsNone(_safe_number(float("nan")))
        self.assertIsNone(_safe_number(float("inf")))

    def test_paper_engine_is_paused_without_trade_feed(self):
        from pathlib import Path
        source = Path("sniper_launch_feed.py").read_text(encoding="utf-8")
        self.assertNotIn("sniper_paper_engine_tick", source)
        self.assertNotIn("paper_engine_worker", source)
        self.assertIn('"paperEngineEnabled": False', source)
        self.assertIn('"paperEngineStatus": "waiting_for_trade_data_source"', source)


    def test_normalizes_pumpdev_sol_bonding_curve_trade(self):
        event = _normalize_pumpdev_trade_event({
            "txType": "buy", "mint": MINT, "signature": "sig123",
            "quoteMint": "So11111111111111111111111111111111111111112",
            "solAmount": 0.5, "tokenAmount": 1000000,
            "vSolInBondingCurve": 35, "vTokensInBondingCurve": 900000000,
            "marketCapSol": 40.5, "timestamp": 1743782400,
            "traderPublicKey": "TraderWallet",
        })
        self.assertIsNotNone(event)
        self.assertEqual(event["side"], "buy")
        self.assertEqual(event["solAmount"], 0.5)
        self.assertEqual(event["tokenAmount"], 1)
        self.assertAlmostEqual(event["priceSol"], 0.5)
        self.assertEqual(event["vSolInBondingCurve"], 35)
        self.assertEqual(event["vTokensInBondingCurve"], 900)
        self.assertEqual(event["eventAt"], 1743782400000)

    def test_normalizes_pumpswap_using_effective_reserves(self):
        event = _normalize_pumpdev_trade_event({
            "txType": "sell", "mint": MINT, "signature": "sig456",
            "quoteMint": "So11111111111111111111111111111111111111112",
            "solAmount": 0.2, "tokenAmount": 400000,
            "source": "pumpswap", "poolEffectiveQuoteReserves": 85000000000,
            "poolBaseReserves": 206900000000000,
        })
        self.assertIsNotNone(event)
        self.assertEqual(event["tokenAmount"], 0.4)
        self.assertEqual(event["vSolInBondingCurve"], 85)
        self.assertEqual(event["vTokensInBondingCurve"], 206900000)

    def test_rejects_non_sol_or_unresolved_pumpdev_trade(self):
        base = {
            "txType": "buy", "mint": MINT, "signature": "sig789",
            "solAmount": 0.1, "tokenAmount": 1000,
            "vSolInBondingCurve": 30, "vTokensInBondingCurve": 900000000,
        }
        self.assertIsNone(_normalize_pumpdev_trade_event({**base, "quoteMint": "USDC"}))
        self.assertIsNone(_normalize_pumpdev_trade_event({**base, "quoteMint": "So11111111111111111111111111111111111111112", "quoteContextResolved": False}))

    def test_pumpdev_feed_is_opt_in_and_never_uses_trading_endpoint(self):
        from pathlib import Path
        source = Path("sniper_launch_feed.py").read_text(encoding="utf-8")
        self.assertIn("await launch_enabled.wait()", source)
        self.assertIn("PUMPDEV_API_KEY", source)
        self.assertIn("wss://pumpdev.io/ws", source)
        self.assertIn("subscribeNewToken", source)
        self.assertIn("subscribeTokenTrade", source)
        self.assertNotIn("/api/trade", source)
        self.assertIn('"pumpdevStatus": "connected_anonymous"', source)
        self.assertIn('"pumpdevStatus": "connected" if status == "ok" else "connected_free"', source)
        self.assertIn('"paperEngineEnabled": False', source)

    def test_free_tier_request_budget_is_conservative(self):
        from pathlib import Path
        source = Path("sniper_launch_feed.py").read_text(encoding="utf-8")
        self.assertIn("max_subs = 20 if api_key else 4", source)
        self.assertIn("heartbeat=30", source)
        self.assertIn("backoff = 30", source)
        self.assertIn("backoff = 120", source)
        self.assertIn("backoff = min(backoff * 2, 300)", source)
        self.assertIn("backoff = min(backoff * 2, 600)", source)

    def test_pumpdev_worker_waits_for_website_opt_in(self):
        from pathlib import Path
        source = Path("sniper_launch_feed.py").read_text(encoding="utf-8")
        worker = source.split("async def pumpdev_trade_worker()", 1)[1].split("async def worker()", 1)[0]
        self.assertIn("while True:", worker)
        self.assertIn("await launch_enabled.wait()", worker)
        self.assertIn('os.getenv("PUMPDEV_API_KEY", "")', worker)
        self.assertIn("backoff = min(backoff * 2, 300)", worker)

class SniperMonitoringRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_restores_persisted_paper_opt_in(self):
        event = asyncio.Event()
        async def fake_supabase(method, path, *, payload=None):
            self.assertEqual(method, "POST")
            self.assertEqual(path, "rpc/sniper_any_paper_monitoring_enabled")
            return True
        restored = await _restore_paper_monitoring_opt_in(fake_supabase, event)
        self.assertTrue(restored)
        self.assertTrue(event.is_set())

    async def test_keeps_feeds_stopped_when_no_paper_user_enabled(self):
        event = asyncio.Event()
        async def fake_supabase(method, path, *, payload=None):
            return False
        restored = await _restore_paper_monitoring_opt_in(fake_supabase, event)
        self.assertFalse(restored)
        self.assertFalse(event.is_set())

    async def test_fails_closed_when_preference_lookup_fails(self):
        event = asyncio.Event()
        async def fake_supabase(method, path, *, payload=None):
            raise RuntimeError("database unavailable")
        restored = await _restore_paper_monitoring_opt_in(fake_supabase, event)
        self.assertFalse(restored)
        self.assertFalse(event.is_set())


if __name__ == "__main__":
    unittest.main()
