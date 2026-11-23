"""Read-only DexScreener enrichment adapter; latest lists are not a full launch feed."""
import json
import urllib.request

BASE = "https://api.dexscreener.com"

class DexScreenerClient:
    def __init__(self, timeout=15, opener=None):
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen
    def _get(self, path):
        req = urllib.request.Request(BASE + path, headers={
            "Accept": "application/json",
            "User-Agent": "SolanaOutbreakIntelligence/0.1"
        })
        with self.opener(req, timeout=self.timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"DexScreener HTTP {response.status}")
            return json.loads(response.read().decode("utf-8"))
    def _solana(self, path):
        data = self._get(path)
        return [x for x in data if x.get("chainId") == "solana"] if isinstance(data, list) else []
    def latest_profiles(self):
        return self._solana("/token-profiles/latest/v1")
    def latest_ads(self):
        return self._solana("/ads/latest/v1")
    def latest_boosts(self):
        return self._solana("/token-boosts/latest/v1")
    def token_orders(self, mint):
        data = self._get(f"/orders/v1/solana/{mint}")
        return data if isinstance(data, list) else []
    def token_pairs(self, mint):
        data = self._get(f"/token-pairs/v1/solana/{mint}")
        return data if isinstance(data, list) else []
