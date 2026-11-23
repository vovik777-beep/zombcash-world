import os
import unittest
from unittest.mock import patch

from bot.publisher_main import _load_admin_ids, build_application


class PublisherMainTests(unittest.TestCase):
    def test_admin_ids_are_parsed_as_integers(self):
        with patch.dict(os.environ, {"ZOMBCASH_ADMIN_IDS": "123, 456,123"}):
            self.assertEqual(_load_admin_ids(), {123, 456})

    def test_missing_admin_ids_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ZOMBCASH_ADMIN_IDS"):
                _load_admin_ids()

    def test_staging_without_private_target_fails_before_application_creation(self):
        with patch.dict(os.environ, {
            "ZOMBCASH_ENV": "staging",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "GEMINI_API_KEY": "test-gemini-key",
            "POLLINATIONS_API_KEY": "test-pollinations-key",
            "ZOMBCASH_ADMIN_IDS": "123",
        }, clear=True), patch("bot.publisher_main.Application.builder") as builder:
            with self.assertRaisesRegex(RuntimeError, "private test channel"):
                build_application()
            builder.assert_not_called()

    def test_staging_production_channel_is_rejected_before_application_creation(self):
        with patch.dict(os.environ, {
            "ZOMBCASH_ENV": "staging",
            "ZOMBCASH_TELEGRAM_CHAT_ID": "@ZOMBCASH",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "GEMINI_API_KEY": "test-gemini-key",
            "POLLINATIONS_API_KEY": "test-pollinations-key",
            "ZOMBCASH_ADMIN_IDS": "123",
        }, clear=True), patch("bot.publisher_main.Application.builder") as builder:
            with self.assertRaisesRegex(RuntimeError, "forbidden"):
                build_application()
            builder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
