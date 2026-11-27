import unittest
from unittest.mock import patch

from sniper_live_lifecycle import submit_live_order
from sniper_execution import LiveExecutionDisabled, LiveExecutionError


MINT = "5qErNMKHiYvixh69V1aw8Dazzg6bvUN41ojcozUppump"
ORDER_ID = "11111111-1111-4111-8111-111111111111"
WALLET = "11111111111111111111111111111111"
SECRET = "test-secret-never-return-this"


class LiveLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def make_supabase(self, prepared_status="prepared"):
        calls = []
        async def request(method, path, payload=None):
            calls.append((path, payload or {}))
            if path == "rpc/sniper_user_settings_get":
                return {"enabled": True, "mode": "live", "live_armed": True, "max_slippage_bps": 300}
            if path == "rpc/sniper_live_order_prepare":
                return {"orderId": "22222222-2222-4222-8222-222222222222", "status": prepared_status}
            if path == "rpc/sniper_wallet_get":
                return {"publicKey": WALLET, "status": "active"}
            if path == "rpc/sniper_wallet_secret":
                return SECRET
            if path == "rpc/sniper_live_order_transition":
                return {
                    "orderId": "22222222-2222-4222-8222-222222222222",
                    "status": payload["p_status"],
                    "signature": payload.get("p_signature"),
                    "chainSlot": payload.get("p_chain_slot"),
                    "errorCode": payload.get("p_error_code"),
                }
            raise AssertionError("unexpected RPC: " + path)
        return request, calls

    async def test_global_flag_blocks_before_order_creation(self):
        request, calls = self.make_supabase()
        with patch("sniper_live_lifecycle.live_execution_enabled", return_value=False):
            with self.assertRaises(LiveExecutionDisabled):
                await submit_live_order(
                    telegram_id=1, client_order_id=ORDER_ID, mint=MINT, side="buy",
                    amount=0.01, supabase_request=request,
                )
        self.assertEqual(calls, [])

    async def test_existing_non_prepared_order_is_never_rebroadcast(self):
        request, calls = self.make_supabase("broadcasting")
        async def executor(**kwargs):
            raise AssertionError("executor must not be called")
        with patch("sniper_live_lifecycle.live_execution_enabled", return_value=True):
            result = await submit_live_order(
                telegram_id=1, client_order_id=ORDER_ID, mint=MINT, side="buy",
                amount=0.01, supabase_request=request, executor=executor,
            )
        self.assertEqual(result["status"], "broadcasting")
        self.assertTrue(result["reused"])
        self.assertFalse(any(path == "rpc/sniper_wallet_secret" for path, _ in calls))

    async def test_confirmed_execution_uses_broadcasting_lock_and_two_phase_confirmation(self):
        request, calls = self.make_supabase()
        async def executor(**kwargs):
            self.assertEqual(kwargs["public_key"], WALLET)
            self.assertEqual(kwargs["secret_key_base58"], SECRET)
            self.assertEqual(kwargs["max_slippage_bps"], 300)
            return {"status": "confirmed", "signature": "sig123", "slot": 42}
        with patch("sniper_live_lifecycle.live_execution_enabled", return_value=True):
            result = await submit_live_order(
                telegram_id=1, client_order_id=ORDER_ID, mint=MINT, side="buy",
                amount=0.01, supabase_request=request, executor=executor,
            )
        transitions = [payload["p_status"] for path, payload in calls if path == "rpc/sniper_live_order_transition"]
        self.assertEqual(transitions, ["broadcasting", "submitted", "confirmed"])
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["signature"], "sig123")
        self.assertNotIn(SECRET, repr(result))

    async def test_unknown_execution_is_persisted_and_not_retried(self):
        request, calls = self.make_supabase()
        async def executor(**kwargs):
            return {"status": "unknown", "signature": "sig-unknown", "errorCode": "confirmation_timeout"}
        with patch("sniper_live_lifecycle.live_execution_enabled", return_value=True):
            result = await submit_live_order(
                telegram_id=1, client_order_id=ORDER_ID, mint=MINT, side="buy",
                amount=0.01, supabase_request=request, executor=executor,
            )
        transitions = [payload["p_status"] for path, payload in calls if path == "rpc/sniper_live_order_transition"]
        self.assertEqual(transitions, ["broadcasting", "unknown"])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["signature"], "sig-unknown")

    async def test_execution_error_marks_order_failed(self):
        request, calls = self.make_supabase()
        async def executor(**kwargs):
            raise LiveExecutionError("pumpportal_transaction_build_failed")
        with patch("sniper_live_lifecycle.live_execution_enabled", return_value=True):
            result = await submit_live_order(
                telegram_id=1, client_order_id=ORDER_ID, mint=MINT, side="buy",
                amount=0.01, supabase_request=request, executor=executor,
            )
        transitions = [payload["p_status"] for path, payload in calls if path == "rpc/sniper_live_order_transition"]
        self.assertEqual(transitions, ["broadcasting", "failed"])
        self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
