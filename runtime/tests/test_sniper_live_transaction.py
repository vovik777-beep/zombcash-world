import unittest

from sniper_live_transaction import extract_live_fill


WALLET = "11111111111111111111111111111111"
MINT = "5qErNMKHiYvixh69V1aw8Dazzg6bvUN41ojcozUppump"
TOKEN_ACCOUNT = "22222222222222222222222222222222"


def make_tx(*, side="buy", token_amount="1000", fee=5000):
    if side == "buy":
        pre_wallet, post_wallet = 10_000_000_000, 8_997_955_720
        pre_token_lamports, post_token_lamports = 0, 2_039_280
        pre_token_amount, post_token_amount = None, token_amount
    else:
        pre_wallet, post_wallet = 8_997_955_000, 9_999_989_280
        pre_token_lamports, post_token_lamports = 2_039_280, 0
        pre_token_amount, post_token_amount = token_amount, None

    def token_entry(amount):
        if amount is None:
            return None
        return {
            "accountIndex": 1,
            "mint": MINT,
            "owner": WALLET,
            "uiTokenAmount": {"amount": str(int(float(amount))), "decimals": 0, "uiAmountString": str(amount)},
        }

    pre = [token_entry(pre_token_amount)] if pre_token_amount is not None else []
    post = [token_entry(post_token_amount)] if post_token_amount is not None else []
    return {
        "slot": 123,
        "blockTime": 1788976012,
        "transaction": {"message": {"accountKeys": [WALLET, TOKEN_ACCOUNT]}},
        "meta": {
            "err": None,
            "fee": fee,
            "preBalances": [pre_wallet, pre_token_lamports],
            "postBalances": [post_wallet, post_token_lamports],
            "preTokenBalances": pre,
            "postTokenBalances": post,
        },
    }


class LiveTransactionFillTests(unittest.TestCase):
    def test_buy_fill_removes_ata_rent_and_network_fee(self):
        fill = extract_live_fill(make_tx(side="buy"), wallet_public_key=WALLET, mint=MINT, side="buy")
        self.assertIsNotNone(fill)
        self.assertEqual(fill["tokenAmount"], "1000")
        self.assertAlmostEqual(float(fill["solAmount"]), 1.0)
        self.assertAlmostEqual(float(fill["priceSol"]), 0.001)
        self.assertEqual(fill["feeLamports"], 5000)

    def test_sell_fill_removes_closed_ata_rent_and_network_fee(self):
        fill = extract_live_fill(make_tx(side="sell"), wallet_public_key=WALLET, mint=MINT, side="sell")
        self.assertIsNotNone(fill)
        self.assertEqual(fill["tokenAmount"], "1000")
        self.assertAlmostEqual(float(fill["solAmount"]), 1.0)
        self.assertAlmostEqual(float(fill["priceSol"]), 0.001)

    def test_rejects_failed_transaction(self):
        tx = make_tx()
        tx["meta"]["err"] = {"InstructionError": [0, "Custom"]}
        self.assertIsNone(extract_live_fill(tx, wallet_public_key=WALLET, mint=MINT, side="buy"))

    def test_rejects_token_balance_owned_by_another_wallet(self):
        tx = make_tx()
        tx["meta"]["postTokenBalances"][0]["owner"] = "33333333333333333333333333333333"
        self.assertIsNone(extract_live_fill(tx, wallet_public_key=WALLET, mint=MINT, side="buy"))

    def test_rejects_side_that_does_not_match_token_delta(self):
        self.assertIsNone(extract_live_fill(make_tx(side="buy"), wallet_public_key=WALLET, mint=MINT, side="sell"))


if __name__ == "__main__":
    unittest.main()
