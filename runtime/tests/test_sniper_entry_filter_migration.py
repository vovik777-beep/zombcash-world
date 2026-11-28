from pathlib import Path
import unittest


class SniperEntryFilterMigrationTests(unittest.TestCase):
    def test_paper_and_live_entry_gates_enforce_buy_count_and_socials(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "supabase"
            / "migrations"
            / "20261128233100_sniper_enforce_entry_social_and_buy_count.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("v_user.min_buy_count", migration)
        self.assertIn("s.min_buy_count", migration)
        self.assertIn("v_user.require_socials", migration)
        self.assertIn("s.require_socials", migration)
        self.assertIn("sniper.token_social_status", migration)
        self.assertIn("ss.has_socials is true", migration)

    def test_entry_filters_are_fail_closed_and_service_role_only(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "supabase"
            / "migrations"
            / "20261128233100_sniper_enforce_entry_social_and_buy_count.sql"
        ).read_text(encoding="utf-8")

        self.assertIn(") < greatest(1,v_user.min_buy_count) then continue; end if;", migration)
        self.assertIn(") >= greatest(1,s.min_buy_count)", migration)
        self.assertIn("REVOKE ALL ON FUNCTION public.sniper_paper_engine_process(text)", migration)
        self.assertIn("REVOKE ALL ON FUNCTION public.sniper_live_entry_candidates()", migration)
        self.assertIn("TO service_role", migration)


if __name__ == "__main__":
    unittest.main()
