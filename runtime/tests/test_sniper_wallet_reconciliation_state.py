import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "api_server.py").read_text(encoding="utf-8")
MIGRATION = (ROOT / "supabase/migrations/20261120100000_sniper_wallet_reconciliation_state_read.sql").read_text(encoding="utf-8")
TREE = ast.parse(API)
FUNCTIONS = {
    node.name: node
    for node in ast.walk(TREE)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}


class SniperWalletReconciliationStateTests(unittest.TestCase):
    def test_read_endpoint_requires_origin_and_signed_survivor_session(self):
        self.assertIn("sniper_wallet_reconciliation_state", FUNCTIONS)
        source = ast.get_source_segment(API, FUNCTIONS["sniper_wallet_reconciliation_state"])
        self.assertIn('request.headers.get("Origin", "") != ALLOWED_ORIGIN', source)
        self.assertIn("_read_survivor_session(request)", source)
        self.assertIn('"rpc/sniper_wallet_get"', source)
        self.assertIn('"rpc/sniper_wallet_reconciliation_state"', source)
        self.assertIn('"Cache-Control": "no-store"', source)
        self.assertNotIn("request.query.get", source)
        self.assertIn('app.router.add_get("/api/sniper/wallet/reconciliation", sniper_wallet_reconciliation_state)', API)

    def test_sql_read_is_scoped_to_wallet_owner_and_never_mutates_baseline(self):
        lower = " ".join(MIGRATION.lower().split())
        self.assertIn("security definer", lower)
        self.assertIn("set search_path = ''", lower)
        self.assertIn("w.id = p_wallet_id and w.telegram_id = p_telegram_id", lower)
        self.assertIn("w.chain = 'solana' and w.status = 'active'", lower)
        self.assertIn("where wallet_id = p_wallet_id and telegram_id = p_telegram_id", lower)
        self.assertIn("if not found then return null;", lower)
        self.assertIn("'baselineLamports'", MIGRATION)
        self.assertIn("'baselineSlot'", MIGRATION)
        self.assertIn("'status'", MIGRATION)
        self.assertNotIn("insert into sniper.wallet_reconciliation_state", lower)
        self.assertNotIn("update sniper.wallet_reconciliation_state", lower)
        self.assertIn("revoke all on function public.sniper_wallet_reconciliation_state(bigint, uuid)", lower)
        self.assertIn("grant execute on function public.sniper_wallet_reconciliation_state(bigint, uuid) to service_role", lower)


if __name__ == "__main__":
    unittest.main()
