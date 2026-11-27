import unittest

from sniper_paper_price_feed import SOL_MINT, normalize_dexscreener_paper_mark

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

    def test_rejects_invalid_mint_or_missing_pair_address(self):
        self.assertIsNone(normalize_dexscreener_paper_mark([], "not-a-mint"))
        result = normalize_dexscreener_paper_mark([
            pair("", MINT, SOL_MINT, 0.001, 1000),
        ], MINT)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
