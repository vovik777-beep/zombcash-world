import asyncio
import unittest

from aiohttp import ClientConnectionError
from shrine_trade_feed import _safe_ws_send, normalize_shrine_trade, select_paper_subscription_mints

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

    def test_normalizes_sparse_sol_trade_using_metadata_quote(self):
        event = normalize_shrine_trade({
            "mint": MINT, "signature": "sparse-sol-buy", "is_buy": True,
            "token_amount": 2000, "quote_amount": 0.12,
            "timestamp": 1788976012,
        }, quote_mint=SOL)
        self.assertIsNotNone(event)
        self.assertAlmostEqual(event["solAmount"], 0.12)
        self.assertAlmostEqual(event["priceSol"], 0.00006)

    def test_allows_missing_price_when_sol_amount_is_known(self):
        event = normalize_shrine_trade({
            "mint": MINT, "signature": "no-price-buy", "is_buy": True,
            "token_amount": 500, "sol_amount": 0.025,
            "timestamp": 1788976012,
        })
        self.assertIsNotNone(event)
        self.assertAlmostEqual(event["priceSol"], 0.00005)

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


class ShrinePaperSubscriptionSelectionTests(unittest.TestCase):
    def test_selects_unique_valid_open_mints(self):
        rows = [
            {"mint": MINT},
            {"mint": MINT},
            {"mint": "not-a-solana-mint"},
            {"mint": "7P7F9USPQ332N7QZBs6bwNXsL4AB6riEJtGv5Rxtpump"},
        ]
        selected, next_offset, total = select_paper_subscription_mints(rows, 0, 20)
        self.assertEqual(selected, [MINT, "7P7F9USPQ332N7QZBs6bwNXsL4AB6riEJtGv5Rxtpump"])
        self.assertEqual(next_offset, 0)
        self.assertEqual(total, 2)

    def test_rotates_batches_without_skipping_or_repeating(self):
        mints = [f"5qErNMKHiYvixh69V1aw8Dazzg6bvUN41ojcozU{i:02d}" for i in range(1, 39)]
        rows = [{"mint": mint} for mint in mints]
        first, offset, total = select_paper_subscription_mints(rows, 0, 20)
        second, offset, total2 = select_paper_subscription_mints(rows, offset, 20)
        self.assertEqual(total, 38)
        self.assertEqual(total2, 38)
        self.assertEqual(len(first), 20)
        self.assertEqual(len(second), 18)
        self.assertEqual(set(first).intersection(second), set())
        self.assertEqual(first + second, mints)

    def test_accepts_rpc_rows_as_strings(self):
        selected, offset, total = select_paper_subscription_mints([MINT], 0, 20)
        self.assertEqual(selected, [MINT])
        self.assertEqual(offset, 0)
        self.assertEqual(total, 1)

    def test_empty_rows_return_empty_selection(self):
        self.assertEqual(select_paper_subscription_mints([], 17, 20), ([], 0, 0))


class FakeWebSocket:
    def __init__(self, *, closed=False, error=None):
        self.closed = closed
        self.error = error
        self.sent = []

    async def send_str(self, payload):
        if self.error:
            raise self.error
        self.sent.append(payload)


class ShrineSafeSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_while_connection_is_open(self):
        ws = FakeWebSocket()
        result = await _safe_ws_send(ws, "42packet", asyncio.Lock())
        self.assertTrue(result)
        self.assertEqual(ws.sent, ["42packet"])

    async def test_skips_closed_connection(self):
        ws = FakeWebSocket(closed=True)
        result = await _safe_ws_send(ws, "42packet", asyncio.Lock())
        self.assertFalse(result)
        self.assertEqual(ws.sent, [])

    async def test_swallows_disconnect_race_without_unhandled_task_error(self):
        ws = FakeWebSocket(error=ClientConnectionError("closed during send"))
        result = await _safe_ws_send(ws, "42packet", asyncio.Lock())
        self.assertFalse(result)
        self.assertEqual(ws.sent, [])


if __name__ == "__main__":
    unittest.main()
