import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from sniper_paper_price_feed import (
    SOL_MINT,
    MAX_MINTS_PER_CYCLE,
    PAPER_POLL_INTERVAL_SECONDS,
    PAPER_GECKO_POOL_BATCH_SIZE,
    PAPER_GECKO_BATCH_INTERVAL_SECONDS,
    normalize_dexscreener_paper_mark,
    normalize_jupiter_paper_mark,
    normalize_geckoterminal_paper_mark,
    normalize_geckoterminal_pool_mark,
    geckoterminal_pool_diagnostics,
    geckoterminal_pools_by_mint,
    classify_gecko_pool_result,
    is_gecko_rate_limit_error,
    normalize_trade_feed_paper_mark,
    paper_price_mark_worker,
    select_gecko_pool_mints,
    select_price_feed_mints,
    _save_price_mark,
    _save_live_price_mark,
    live_price_mark_worker,
)

MINT = "5qErNMKHiYvixh69V1aw8Dazzg6bvUN41ojcozUppump"


def pair(pair_address, base, quote, price_native, liquidity):
    return {
        "chainId": "solana",
        "pairAddress": pair_address,
        "baseToken": {"address": base},
        "quoteToken": {"address": quote},
        "priceNative": str(price_native),
        "liquidity": {"usd": liquidity},
    }


class PaperPriceFeedTests(unittest.TestCase):
    def test_selects_most_liquid_sol_quoted_pair(self):
        result = normalize_dexscreener_paper_mark([
            pair("pair-low", MINT, SOL_MINT, 0.001, 800),
            pair("pair-high", MINT, SOL_MINT, 0.002, 2500),
            pair("pair-usdc", MINT, "USDC_MINT", 0.15, 100000),
        ], MINT, observed_at_ms=123456)
        self.assertEqual(result["pairAddress"], "pair-high")
        self.assertEqual(result["priceSol"], 0.002)
        self.assertEqual(result["liquidityUsd"], 2500)
        self.assertEqual(result["observedAt"], 123456)

    def test_inverts_price_when_sol_is_base_and_token_is_quote(self):
        result = normalize_dexscreener_paper_mark([
            pair("pair-inverse", SOL_MINT, MINT, 500, 3000),
        ], MINT)
        self.assertAlmostEqual(result["priceSol"], 0.002)

    def test_rejects_low_liquidity_and_non_solana_pairs(self):
        result = normalize_dexscreener_paper_mark([
            pair("pair-low", MINT, SOL_MINT, 0.001, 499.99),
            {**pair("pair-eth", MINT, SOL_MINT, 0.001, 10000), "chainId": "ethereum"},
        ], MINT)
        self.assertIsNone(result)

    def test_rejects_non_sol_quote_pairs_and_invalid_prices(self):
        result = normalize_dexscreener_paper_mark([
            pair("pair-usdc", MINT, "USDC_MINT", 0.15, 100000),
            pair("pair-zero", MINT, SOL_MINT, 0, 100000),
        ], MINT)
        self.assertIsNone(result)

    def test_jupiter_price_is_converted_from_usd_to_sol(self):
        prices = {
            SOL_MINT: {"usdPrice": 150.0, "liquidity": 1000000},
            MINT: {"usdPrice": 0.00003, "liquidity": 1200},
        }
        mark = normalize_jupiter_paper_mark(prices, MINT, observed_at_ms=123456)
        self.assertEqual(mark["source"], "jupiter")
        self.assertAlmostEqual(mark["priceSol"], 0.0000002)
        self.assertEqual(mark["liquidityUsd"], 1200)
        self.assertEqual(mark["pairAddress"], "jupiter-price-v3")
        self.assertEqual(mark["observedAt"], 123456)

    def test_jupiter_omitted_or_low_liquidity_token_has_no_mark(self):
        self.assertIsNone(normalize_jupiter_paper_mark(
            {SOL_MINT: {"usdPrice": 150}}, MINT
        ))
        self.assertIsNone(normalize_jupiter_paper_mark({
            SOL_MINT: {"usdPrice": 150},
            MINT: {"usdPrice": 0.00003, "liquidity": 499.99},
        }, MINT))

    def test_geckoterminal_batch_price_converts_usd_to_sol(self):
        payload = {
            "data": {
                "attributes": {
                    "token_prices": {
                        SOL_MINT: "150",
                        MINT: "0.00003",
                    },
                    "total_reserve_in_usd": {
                        MINT: "1200",
                    },
                }
            }
        }
        mark = normalize_geckoterminal_paper_mark(payload, MINT, observed_at_ms=123456)
        self.assertEqual(mark["source"], "geckoterminal")
        self.assertAlmostEqual(mark["priceSol"], 0.0000002)
        self.assertEqual(mark["liquidityUsd"], 1200)
        self.assertEqual(mark["pairAddress"], "geckoterminal-simple-price")
        self.assertEqual(mark["observedAt"], 123456)

    def test_geckoterminal_rejects_missing_sol_price_or_low_reserve(self):
        self.assertIsNone(normalize_geckoterminal_paper_mark({
            "data": {"attributes": {"token_prices": {MINT: "0.00003"},
                                    "total_reserve_in_usd": {MINT: "1200"}}}
        }, MINT))
        self.assertIsNone(normalize_geckoterminal_paper_mark({
            "data": {"attributes": {"token_prices": {SOL_MINT: "150", MINT: "0.00003"},
                                    "total_reserve_in_usd": {MINT: "499.99"}}}
        }, MINT))

    def test_geckoterminal_token_pools_selects_highest_liquidity_pool(self):
        payload = {
            "data": [
                {
                    "attributes": {
                        "address": "11111111111111111111111111111111",
                        "base_token_price_native_currency": "0.000001",
                        "quote_token_price_native_currency": "12",
                        "reserve_in_usd": "800",
                    },
                    "relationships": {
                        "base_token": {"data": {"id": "solana_" + MINT}},
                        "quote_token": {"data": {"id": "solana_" + SOL_MINT}},
                    },
                },
                {
                    "attributes": {
                        "address": "22222222222222222222222222222222",
                        "base_token_price_native_currency": "0.000002",
                        "quote_token_price_native_currency": "8",
                        "reserve_in_usd": "2500",
                    },
                    "relationships": {
                        "base_token": {"data": {"id": "solana_" + MINT}},
                        "quote_token": {"data": {"id": "solana_" + SOL_MINT}},
                    },
                },
            ]
        }
        mark = normalize_geckoterminal_pool_mark(payload, MINT, observed_at_ms=123456)
        self.assertEqual(mark["source"], "geckoterminal")
        self.assertEqual(mark["pairAddress"], "22222222222222222222222222222222")
        self.assertEqual(mark["priceSol"], 0.000002)
        self.assertEqual(mark["liquidityUsd"], 2500)
        self.assertEqual(mark["observedAt"], 123456)

    def test_geckoterminal_token_pools_supports_token_as_quote(self):
        payload = {
            "data": [{
                "attributes": {
                    "address": "33333333333333333333333333333333",
                    "base_token_price_native_currency": "12",
                    "quote_token_price_native_currency": "0.000003",
                    "reserve_in_usd": "1000",
                },
                "relationships": {
                    "base_token": {"data": {"id": "solana_" + SOL_MINT}},
                    "quote_token": {"data": {"id": "solana_" + MINT}},
                },
            }]
        }
        mark = normalize_geckoterminal_pool_mark(payload, MINT)
        self.assertEqual(mark["priceSol"], 0.000003)

    def test_geckoterminal_token_pools_rejects_wrong_token_and_low_reserve(self):
        payload = {
            "data": [
                {
                    "attributes": {"address": "44444444444444444444444444444444",
                                   "base_token_price_native_currency": "0.000001",
                                   "reserve_in_usd": "5000"},
                    "relationships": {
                        "base_token": {"data": {"id": "solana_OtherToken"}},
                        "quote_token": {"data": {"id": "solana_" + SOL_MINT}},
                    },
                },
                {
                    "attributes": {"address": "11111111111111111111111111111111",
                                   "base_token_price_native_currency": "0.000001",
                                   "reserve_in_usd": "499.99"},
                    "relationships": {
                        "base_token": {"data": {"id": "solana_" + MINT}},
                        "quote_token": {"data": {"id": "solana_" + SOL_MINT}},
                    },
                },
            ]
        }
        self.assertIsNone(normalize_geckoterminal_pool_mark(payload, MINT))

    def test_geckoterminal_pool_diagnostics_report_matching_pools_and_liquidity(self):
        payload = {
            "data": [
                {
                    "attributes": {"reserve_in_usd": "250"},
                    "relationships": {
                        "base_token": {"data": {"id": "solana_" + MINT}},
                        "quote_token": {"data": {"id": "solana_" + SOL_MINT}},
                    },
                },
                {
                    "attributes": {"reserve_in_usd": "9000"},
                    "relationships": {
                        "base_token": {"data": {"id": "solana_OtherToken"}},
                        "quote_token": {"data": {"id": "solana_" + SOL_MINT}},
                    },
                },
                {
                    "attributes": {"reserve_in_usd": "1200"},
                    "relationships": {
                        "base_token": {"data": {"id": "solana_" + SOL_MINT}},
                        "quote_token": {"data": {"id": "solana_" + MINT}},
                    },
                },
            ]
        }
        self.assertEqual(geckoterminal_pool_diagnostics(payload, MINT), {
            "pool_count": 3,
            "matching_pool_count": 2,
            "max_liquidity_usd": 1200.0,
        })
        self.assertEqual(geckoterminal_pool_diagnostics({"data": []}, MINT), {
            "pool_count": 0,
            "matching_pool_count": 0,
            "max_liquidity_usd": None,
        })

    def test_gecko_batch_uses_public_multi_token_limit_and_cache_cadence(self):
        self.assertEqual(PAPER_GECKO_POOL_BATCH_SIZE, 30)
        self.assertEqual(PAPER_GECKO_BATCH_INTERVAL_SECONDS, 60.0)

    def test_geckoterminal_multi_token_response_splits_top_pools_by_mint(self):
        mint_a = "5qErNMKHiYvixh69V1aw8Dazzg6bvUN41ojcozUppump"
        mint_b = "6qErNMKHiYvixh69V1aw8Dazzg6bvUN41ojcozUppump"
        pool_a = {
            "id": "solana_pool_a",
            "type": "pool",
            "attributes": {
                "address": "7qErNMKHiYvixh69V1aw8Dazzg6bvUN41ojcozUppump",
                "base_token_price_native_currency": "0.01",
                "reserve_in_usd": "1200",
            },
            "relationships": {
                "base_token": {"data": {"id": f"solana_{mint_a}", "type": "token"}},
                "quote_token": {"data": {"id": f"solana_{SOL_MINT}", "type": "token"}},
            },
        }
        pool_b = {
            "id": "solana_pool_b",
            "type": "pool",
            "attributes": {
                "address": "8qErNMKHiYvixh69V1aw8Dazzg6bvUN41ojcozUppump",
                "quote_token_price_native_currency": "0.02",
                "reserve_in_usd": "900",
            },
            "relationships": {
                "base_token": {"data": {"id": f"solana_{SOL_MINT}", "type": "token"}},
                "quote_token": {"data": {"id": f"solana_{mint_b}", "type": "token"}},
            },
        }
        payload = {
            "data": [
                {"attributes": {"address": mint_a}, "relationships": {"top_pools": {"data": [{"id": "solana_pool_a"}]}}},
                {"attributes": {"address": mint_b}, "relationships": {"top_pools": {"data": [{"id": "solana_pool_b"}]}}},
            ],
            "included": [pool_a, pool_b],
        }
        split = geckoterminal_pools_by_mint(payload, [mint_a, mint_b])
        self.assertEqual(split[mint_a]["data"], [pool_a])
        self.assertEqual(split[mint_b]["data"], [pool_b])
        self.assertIsNotNone(normalize_geckoterminal_pool_mark(split[mint_a], mint_a))
        self.assertIsNotNone(normalize_geckoterminal_pool_mark(split[mint_b], mint_b))
        self.assertEqual(geckoterminal_pools_by_mint({}, [mint_a])[mint_a], {"data": []})

    def test_gecko_rate_limit_detection_is_specific(self):
        self.assertTrue(is_gecko_rate_limit_error(RuntimeError("GeckoTerminal token pools HTTP 429")))
        self.assertFalse(is_gecko_rate_limit_error(RuntimeError("GeckoTerminal token pools HTTP 500")))
        self.assertFalse(is_gecko_rate_limit_error(RuntimeError("connection timeout")))

    def test_gecko_pool_result_classifies_no_pool_low_liquidity_and_rejected_mark(self):
        self.assertEqual(classify_gecko_pool_result({
            "matching_pool_count": 0, "max_liquidity_usd": None,
        }), "no_pool")
        self.assertEqual(classify_gecko_pool_result({
            "matching_pool_count": 2, "max_liquidity_usd": 499.99,
        }), "below_liquidity_threshold")
        self.assertEqual(classify_gecko_pool_result({
            "matching_pool_count": 1, "max_liquidity_usd": 800,
        }), "pool_mark_rejected")
        self.assertEqual(classify_gecko_pool_result({
            "matching_pool_count": 1, "max_liquidity_usd": None,
        }), "pool_mark_rejected")

    def test_trade_feed_mark_accepts_recent_valid_sol_trade(self):
        now_ms = 1_800_000_000_000
        mark = normalize_trade_feed_paper_mark({
            "mint": MINT, "source": "shrine", "priceSol": "0.000002",
            "eventAt": now_ms - 10_000, "side": "buy",
        }, MINT, now_ms=now_ms)
        self.assertEqual(mark["source"], "trade_feed")
        self.assertEqual(mark["priceSol"], 0.000002)
        self.assertEqual(mark["liquidityUsd"], 0)
        self.assertEqual(mark["pairAddress"], "trade-feed:shrine:buy")
        self.assertEqual(mark["observedAt"], now_ms - 10_000)

    def test_trade_feed_mark_rejects_stale_invalid_or_untrusted_trade(self):
        now_ms = 1_800_000_000_000
        base = {"mint": MINT, "source": "shrine", "priceSol": 0.000002,
                "eventAt": now_ms - 10_000, "side": "buy"}
        self.assertIsNone(normalize_trade_feed_paper_mark(
            {**base, "eventAt": now_ms - 120_001}, MINT, now_ms=now_ms))
        self.assertIsNone(normalize_trade_feed_paper_mark(
            {**base, "priceSol": 0}, MINT, now_ms=now_ms))
        self.assertIsNone(normalize_trade_feed_paper_mark(
            {**base, "source": "legacy_unattributed"}, MINT, now_ms=now_ms))
        self.assertIsNone(normalize_trade_feed_paper_mark(
            {**base, "mint": "OtherMint"}, MINT, now_ms=now_ms))
        self.assertIsNone(normalize_trade_feed_paper_mark(
            {**base, "side": "unknown"}, MINT, now_ms=now_ms))

    def test_rejects_invalid_mint_or_missing_pair_address(self):
        self.assertIsNone(normalize_dexscreener_paper_mark([], "not-a-mint"))
        result = normalize_dexscreener_paper_mark([
            pair("", MINT, SOL_MINT, 0.001, 1000),
        ], MINT)
        self.assertIsNone(result)


class PriceFeedMintSelectionTests(unittest.TestCase):
    def test_more_than_cycle_limit_rotates_without_starvation(self):
        mints = [f"mint-{index}" for index in range(25)]
        first, offset = select_price_feed_mints(mints, offset=0, limit=20)
        second, next_offset = select_price_feed_mints(mints, offset=offset, limit=20)
        self.assertEqual(first, mints[:20])
        self.assertEqual(second, mints[20:] + mints[:15])
        self.assertEqual(set(mints), set(first) | set(second))
        self.assertEqual(offset, 20)
        self.assertEqual(next_offset, 15)

    def test_empty_and_small_sets(self):
        self.assertEqual(select_price_feed_mints([]), ([], 0))
        self.assertEqual(select_price_feed_mints(["a", "b"], offset=1), (["b", "a"], 1))


class GeckoPoolSelectionTests(unittest.TestCase):
    def test_one_mint_is_selected_once(self):
        selected, next_offset = select_gecko_pool_mints(["a"], offset=0, limit=4)
        self.assertEqual(selected, ["a"])
        self.assertEqual(next_offset, 0)

    def test_two_mints_are_selected_once_each(self):
        selected, next_offset = select_gecko_pool_mints(["a", "b"], offset=0, limit=4)
        self.assertEqual(selected, ["a", "b"])
        self.assertEqual(len(selected), len(set(selected)))
        self.assertEqual(next_offset, 0)

    def test_three_mints_are_selected_once_each(self):
        selected, next_offset = select_gecko_pool_mints(["a", "b", "c"], offset=1, limit=4)
        self.assertEqual(selected, ["b", "c", "a"])
        self.assertEqual(len(selected), len(set(selected)))
        self.assertEqual(next_offset, 1)

    def test_five_mints_rotate_within_four_lookup_limit(self):
        mints = ["a", "b", "c", "d", "e"]
        first, next_offset = select_gecko_pool_mints(mints, offset=0, limit=4)
        second, next_offset2 = select_gecko_pool_mints(mints, offset=next_offset, limit=4)
        self.assertEqual(first, ["a", "b", "c", "d"])
        self.assertEqual(second, ["e", "a", "b", "c"])
        self.assertEqual(len(first), len(set(first)))
        self.assertEqual(len(second), len(set(second)))
        self.assertEqual(set(mints), set(first) | set(second))
        self.assertEqual(next_offset, 4)
        self.assertEqual(next_offset2, 3)

    def test_empty_input_returns_no_requests(self):
        self.assertEqual(select_gecko_pool_mints([], offset=0, limit=4), ([], 0))


class PaperPriceFeedWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_open_mints_through_service_role_rpc(self):
        calls = []

        async def supabase_request(method, path, *, payload=None, params=None):
            calls.append((method, path, payload, params))
            return []

        state = {}
        with patch("sniper_paper_price_feed.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await paper_price_mark_worker(supabase_request, state)

        self.assertEqual(calls, [
            ("POST", "rpc/sniper_open_paper_position_mints", {}, None),
        ])
        self.assertNotIn("rpc/sniper_open_live_position_mints", [call[1] for call in calls])
        self.assertEqual(state["paperPriceFeedStatus"], "waiting_for_open_positions")

    async def test_refreshes_paper_mints_with_batched_dexscreener(self):
        self.assertEqual(MAX_MINTS_PER_CYCLE, 60)
        self.assertEqual(PAPER_POLL_INTERVAL_SECONDS, 30.0)
        self.assertEqual(PAPER_GECKO_POOL_BATCH_SIZE, 30)
        calls = []

        async def supabase_request(method, path, *, payload=None, params=None):
            calls.append((method, path, payload))
            if path == "rpc/sniper_open_paper_position_mints":
                return [{"mint": MINT}]
            if path == "rpc/sniper_paper_price_mark_upsert":
                return {"ok": True}
            raise AssertionError("Unexpected RPC: " + path)

        class FakeSession:
            async def __aenter__(self):
                return self
            async def __aexit__(self, exc_type, exc, tb):
                return False

        async def fake_batch_fetch(session, mints):
            self.assertEqual(mints, [MINT])
            return {MINT: [pair("paper-batch-pair", MINT, SOL_MINT, 0.0002, 1200)]}

        state = {}
        with patch("sniper_paper_price_feed.ClientSession", return_value=FakeSession()):
            with patch("sniper_paper_price_feed._fetch_token_pairs_batch", new=AsyncMock(side_effect=fake_batch_fetch)):
                with patch("sniper_paper_price_feed.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
                    with self.assertRaises(asyncio.CancelledError):
                        await paper_price_mark_worker(supabase_request, state)

        self.assertIn("rpc/sniper_paper_price_mark_upsert", [call[1] for call in calls])
        self.assertNotIn("rpc/sniper_live_position_mark", [call[1] for call in calls])
        self.assertEqual(state["paperPriceFeedStatus"], "active")
        self.assertEqual(state["paperPriceFeedUpdated"], 1)


class LivePositionMarkTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_price_mark_updates_only_live_position_rpc(self):
        calls = []
        async def supabase_request(method, path, *, payload=None, params=None):
            calls.append((method, path, payload))
            self.assertEqual(method, "POST")
            self.assertEqual(path, "rpc/sniper_live_position_mark")
            self.assertEqual(payload["p_mint"], MINT)
            self.assertEqual(payload["p_price_sol"], 0.0002)
            self.assertTrue(payload["p_observed_at"].startswith("2026-09-09T"))
            return 2
        mark = {"mint": MINT, "source": "dexscreener", "priceSol": 0.0002,
                "liquidityUsd": 1000, "pairAddress": "pair", "observedAt": 1788976012000}
        updated = await _save_live_price_mark(supabase_request, mark)
        self.assertEqual(updated, 2)
        self.assertEqual([call[1] for call in calls], ["rpc/sniper_live_position_mark"])

    async def test_live_price_worker_reads_live_positions_only(self):
        calls = []
        async def supabase_request(method, path, *, payload=None, params=None):
            calls.append((method, path, payload, params))
            return []
        state = {}
        with patch("sniper_paper_price_feed.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await live_price_mark_worker(supabase_request, state)
        self.assertEqual(calls, [
            ("POST", "rpc/sniper_open_live_position_mints", {}, None),
        ])
        self.assertNotIn("rpc/sniper_open_paper_position_mints", [call[1] for call in calls])
        self.assertEqual(state["livePriceFeedStatus"], "waiting_for_open_positions")


    async def test_price_mark_persists_to_paper_only_without_live_position_rpc(self):
        calls = []
        async def supabase_request(method, path, *, payload=None, params=None):
            calls.append((method, path, payload))
            if path == "rpc/sniper_paper_price_mark_upsert":
                return {"ok": True}
            raise AssertionError("Unexpected RPC: " + path)
        mark = {"mint": MINT, "source": "dexscreener", "priceSol": 0.0002,
                "liquidityUsd": 1000, "pairAddress": "pair", "observedAt": 1788976012000}
        saved, live_updated = await _save_price_mark(supabase_request, mark)
        self.assertTrue(saved)
        self.assertFalse(live_updated)
        self.assertEqual([call[1] for call in calls], ["rpc/sniper_paper_price_mark_upsert"])

    async def test_rejected_paper_mark_stops_without_any_live_position_rpc(self):
        calls = []
        async def supabase_request(method, path, *, payload=None, params=None):
            calls.append(path)
            return {"ok": False}
        mark = {"mint": MINT, "priceSol": 0.0002, "observedAt": 1788976012000}
        saved, live_updated = await _save_price_mark(supabase_request, mark)
        self.assertFalse(saved)
        self.assertFalse(live_updated)
        self.assertEqual(calls, ["rpc/sniper_paper_price_mark_upsert"])


if __name__ == "__main__":
    unittest.main()
