import asyncio
import unittest

from api_server import _sniper_enrich_wallet_activity


PUBLIC_KEY = "11111111111111111111111111111111"
WALLET_ID = "00000000-0000-0000-0000-000000000001"


class SniperWalletActivityConcurrencyTests(unittest.TestCase):
    def test_enrichment_is_bounded_and_preserves_newest_first_order(self):
        async def run():
            active = 0
            max_active = 0
            persisted = []

            async def solana_rpc(method, params, request_id):
                nonlocal active, max_active
                active += 1
                max_active = max(max_active, active)
                try:
                    await asyncio.sleep(0.01)
                    signature = params[0]
                    amount = int(signature.removeprefix("sig"))
                    return {
                        "meta": {
                            "fee": 5000,
                            "err": None,
                            "preBalances": [100000],
                            "postBalances": [100000 + amount],
                        },
                        "transaction": {"message": {"accountKeys": [PUBLIC_KEY]}},
                    }
                finally:
                    active -= 1

            async def supabase_request(method, path, payload):
                nonlocal active, max_active
                active += 1
                max_active = max(max_active, active)
                try:
                    await asyncio.sleep(0.005)
                    persisted.append(payload["p_signature"])
                    return {}
                finally:
                    active -= 1

            items = [
                {"signature": f"sig{i}", "slot": i, "blockTime": 1000 + i}
                for i in range(12, 0, -1)
            ]
            result = await _sniper_enrich_wallet_activity(
                items, PUBLIC_KEY, 12345, WALLET_ID,
                solana_rpc, supabase_request, max_concurrency=4,
            )
            self.assertLessEqual(max_active, 4)
            self.assertEqual([entry["signature"] for entry in result], [item["signature"] for item in items])
            self.assertEqual([entry["netLamports"] for entry in result], list(range(12, 0, -1)))
            self.assertEqual(len(persisted), len(items))

        asyncio.run(run())

    def test_detail_and_persistence_failures_do_not_abort_other_entries(self):
        async def run():
            persisted = []

            async def solana_rpc(method, params, request_id):
                signature = params[0]
                if signature == "sig3":
                    raise RuntimeError("rpc unavailable")
                amount = int(signature.removeprefix("sig"))
                return {
                    "meta": {
                        "fee": 5000,
                        "err": None,
                        "preBalances": [100000],
                        "postBalances": [100000 + amount],
                    },
                    "transaction": {"message": {"accountKeys": [PUBLIC_KEY]}},
                }

            async def supabase_request(method, path, payload):
                persisted.append(payload["p_signature"])
                if payload["p_signature"] == "sig2":
                    raise RuntimeError("database unavailable")
                return {}

            items = [{"signature": f"sig{i}", "slot": i} for i in (4, 3, 2, 1)]
            result = await _sniper_enrich_wallet_activity(
                items, PUBLIC_KEY, 12345, WALLET_ID,
                solana_rpc, supabase_request, max_concurrency=2,
            )
            self.assertEqual([entry["signature"] for entry in result], ["sig4", "sig3", "sig2", "sig1"])
            self.assertEqual(result[1]["accountingStatus"], "unavailable")
            self.assertEqual(result[1]["direction"], "unknown")
            self.assertEqual(result[0]["accountingStatus"], "confirmed")
            self.assertEqual(result[2]["accountingStatus"], "confirmed")
            self.assertEqual(len(persisted), 4)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
