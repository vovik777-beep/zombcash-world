import os
import unittest
import asyncio
from unittest.mock import AsyncMock, patch

from sniper_execution import (
    LiveExecutionDisabled,
    build_pumpportal_trade_request,
    live_execution_enabled,
    live_execution_ready,
    classify_signature_status,
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

    def test_signature_status_requires_definitive_confirmation(self):
        self.assertIsNone(classify_signature_status(None))
        self.assertIsNone(classify_signature_status({"confirmationStatus": "processed", "err": None}))
        self.assertEqual(classify_signature_status({"confirmationStatus": "confirmed", "err": None}), "confirmed")
        self.assertEqual(classify_signature_status({"confirmationStatus": "finalized", "err": None}), "confirmed")
        self.assertEqual(classify_signature_status({"confirmationStatus": "confirmed", "err": {"InstructionError": [0, "Custom"]}}), "failed")

    def test_execution_uses_helius_rpc_only(self):
        from pathlib import Path
        source = Path("sniper_execution.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("HELIUS_RPC_URL"', source)
        self.assertNotIn('os.getenv("SOLANA_RPC_URL"', source)

    def test_code_readiness_is_separate_from_global_live_kill_switch(self):
        with patch.dict(os.environ, {
            "HELIUS_RPC_URL": "https://rpc.example.invalid",
            "PUMPPORTAL_TRADE_LOCAL_URL": "https://trade.example.invalid",
            "SNIPER_LIVE_TRADING_ENABLED": "false",
        }, clear=True):
            self.assertTrue(live_execution_ready())
            self.assertFalse(live_execution_enabled())

    def test_execution_rpc_preflight_requires_a_valid_slot(self):
        with patch("sniper_execution._solana_rpc_call", new=AsyncMock(return_value=123456)):
            self.assertTrue(asyncio.run(check_execution_rpc()))
        with patch("sniper_execution._solana_rpc_call", new=AsyncMock(return_value=None)):
            self.assertFalse(asyncio.run(check_execution_rpc()))
        with patch("sniper_execution._solana_rpc_call", new=AsyncMock(side_effect=RuntimeError("offline"))):
            self.assertFalse(asyncio.run(check_execution_rpc()))

    def test_code_readiness_requires_an_https_execution_rpc(self):
        with patch.dict(os.environ, {
            "HELIUS_RPC_URL": "",
            "PUMPPORTAL_TRADE_LOCAL_URL": "https://trade.example.invalid",
        }, clear=True):
            self.assertFalse(live_execution_ready())


if __name__ == "__main__":
    unittest.main()
