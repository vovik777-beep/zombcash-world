import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sniper_live_engine import (
    LIVE_ENTRY_DATABASE_CHECKS,
    LIVE_EXIT_DATABASE_CHECKS,
    _live_database_missing_checks,
    live_entry_worker,
    live_exit_worker,
    process_live_entry_candidates,
    process_live_exit_positions,
)


MINT = "5qErNMKHiYvixh69V1aw8Dazzg6bvUN41ojcozUppump"
NOW = datetime(2026, 11, 28, 20, 0, tzinfo=timezone.utc)


class LiveEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_readiness_requires_all_entry_migrations(self):
        async def request(method, path, payload=None):
            self.assertEqual(path, "rpc/sniper_live_engine_health")
            return {"ready": False, "checks": {
                name: name not in {"entry_buy_count_filter", "entry_social_filter"}
                for name in LIVE_ENTRY_DATABASE_CHECKS
            }}
        missing = await _live_database_missing_checks(request, LIVE_ENTRY_DATABASE_CHECKS)
        self.assertEqual(missing, ["entry_buy_count_filter", "entry_social_filter"])

    async def test_exit_readiness_does_not_depend_on_entry_filters(self):
        async def request(method, path, payload=None):
            return {"ready": False, "checks": {
                name: name not in {"entry_buy_count_filter", "entry_social_filter"}
                for name in (*LIVE_ENTRY_DATABASE_CHECKS, *LIVE_EXIT_DATABASE_CHECKS)
            }}
        missing = await _live_database_missing_checks(request, LIVE_EXIT_DATABASE_CHECKS)
        self.assertEqual(missing, [])

    async def test_live_entry_worker_stays_locked_when_migrations_are_missing(self):
        state = {}
        async def request(method, path, payload=None):
            self.assertEqual(path, "rpc/sniper_live_engine_health")
            return {"ready": False, "checks": {}}
        with patch("sniper_live_engine.live_execution_ready", return_value=True), \
             patch("sniper_live_engine.live_execution_enabled", return_value=True), \
             patch("sniper_live_engine.asyncio.sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await live_entry_worker(request, state)
        self.assertEqual(state["liveEntryStatus"], "locked_database_migrations")
        self.assertTrue(state["liveEntryMissingDatabaseChecks"])

    async def test_live_exit_worker_requires_exit_migration_but_not_entry_filters(self):
        state = {}
        async def request(method, path, payload=None):
            if path == "rpc/sniper_live_engine_health":
                checks = {name: True for name in LIVE_EXIT_DATABASE_CHECKS}
                checks.update({"entry_buy_count_filter": False, "entry_social_filter": False})
                return {"ready": False, "checks": checks}
            if path == "rpc/sniper_live_open_positions":
                return []
            raise AssertionError(path)
        with patch("sniper_live_engine.live_execution_ready", return_value=True), \
             patch("sniper_live_engine.live_execution_enabled", return_value=True), \
             patch("sniper_live_engine.asyncio.sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await live_exit_worker(request, state)
        self.assertEqual(state["liveExitStatus"], "active")
        self.assertEqual(state["liveExitMissingDatabaseChecks"], [])

    async def test_entry_candidates_use_deterministic_order_id(self):
        calls = []
        async def request(method, path, payload=None):
            self.assertEqual(path, "rpc/sniper_live_entry_candidates")
            return [{
                "telegram_id": 7, "mint": MINT, "launch_at": "2026-11-28T19:59:00+00:00",
                "max_trade_sol": 0.02, "max_slippage_bps": 300,
            }]
        async def submit_order(**kwargs):
            calls.append(kwargs)
            return {"status": "confirmed"}
        first = await process_live_entry_candidates(request, submit_order=submit_order)
        second = await process_live_entry_candidates(request, submit_order=submit_order)
        self.assertEqual(first["confirmed"], 1)
        self.assertEqual(second["confirmed"], 1)
        self.assertEqual(calls[0]["client_order_id"], calls[1]["client_order_id"])
        self.assertEqual(calls[0]["side"], "buy")
        self.assertEqual(calls[0]["amount"], 0.02)

    async def test_exit_worker_triggers_take_profit(self):
        calls = []
        async def request(method, path, payload=None):
            self.assertEqual(path, "rpc/sniper_live_open_positions")
            return [{
                "position_id": "11111111-1111-4111-8111-111111111111",
                "telegram_id": 7, "mint": MINT, "quantity": "1000",
                "net_pnl_pct": "42", "take_profit_pct": "40", "stop_loss_pct": "20",
                "idle_exit_seconds": 3600, "last_mark_at": NOW.isoformat(),
                "last_movement_at": (NOW - timedelta(minutes=1)).isoformat(),
                "last_exit_order_id": None,
            }]
        async def submit_order(**kwargs):
            calls.append(kwargs)
            return {"status": "confirmed"}
        result = await process_live_exit_positions(request, submit_order=submit_order, now=NOW)
        self.assertEqual(result["triggered"], 1)
        self.assertEqual(calls[0]["side"], "sell")
        self.assertEqual(calls[0]["amount"], 1000.0)

    async def test_exit_worker_records_failed_order_and_waits_for_retry_cooldown(self):
        calls = []
        async def request(method, path, payload=None):
            calls.append((path, payload))
            if path == "rpc/sniper_live_open_positions":
                return [{
                    "position_id": "11111111-1111-4111-8111-111111111111",
                    "telegram_id": 7, "mint": MINT, "quantity": "1000",
                    "net_pnl_pct": "42", "take_profit_pct": "40", "stop_loss_pct": "20",
                    "idle_exit_seconds": 3600, "last_mark_at": NOW.isoformat(),
                    "last_exit_order_id": None,
                }]
            if path == "rpc/sniper_live_position_note_exit_attempt":
                self.assertEqual(payload, {
                    "p_position_id": "11111111-1111-4111-8111-111111111111",
                    "p_order_id": "22222222-2222-4222-8222-222222222222",
                })
                return True
            raise AssertionError(path)

        async def submit_order(**kwargs):
            return {"status": "failed", "orderId": "22222222-2222-4222-8222-222222222222"}

        result = await process_live_exit_positions(request, submit_order=submit_order, now=NOW)
        self.assertEqual(result["triggered"], 1)
        self.assertIn("rpc/sniper_live_position_note_exit_attempt", [call[0] for call in calls])

    async def test_exit_worker_waits_for_failed_order_cooldown(self):
        calls = []
        async def request(method, path, payload=None):
            calls.append(path)
            if path == "rpc/sniper_live_open_positions":
                return [{
                    "position_id": "11111111-1111-4111-8111-111111111111",
                    "telegram_id": 7, "mint": MINT, "quantity": "1000",
                    "net_pnl_pct": "42", "take_profit_pct": "40", "stop_loss_pct": "20",
                    "idle_exit_seconds": 3600, "last_mark_at": NOW.isoformat(),
                    "last_exit_order_id": "22222222-2222-4222-8222-222222222222",
                }]
            if path == "rpc/sniper_live_exit_retry_allowed":
                return False
            raise AssertionError(path)

        async def submit_order(**kwargs):
            raise AssertionError("failed exit must respect its cooldown")

        result = await process_live_exit_positions(request, submit_order=submit_order, now=NOW)
        self.assertEqual(result["triggered"], 0)
        self.assertIn("rpc/sniper_live_exit_retry_allowed", calls)

    async def test_exit_worker_skips_stale_marks(self):
        async def request(method, path, payload=None):
            return [{
                "position_id": "11111111-1111-4111-8111-111111111111",
                "telegram_id": 7, "mint": MINT, "quantity": "1000",
                "net_pnl_pct": "42", "take_profit_pct": "40", "stop_loss_pct": "20",
                "idle_exit_seconds": 3600, "last_mark_at": (NOW - timedelta(seconds=120)).isoformat(),
            }]
        async def submit_order(**kwargs):
            raise AssertionError("stale marks must not trigger an exit")
        result = await process_live_exit_positions(request, submit_order=submit_order, now=NOW)
        self.assertEqual(result["stale"], 1)
        self.assertEqual(result["triggered"], 0)

    async def test_exit_worker_triggers_stagnation_exit(self):
        calls = []
        async def request(method, path, payload=None):
            return [{
                "position_id": "11111111-1111-4111-8111-111111111111",
                "telegram_id": 7, "mint": MINT, "quantity": "1000",
                "net_pnl_pct": "1", "take_profit_pct": "40", "stop_loss_pct": "20",
                "idle_exit_seconds": 600, "last_mark_at": NOW.isoformat(),
                "last_movement_at": (NOW - timedelta(seconds=601)).isoformat(),
            }]
        async def submit_order(**kwargs):
            calls.append(kwargs)
            return {"status": "confirmed"}
        result = await process_live_exit_positions(request, submit_order=submit_order, now=NOW)
        self.assertEqual(result["triggered"], 1)
        self.assertEqual(calls[0]["side"], "sell")


if __name__ == "__main__":
    unittest.main()
