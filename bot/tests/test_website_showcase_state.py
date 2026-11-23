import tempfile
import unittest
from pathlib import Path

from bot.website_showcase import resolve_state_file


class WebsiteShowcaseStateTests(unittest.TestCase):
    def test_prefers_existing_app_root_state_file(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "site_promo_state.json"
            expected.write_text('{"last_page_key": "home"}', encoding="utf-8")
            self.assertEqual(
                resolve_state_file(working_directory=directory),
                str(expected),
            )

    def test_explicit_state_file_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = str(Path(directory) / "custom-state.json")
            self.assertEqual(
                resolve_state_file(expected, working_directory="/unused"),
                expected,
            )


if __name__ == "__main__":
    unittest.main()
