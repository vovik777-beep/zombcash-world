import unittest

from sniper_execution import (
    LiveExecutionDisabled,
    build_pumpportal_trade_request,
    live_execution_enabled,
    live_execution_ready,
)


MINT = "5qErNMKHiYvixh69V1aw8Dazzg6bvUN41ojcozUppump"
WALLET = "11111111111111111111111111111111"


class SniperExecutionTests(unittest.TestCase):
    def test_buy_request_uses_sol_amount_and_pump_bonding_curve(self):
        payload = build_pumpportal_trade_request(
            public_key=WALLET, mint=MINT, side="buy", amount=0.01,
            max_slippage_bps=300, priority_fee_sol=0.00005,
        )
        self.assertEqual(payload["action"], "buy")
        self.assertEqual(payload["denominatedInSol"], "true")
        self.assertEqual(payload["pool"], "pump")
        self.assertEqual(payload["slippage"], 3.0)

    def test_sell_request_uses_token_amount(self):
        payload = build_pumpportal_trade_request(
            public_key=WALLET, mint=MINT, side="sell", amount=1000,
            max_slippage_bps=500, priority_fee_sol=0,
        )
        self.assertEqual(payload["denominatedInSol"], "false")

    def test_rejects_invalid_amount_slippage_and_mint(self):
        with self.assertRaises(ValueError):
            build_pumpportal_trade_request(
                public_key=WALLET, mint=MINT, side="buy", amount=0,
                max_slippage_bps=300, priority_fee_sol=0,
            )
        with self.assertRaises(ValueError):
            build_pumpportal_trade_request(
                public_key=WALLET, mint="bad", side="buy", amount=0.01,
                max_slippage_bps=300, priority_fee_sol=0,
            )
        with self.assertRaises(ValueError):
            build_pumpportal_trade_request(
                public_key=WALLET, mint=MINT, side="buy", amount=0.01,
                max_slippage_bps=6000, priority_fee_sol=0,
            )

    def test_execution_uses_helius_rpc_only(self):
        from pathlib import Path
        source = Path("sniper_execution.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("HELIUS_RPC_URL"', source)
        self.assertNotIn('os.getenv("SOLANA_RPC_URL"', source)

    def test_live_mode_is_locked_until_full_order_lifecycle_is_ready(self):
        self.assertFalse(live_execution_enabled())
        self.assertFalse(live_execution_ready())


if __name__ == "__main__":
    unittest.main()
