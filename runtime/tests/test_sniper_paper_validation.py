import asyncio
import time
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import api_server

from api_server import _sniper_candidate_from_pair, _sniper_interleave_candidates, _sniper_parse_paper_position


MINT = "5qErNMKHiYvixh69V1aw8Dazzg6bvUN41ojcozUppump"


def make_payload(**overrides):
    position = {
        "id": str(uuid.uuid4()),
        "address": MINT,
        "symbol": "TEST",
        "name": "Paper Test Token",
        "buySol": 0.01,
        "entryPrice": 0.000001,
        "lastPrice": 0.000001,
        "quantity": 9950,
        "netPnl": 0,
        "netPct": 0,
        "status": "open",
        "exitReason": None,
        "openedAt": int(time.time() * 1000),
        "closedAt": None,
    }
    position.update(overrides)
    return {"position": position}


class SniperPaperValidationTests(unittest.TestCase):
    def test_all_source_interleaving_does_not_starve_boost_or_community(self):
        candidates = (
            [{"source": "pumpportal", "mint": f"pump-{i}"} for i in range(5)]
            + [{"source": "boosts", "mint": "boost-1"}]
            + [{"source": "community", "mint": "community-1"}]
        )
        result = _sniper_interleave_candidates(candidates)
        self.assertEqual([item['source'] for item in result[:3]], ['pumpportal', 'boosts', 'community'])
        self.assertEqual(len(result), len(candidates))

    def test_interleaving_preserves_order_within_each_source(self):
        candidates = (
            [{"source": "pumpportal", "mint": "p1"}, {"source": "pumpportal", "mint": "p2"}]
            + [{"source": "boosts", "mint": "b1"}]
        )
        result = _sniper_interleave_candidates(candidates)
        self.assertEqual([item['mint'] for item in result], ['p1', 'b1', 'p2'])

    def test_socials_are_unknown_without_a_resolved_pair(self):
        result = _sniper_candidate_from_pair(MINT, None, "pumpportal")
        self.assertIsNone(result["hasSocials"])

    def test_socials_are_false_when_pair_metadata_has_no_links(self):
        result = _sniper_candidate_from_pair(
            MINT, {"baseToken": {"name": "Test"}, "info": {"socials": [], "websites": []}}, "pumpportal"
        )
        self.assertFalse(result["hasSocials"])

    def test_socials_are_true_when_pair_metadata_has_a_link(self):
        result = _sniper_candidate_from_pair(
            MINT, {"baseToken": {"name": "Test"}, "info": {"socials": [{"type": "twitter", "url": "https://x.com/test"}]}}, "pumpportal"
        )
        self.assertTrue(result["hasSocials"])

    def test_accepts_valid_open_paper_position(self):
        result = _sniper_parse_paper_position(make_payload())
        self.assertEqual(result["status"], "open")
        self.assertEqual(result["buySol"], 0.01)
        self.assertEqual(result["mint"], MINT)

    def test_accepts_paper_buy_above_old_point_zero_five_sol_cap(self):
        result = _sniper_parse_paper_position(make_payload(buySol=1.25))
        self.assertEqual(result["buySol"], 1.25)

    def test_rejects_paper_buy_above_supported_virtual_capital_range(self):
        with self.assertRaises(ValueError):
            _sniper_parse_paper_position(make_payload(buySol=1000.0001))

    def test_rejects_invalid_solana_mint(self):
        with self.assertRaises(ValueError):
            _sniper_parse_paper_position(make_payload(address="not-a-solana-mint"))

    def test_rejects_invalid_position_uuid(self):
        with self.assertRaises(ValueError):
            _sniper_parse_paper_position(make_payload(id="paper-1"))

    def test_rejects_non_finite_amount(self):
        with self.assertRaises(ValueError):
            _sniper_parse_paper_position(make_payload(buySol=float("nan")))

    def test_rejects_live_status(self):
        with self.assertRaises(ValueError):
            _sniper_parse_paper_position(make_payload(status="live"))

    def test_rejects_exit_reason_on_open_position(self):
        with self.assertRaises(ValueError):
            _sniper_parse_paper_position(make_payload(exitReason="target"))

    def test_accepts_stagnant_exit_reason(self):
        result = _sniper_parse_paper_position(
            make_payload(status="closed", exitReason="stagnant", closedAt=int(time.time() * 1000))
        )
        self.assertEqual(result["status"], "closed")
        self.assertEqual(result["exitReason"], "stagnant")

    def test_closed_position_gets_manual_reason_and_timestamp(self):
        result = _sniper_parse_paper_position(
            make_payload(status="closed", closedAt=int(time.time() * 1000))
        )
        self.assertEqual(result["status"], "closed")
        self.assertEqual(result["exitReason"], "manual")
        self.assertIsNotNone(result["closedAt"])
        self.assertEqual(result["sellSol"], 0)


class SniperDemoPaperIsolationApiTests(unittest.TestCase):
    class Request:
        headers = {"Origin": api_server.ALLOWED_ORIGIN}

        def __init__(self, payload=None):
            self.payload = payload

        async def json(self):
            return self.payload

    def _run(self, coroutine):
        return asyncio.run(coroutine)

    def test_list_uses_demo_rpc_not_engine_rpc(self):
        request = self.Request()
        with (patch.object(api_server, "_read_survivor_session", return_value=123),
              patch.object(api_server, "_supabase_request", new=AsyncMock(return_value=[])) as rpc):
            response = self._run(api_server.sniper_paper_list(request))
        self.assertEqual(response.status, 200)
        self.assertEqual(rpc.await_args.args[:2], ("POST", "rpc/sniper_demo_paper_list"))

    def test_save_uses_demo_rpc_not_engine_rpc(self):
        request = self.Request(make_payload())
        with (patch.object(api_server, "_read_survivor_session", return_value=123),
              patch.object(api_server, "_supabase_request", new=AsyncMock(return_value={"id": "demo"})) as rpc):
            response = self._run(api_server.sniper_paper_save(request))
        self.assertEqual(response.status, 200)
        self.assertEqual(rpc.await_args.args[:2], ("POST", "rpc/sniper_demo_paper_save"))

    def test_clear_uses_demo_rpc_not_engine_rpc(self):
        request = self.Request()
        with (patch.object(api_server, "_read_survivor_session", return_value=123),
              patch.object(api_server, "_supabase_request", new=AsyncMock(return_value=0)) as rpc):
            response = self._run(api_server.sniper_paper_clear(request))
        self.assertEqual(response.status, 200)
        self.assertEqual(rpc.await_args.args[:2], ("POST", "rpc/sniper_demo_paper_clear"))


if __name__ == "__main__":
    unittest.main()
