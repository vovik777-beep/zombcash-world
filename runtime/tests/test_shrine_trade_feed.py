import unittest

from shrine_trade_feed import normalize_shrine_trade

MINT = "5qErNMKHiYvixh69V1aw8Dazzg6bvUN41ojcozUppump"
SOL = "So11111111111111111111111111111111111111112"


class ShrineTradeNormalizationTests(unittest.TestCase):
    def test_normalizes_documented_sol_buy(self):
        event = normalize_shrine_trade({
            "mint": MINT,
            "signature": "shrine-signature",
            "is_buy": True,
            "token_amount": 31952.74,
            "quote_amount": 0.0161,
            "sol_amount": 0.0161,
            "price": 0.000000504,
            "quote": SOL,
            "wallet": "TraderWallet",
            "timestamp": 1788976012,
        })
        self.assertIsNotNone(event)
        self.assertEqual(event["source"], "shrine")
        self.assertEqual(event["side"], "buy")
        self.assertEqual(event["tokenAmount"], 31952.74)
        self.assertEqual(event["solAmount"], 0.0161)
        self.assertAlmostEqual(event["priceSol"], 0.0161 / 31952.74)
        self.assertEqual(event["eventAt"], 1788976012000)

    def test_normalizes_usdc_quote_using_sol_amount(self):
        event = normalize_shrine_trade({
            "mint": MINT, "signature": "usdc-buy", "is_buy": True,
            "token_amount": 1000, "quote_amount": 12.5,
            "sol_amount": 0.08, "price": 0.0125,
            "quote": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "timestamp": 1788976012,
        })
        self.assertIsNotNone(event)
        self.assertAlmostEqual(event["priceSol"], 0.00008)
        self.assertAlmostEqual(event["solAmount"], 0.08)

    def test_accepts_missing_quote_when_sol_amount_is_explicit(self):
        event = normalize_shrine_trade({
            "mint": MINT, "signature": "no-quote-buy", "is_buy": True,
            "token_amount": 200, "sol_amount": 0.04,
            "price": 0.0002, "timestamp": 1788976012,
        })
        self.assertIsNotNone(event)
        self.assertAlmostEqual(event["priceSol"], 0.0002)

    def test_rejects_non_sol_quote_without_sol_normalized_amount(self):
        self.assertIsNone(normalize_shrine_trade({
            "mint": MINT, "signature": "usdc-no-sol", "is_buy": True,
            "token_amount": 1000, "quote_amount": 12.5,
            "price": 0.0125,
            "quote": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "timestamp": 1788976012,
        }))

    def test_normalizes_sell(self):
        event = normalize_shrine_trade({
            "mint": MINT, "signature": "sell-signature", "is_buy": False,
            "token_amount": 100, "sol_amount": 0.02, "price": 0.0002,
            "quote": SOL, "timestamp": 1788976012,
        })
        self.assertEqual(event["side"], "sell")

    def test_rejects_non_sol_quote_without_sol_normalized_amount(self):
        self.assertIsNone(normalize_shrine_trade({
            "mint": MINT, "signature": "usdc-signature", "is_buy": True,
            "token_amount": 100, "quote_amount": 2.0, "price": 0.02,
            "quote": "USDC", "timestamp": 1788976012,
        }))

    def test_rejects_missing_signature_and_invalid_numbers(self):
        base = {
            "mint": MINT, "is_buy": True, "token_amount": 100,
            "sol_amount": 0.02, "price": 0.0002, "quote": SOL,
            "timestamp": 1788976012,
        }
        self.assertIsNone(normalize_shrine_trade(base))
        self.assertIsNone(normalize_shrine_trade({**base, "signature": "x", "price": float("nan")}))


if __name__ == "__main__":
    unittest.main()
