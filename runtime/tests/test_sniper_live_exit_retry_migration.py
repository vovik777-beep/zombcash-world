from pathlib import Path
import unittest


class SniperLiveExitRetryMigrationTests(unittest.TestCase):
    def test_retry_rpc_allows_only_confirmed_or_cooldown_expired_failed_orders(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "supabase"
            / "migrations"
            / "20261128233200_sniper_live_exit_retry_cooldown.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("public.sniper_live_exit_retry_allowed", migration)
        self.assertIn("v_status='failed'", migration)
        self.assertIn("v_status='confirmed'", migration)
        self.assertIn("v_updated_at <= pg_catalog.now()", migration)
        self.assertIn("Unknown and in-flight outcomes are never retried automatically", migration)

    def test_failed_exit_attempt_is_recorded_on_the_matching_open_position(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "supabase"
            / "migrations"
            / "20261128233200_sniper_live_exit_retry_cooldown.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("public.sniper_live_position_note_exit_attempt", migration)
        self.assertIn("p.status='open'", migration)
        self.assertIn("o.side='sell'", migration)
        self.assertIn("o.status='failed'", migration)
        self.assertIn("o.wallet_id=p.wallet_id", migration)
        self.assertIn("o.mint=p.mint", migration)

    def test_live_health_requires_retry_support_before_arming(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "supabase"
            / "migrations"
            / "20261128233200_sniper_live_exit_retry_cooldown.sql"
        ).read_text(encoding="utf-8")
        api = (Path(__file__).resolve().parents[1] / "api_server.py").read_text(encoding="utf-8")

        self.assertIn("as exit_retry", migration)
        self.assertIn("as exit_attempt_recording", migration)
        self.assertIn("and exit_retry and exit_attempt_recording", migration)
        self.assertIn('checks.get("exit_retry") is True', api)
        self.assertIn('checks.get("exit_attempt_recording") is True', api)


if __name__ == "__main__":
    unittest.main()
