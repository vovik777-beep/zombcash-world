import time
import unittest

from helius_standard_ws import (
    _helius_rpc_url_from_env,
    _helius_ws_url_from_rpc,
    _normalize_helius_trade_event,
    _normalize_pumpfun_log_trade,
)

MINT = "5qErNMKHiYvixh69V1aw8Dazzg6bvUN41ojcozUppump"


class HeliusStandardWsTests(unittest.TestCase):
    def _transaction(self, side):
        if side == "buy":
            pre_lamports, post_lamports = 2_000_000_000, 1_899_995_000
            pre_tokens, post_tokens = "1000000", "2000000"
            pre_ui, post_ui = "1", "2"
        else:
            pre_lamports, post_lamports = 1_899_995_000, 1_999_990_000
            pre_tokens, post_tokens = "2000000", "1000000"
            pre_ui, post_ui = "2", "1"
        return {
            "blockTime": int(time.time()),
            "transaction": {
                "signatures": ["sig-" + side],
                "message": {"accountKeys": [
                    {"pubkey": "TraderWallet", "signer": True},
                    {"pubkey": "TokenAccount", "signer": False},
                ]},
            },
            "meta": {
                "err": None,
                "logMessages": ["Program log: Instruction: " + ("Buy" if side == "buy" else "Sell")],
                "fee": 5000,
                "preBalances": [pre_lamports, 2_039_280],
                "postBalances": [post_lamports, 2_039_280],
                "preTokenBalances": [{
                    "mint": MINT, "owner": "TraderWallet",
                    "uiTokenAmount": {"amount": pre_tokens, "decimals": 6, "uiAmountString": pre_ui},
                }],
                "postTokenBalances": [{
                    "mint": MINT, "owner": "TraderWallet",
                    "uiTokenAmount": {"amount": post_tokens, "decimals": 6, "uiAmountString": post_ui},
                }],
            },
        }

    def test_builds_mainnet_rpc_url_from_api_key_without_logging_it(self):
        from unittest.mock import patch
        with patch.dict("os.environ", {"HELIUS_API_KEY": "test-key", "HELIUS_RPC_URL": ""}, clear=False):
            self.assertEqual(
                _helius_rpc_url_from_env(),
                "https://mainnet.helius-rpc.com/?api-key=test-key",
            )
        with patch.dict("os.environ", {"HELIUS_API_KEY": "", "HELIUS_RPC_URL": "https://mainnet.helius-rpc.com/?api-key=url-key"}, clear=False):
            self.assertEqual(
                _helius_rpc_url_from_env(),
                "https://mainnet.helius-rpc.com/?api-key=url-key",
            )

    def test_derives_helius_standard_wss_url(self):
        self.assertEqual(
            _helius_ws_url_from_rpc("https://mainnet.helius-rpc.com/?api-key=secret"),
            "wss://mainnet.helius-rpc.com/?api-key=secret",
        )
        self.assertIsNone(_helius_ws_url_from_rpc("https://example.com/?api-key=secret"))

    def test_normalizes_buy_and_sell_from_balance_deltas(self):
        buy = _normalize_helius_trade_event(self._transaction("buy"), MINT)
        sell = _normalize_helius_trade_event(self._transaction("sell"), MINT)
        self.assertEqual(buy["side"], "buy")
        self.assertEqual(buy["source"], "helius_standard_ws")
        self.assertEqual(sell["side"], "sell")
        self.assertAlmostEqual(buy["solAmount"], 0.1)
        self.assertAlmostEqual(sell["solAmount"], 0.1)
        self.assertEqual(buy["tokenAmount"], 1)
        self.assertEqual(sell["tokenAmount"], 1)

    def test_ignores_non_signer_pool_token_balance_delta(self):
        tx = self._transaction("buy")
        tx["transaction"]["message"]["accountKeys"].append({"pubkey": "CurveAuthority", "signer": False})
        tx["meta"]["preTokenBalances"].append({
            "mint": MINT, "owner": "CurveAuthority",
            "uiTokenAmount": {"amount": "9000000", "decimals": 6, "uiAmountString": "9"},
        })
        tx["meta"]["postTokenBalances"].append({
            "mint": MINT, "owner": "CurveAuthority",
            "uiTokenAmount": {"amount": "8000000", "decimals": 6, "uiAmountString": "8"},
        })
        event = _normalize_helius_trade_event(tx, MINT)
        self.assertIsNotNone(event)
        self.assertEqual(event["trader"], "TraderWallet")

    def test_adjusts_for_rent_when_buy_creates_token_account(self):
        tx = self._transaction("buy")
        tx["meta"]["preBalances"] = [2_000_000_000, 0]
        tx["meta"]["postBalances"] = [1_897_955_720, 2_039_280]
        tx["meta"]["preTokenBalances"] = []
        tx["meta"]["postTokenBalances"][0]["accountIndex"] = 1
        event = _normalize_helius_trade_event(tx, MINT)
        self.assertIsNotNone(event)
        self.assertEqual(event["side"], "buy")
        self.assertAlmostEqual(event["solAmount"], 0.1)

    def test_normalizes_pumpfun_trade_event_directly_from_logs(self):
        event = {
            "mint": MINT,
            "sol_amount": 100_000_000,
            "token_amount": 2_000_000,
            "is_buy": True,
            "user": "TraderWallet",
            "quote_mint": "11111111111111111111111111111111",
        }
        normalized = _normalize_pumpfun_log_trade(event, MINT, "signature")
        self.assertEqual(normalized["side"], "buy")
        self.assertAlmostEqual(normalized["solAmount"], 0.1)
        self.assertAlmostEqual(normalized["tokenAmount"], 2)
        self.assertAlmostEqual(normalized["priceSol"], 0.05)
        self.assertEqual(normalized["trader"], "TraderWallet")

    def test_rejects_non_sol_pumpfun_quote_mint(self):
        event = {
            "mint": MINT,
            "sol_amount": 100_000_000,
            "token_amount": 2_000_000,
            "is_buy": True,
            "user": "TraderWallet",
            "quote_mint": "USDC_MINT",
        }
        self.assertIsNone(_normalize_pumpfun_log_trade(event, MINT, "signature"))

    def test_skips_failed_transactions(self):
        failed = self._transaction("buy")
        failed["meta"]["err"] = {"InstructionError": [1, "Custom"]}
        self.assertIsNone(_normalize_helius_trade_event(failed, MINT))

    def test_skips_ambiguous_signers(self):
        tx = self._transaction("buy")
        tx["transaction"]["message"]["accountKeys"].append({"pubkey": "TraderWallet", "signer": True})
        self.assertIsNone(_normalize_helius_trade_event(tx, MINT))


if __name__ == "__main__":
    unittest.main()
