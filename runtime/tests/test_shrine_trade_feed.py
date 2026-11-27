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
        self.assertEqual(event["priceSol"], 0.000000504)
        self.assertEqual(event["eventAt"], 1788976012000)

    def test_normalizes_sell(self):
        event = normalize_shrine_trade({
            "mint": MINT, "signature": "sell-signature", "is_buy": False,
            "token_amount": 100, "sol_amount": 0.02, "price": 0.0002,
            "quote": SOL, "timestamp": 1788976012,
        })
        self.assertEqual(event["side"], "sell")

    def test_rejects_non_sol_quote(self):
        self.assertIsNone(normalize_shrine_trade({
            "mint": MINT, "signature": "usdc-signature", "is_buy": True,
            "token_amount": 100, "sol_amount": 0.02, "price": 0.0002,
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
