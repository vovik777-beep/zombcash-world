import unittest
from unittest.mock import patch

from sniper_live_lifecycle import reconcile_confirmed_live_orders, reconcile_unknown_live_orders, submit_live_order
from sniper_execution import LiveExecutionDisabled, LiveExecutionError


MINT = "5qErNMKHiYvixh69V1aw8Dazzg6bvUN41ojcozUppump"
ORDER_ID = "11111111-1111-4111-8111-111111111111"
WALLET = "11111111111111111111111111111111"
DUMMY_KEY_MATERIAL = "test-secret-never-return-this"


class LiveLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def balance_ok(self, public_key):
        return 100_000_000

    def make_supabase(self, prepared_status="prepared", settings=None):
        calls = []
        async def request(method, path, payload=None):
            calls.append((path, payload or {}))
            if path == "rpc/sniper_user_settings_get":
                return settings or {"enabled": True, "mode": "live", "live_armed": True, "max_slippage_bps": 300}
            if path == "rpc/sniper_live_order_prepare":
                return {"orderId": "22222222-2222-4222-8222-222222222222", "walletId": "33333333-3333-4333-8333-333333333333", "status": prepared_status}
            if path == "rpc/sniper_live_order_wallet_get":
                return {"walletId": "33333333-3333-4333-8333-333333333333", "publicKey": WALLET, "status": "active", "custodyMode": "managed"}
            if path == "rpc/sniper_live_order_wallet_secret":
                return DUMMY_KEY_MATERIAL
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

    async def test_unknown_reconciler_promotes_confirmed_signature_without_resending(self):
        calls = []
        async def request(method, path, payload=None):
            calls.append((path, payload or {}))
            if path == "rpc/sniper_live_unknown_orders":
                return [{"order_id": "22222222-2222-4222-8222-222222222222", "signature": "sig"}]
            if path == "rpc/sniper_live_order_transition":
                return {"status": payload["p_status"], "signature": payload["p_signature"]}
            if path == "rpc/sniper_live_order_unknown_reconciliation_touch":
                return None
            raise AssertionError(path)
        async def status_fetcher(signature):
            self.assertEqual(signature, "sig")
            return {"err": None, "confirmationStatus": "confirmed", "slot": 321}
        result = await reconcile_unknown_live_orders(request, signature_status_fetcher=status_fetcher)
        self.assertEqual(result, {"checked": 1, "confirmed": 1, "failed": 0, "pending": 0, "errors": 0})
        transition = next(payload for path, payload in calls if path == "rpc/sniper_live_order_transition")
        self.assertEqual(transition["p_status"], "confirmed")
        self.assertEqual(transition["p_chain_slot"], 321)

    async def test_unknown_reconciler_marks_failed_chain_transaction(self):
        calls = []
        async def request(method, path, payload=None):
            calls.append((path, payload or {}))
            if path == "rpc/sniper_live_unknown_orders":
                return [{"order_id": "22222222-2222-4222-8222-222222222222", "signature": "sig"}]
            return {"status": "failed"}
        async def status_fetcher(signature):
            return {"err": {"InstructionError": [0, "Custom"]}, "confirmationStatus": "confirmed", "slot": 321}
        result = await reconcile_unknown_live_orders(request, signature_status_fetcher=status_fetcher)
        self.assertEqual(result["failed"], 1)
        transition = next(payload for path, payload in calls if path == "rpc/sniper_live_order_transition")
        self.assertEqual(transition["p_status"], "failed")
        self.assertEqual(transition["p_error_code"], "chain_transaction_failed")

    async def test_unknown_reconciler_keeps_processed_signature_pending(self):
        calls = []
        async def request(method, path, payload=None):
            calls.append(path)
            if path == "rpc/sniper_live_unknown_orders":
                return [{"order_id": "22222222-2222-4222-8222-222222222222", "signature": "sig"}]
            raise AssertionError(path)
        async def status_fetcher(signature):
            return {"err": None, "confirmationStatus": "processed", "slot": 321}
        result = await reconcile_unknown_live_orders(request, signature_status_fetcher=status_fetcher)
        self.assertEqual(result["pending"], 1)
        self.assertEqual(calls, ["rpc/sniper_live_unknown_orders", "rpc/sniper_live_order_unknown_reconciliation_touch"])

    async def test_reconciler_applies_confirmed_fill_once(self):
        calls = []
        async def request(method, path, payload=None):
            calls.append((path, payload or {}))
            if path == "rpc/sniper_live_unfilled_confirmed_orders":
                return [{"order_id": "22222222-2222-4222-8222-222222222222", "signature": "sig", "mint": MINT, "side": "buy", "chain_slot": 123}]
            if path == "rpc/sniper_live_order_wallet_get":
                return {"publicKey": WALLET, "status": "inactive", "custodyMode": "managed"}
            if path == "rpc/sniper_live_position_apply_fill":
                return {"positionId": "44444444-4444-4444-8444-444444444444", "status": "open"}
            if path == "rpc/sniper_live_order_fill_reconciliation_touch":
                return None
            if path == "rpc/sniper_live_order_unknown_reconciliation_touch":
                return None
            raise AssertionError(path)
        tx = {
            "slot": 123, "blockTime": 1788976012,
            "transaction": {"message": {"accountKeys": [WALLET, "22222222222222222222222222222222"]}},
            "meta": {
                "err": None, "fee": 5000,
                "preBalances": [10_000_000_000, 0],
                "postBalances": [8_997_955_720, 2_039_280],
                "preTokenBalances": [],
                "postTokenBalances": [{
                    "accountIndex": 1, "mint": MINT, "owner": WALLET,
                    "uiTokenAmount": {"amount": "1000", "decimals": 0, "uiAmountString": "1000"},
                }],
            },
        }
        async def fetcher(signature):
            self.assertEqual(signature, "sig")
            return tx
        result = await reconcile_confirmed_live_orders(request, transaction_fetcher=fetcher)
        self.assertEqual(result, {"checked": 1, "applied": 1, "pending": 0, "errors": 0})
        apply_call = next(payload for path, payload in calls if path == "rpc/sniper_live_position_apply_fill")
        self.assertEqual(apply_call["p_order_id"], "22222222-2222-4222-8222-222222222222")
        self.assertEqual(apply_call["p_token_amount"], "1000")

    async def test_reconciler_does_not_call_chain_when_no_confirmed_orders(self):
        calls = []
        async def request(method, path, payload=None):
            calls.append(path)
            return []
        async def fetcher(signature):
            raise AssertionError("no signatures to fetch")
        result = await reconcile_confirmed_live_orders(request, transaction_fetcher=fetcher)
        self.assertEqual(result, {"checked": 0, "applied": 0, "pending": 0, "errors": 0})
        self.assertEqual(calls, ["rpc/sniper_live_unfilled_confirmed_orders"])

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
                amount=0.01, supabase_request=request, executor=executor, balance_checker=self.balance_ok,
            )
        self.assertEqual(result["status"], "broadcasting")
        self.assertTrue(result["reused"])
        self.assertFalse(any(path == "rpc/sniper_live_order_wallet_secret" for path, _ in calls))

    async def test_confirmed_execution_uses_broadcasting_lock_and_two_phase_confirmation(self):
        request, calls = self.make_supabase()
        async def executor(**kwargs):
            self.assertEqual(kwargs["public_key"], WALLET)
            self.assertEqual(kwargs["secret_key_base58"], DUMMY_KEY_MATERIAL)
            self.assertEqual(kwargs["max_slippage_bps"], 300)
            return {"status": "confirmed", "signature": "sig123", "slot": 42}
        with patch("sniper_live_lifecycle.live_execution_enabled", return_value=True):
            result = await submit_live_order(
                telegram_id=1, client_order_id=ORDER_ID, mint=MINT, side="buy",
                amount=0.01, supabase_request=request, executor=executor, balance_checker=self.balance_ok,
            )
        transitions = [payload["p_status"] for path, payload in calls if path == "rpc/sniper_live_order_transition"]
        self.assertEqual(transitions, ["broadcasting", "submitted", "confirmed"])
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["signature"], "sig123")
        self.assertNotIn(DUMMY_KEY_MATERIAL, repr(result))

    async def test_insufficient_wallet_balance_fails_before_secret_or_broadcast(self):
        request, calls = self.make_supabase()
        async def balance_checker(public_key):
            return 10_000_000
        async def executor(**kwargs):
            raise AssertionError("executor must not be called")
        with patch("sniper_live_lifecycle.live_execution_enabled", return_value=True):
            result = await submit_live_order(
                telegram_id=1, client_order_id=ORDER_ID, mint=MINT, side="buy",
                amount=0.01, supabase_request=request, executor=executor,
                balance_checker=balance_checker,
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errorCode"], "insufficient_wallet_sol_balance")
        self.assertFalse(any(path == "rpc/sniper_live_order_wallet_secret" for path, _ in calls))

    async def test_unknown_execution_is_persisted_and_not_retried(self):
        request, calls = self.make_supabase()
        async def executor(**kwargs):
            return {"status": "unknown", "signature": "sig-unknown", "errorCode": "confirmation_timeout"}
        with patch("sniper_live_lifecycle.live_execution_enabled", return_value=True):
            result = await submit_live_order(
                telegram_id=1, client_order_id=ORDER_ID, mint=MINT, side="buy",
                amount=0.01, supabase_request=request, executor=executor, balance_checker=self.balance_ok,
            )
        transitions = [payload["p_status"] for path, payload in calls if path == "rpc/sniper_live_order_transition"]
        self.assertEqual(transitions, ["broadcasting", "unknown"])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["signature"], "sig-unknown")

    async def test_executor_is_not_called_when_broadcast_lock_fails(self):
        async def request(method, path, payload=None):
            if path == "rpc/sniper_user_settings_get":
                return {"enabled": True, "mode": "live", "live_armed": True, "max_slippage_bps": 300}
            if path == "rpc/sniper_live_order_prepare":
                return {"orderId": "22222222-2222-4222-8222-222222222222", "status": "prepared"}
            if path == "rpc/sniper_live_order_wallet_get":
                return {"walletId": "33333333-3333-4333-8333-333333333333", "publicKey": WALLET, "status": "active", "custodyMode": "managed"}
            if path == "rpc/sniper_live_order_wallet_secret":
                return DUMMY_KEY_MATERIAL
            if path == "rpc/sniper_live_order_transition":
                raise RuntimeError("database unavailable")
            raise AssertionError("unexpected RPC: " + path)
        async def executor(**kwargs):
            raise AssertionError("broadcast must not start without the DB lock")
        with patch("sniper_live_lifecycle.live_execution_enabled", return_value=True):
            with self.assertRaises(RuntimeError):
                await submit_live_order(
                    telegram_id=1, client_order_id=ORDER_ID, mint=MINT, side="buy",
                    amount=0.01, supabase_request=request, executor=executor, balance_checker=self.balance_ok,
                )

    async def test_disarmed_user_can_still_submit_sell_exit(self):
        request, calls = self.make_supabase(settings={
            "enabled": False, "mode": "paper", "live_armed": False, "max_slippage_bps": 500,
        })
        async def executor(**kwargs):
            self.assertEqual(kwargs["side"], "sell")
            self.assertEqual(kwargs["max_slippage_bps"], 500)
            return {"status": "confirmed", "signature": "sell-sig", "slot": 77}
        with patch("sniper_live_lifecycle.live_execution_enabled", return_value=True):
            result = await submit_live_order(
                telegram_id=1, client_order_id=ORDER_ID, mint=MINT, side="sell",
                amount=1000, supabase_request=request, executor=executor,
            )
        self.assertEqual(result["status"], "confirmed")
        transitions = [payload["p_status"] for path, payload in calls if path == "rpc/sniper_live_order_transition"]
        self.assertEqual(transitions, ["broadcasting", "submitted", "confirmed"])

    async def test_disarmed_user_cannot_submit_new_buy(self):
        request, calls = self.make_supabase(settings={
            "enabled": False, "mode": "paper", "live_armed": False, "max_slippage_bps": 300,
        })
        with patch("sniper_live_lifecycle.live_execution_enabled", return_value=True):
            with self.assertRaises(LiveExecutionDisabled):
                await submit_live_order(
                    telegram_id=1, client_order_id=ORDER_ID, mint=MINT, side="buy",
                    amount=0.01, supabase_request=request,
                )
        self.assertFalse(any(path == "rpc/sniper_live_order_prepare" for path, _ in calls))

    async def test_execution_error_marks_order_failed(self):
        request, calls = self.make_supabase()
        async def executor(**kwargs):
            raise LiveExecutionError("pumpportal_transaction_build_failed")
        with patch("sniper_live_lifecycle.live_execution_enabled", return_value=True):
            result = await submit_live_order(
                telegram_id=1, client_order_id=ORDER_ID, mint=MINT, side="buy",
                amount=0.01, supabase_request=request, executor=executor, balance_checker=self.balance_ok,
            )
        transitions = [payload["p_status"] for path, payload in calls if path == "rpc/sniper_live_order_transition"]
        self.assertEqual(transitions, ["broadcasting", "failed"])
        self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
