import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from sniper_paper_price_feed import (
    SOL_MINT,
    normalize_dexscreener_paper_mark,
    normalize_jupiter_paper_mark,
    normalize_geckoterminal_paper_mark,
    paper_price_mark_worker,
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

    def test_rejects_invalid_mint_or_missing_pair_address(self):
        self.assertIsNone(normalize_dexscreener_paper_mark([], "not-a-mint"))
        result = normalize_dexscreener_paper_mark([
            pair("", MINT, SOL_MINT, 0.001, 1000),
        ], MINT)
        self.assertIsNone(result)


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

        self.assertEqual(calls, [("POST", "rpc/sniper_open_paper_position_mints", {}, None)])
        self.assertEqual(state["paperPriceFeedStatus"], "waiting_for_open_positions")


if __name__ == "__main__":
    unittest.main()
