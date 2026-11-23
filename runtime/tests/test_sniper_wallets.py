import unittest
from unittest.mock import AsyncMock

from solders.keypair import Keypair

from sniper_wallets import _base58_encode, _generate_solana_keypair, ensure_managed_wallet, PUBLIC_KEY_RE


class SniperWalletTests(unittest.IsolatedAsyncioTestCase):
    def test_base58_leading_zeroes(self):
        self.assertEqual(_base58_encode(b"\x00\x00\x01"), "112")

    def test_generated_solana_keypair_shape(self):
        address, secret = _generate_solana_keypair()
        self.assertTrue(PUBLIC_KEY_RE.fullmatch(address))
        self.assertGreaterEqual(len(secret), 80)
        self.assertLessEqual(len(secret), 100)
        self.assertEqual(str(Keypair.from_base58_string(secret).pubkey()), address)

    async def test_existing_wallet_is_returned_without_generating_secret(self):
        request = AsyncMock(return_value={
            "walletId": "wallet-id", "publicKey": "11111111111111111111111111111111",
            "status": "active", "custodyMode": "managed",
        })
        wallet = await ensure_managed_wallet(123456, request)
        self.assertEqual(wallet["publicKey"], "11111111111111111111111111111111")
        self.assertFalse(wallet["created"])
        request.assert_awaited_once()

    async def test_provision_sends_secret_only_to_vault_rpc(self):
        async def fake_request(method, path, *, payload=None, params=None):
            if path == "rpc/sniper_wallet_get":
                return {}
            self.assertEqual(path, "rpc/sniper_wallet_provision")
            self.assertEqual(payload["p_telegram_id"], 123456)
            self.assertTrue(PUBLIC_KEY_RE.fullmatch(payload["p_public_key"]))
            self.assertGreaterEqual(len(payload["p_secret_key"]), 80)
            return {
                "walletId": "wallet-id", "publicKey": payload["p_public_key"],
                "status": "active", "created": True,
            }

        wallet = await ensure_managed_wallet(123456, fake_request)
        self.assertTrue(wallet["created"])
        self.assertEqual(wallet["custodyMode"], "managed")
        self.assertNotIn("secret", wallet)


if __name__ == "__main__":
    unittest.main()
