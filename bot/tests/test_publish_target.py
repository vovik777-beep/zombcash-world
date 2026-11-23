import os
import unittest
from unittest.mock import patch

from bot.publish_target import PRODUCTION_CHAT_ID, get_publish_target


class PublishTargetTests(unittest.TestCase):
    def test_defaults_to_existing_production_channel_for_parity(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_publish_target(), PRODUCTION_CHAT_ID)

    def test_uses_explicit_staging_channel(self):
        with patch.dict(os.environ, {
            "ZOMBCASH_ENV": "staging",
            "ZOMBCASH_TELEGRAM_CHAT_ID": "@zombcash_staging_test",
        }, clear=True):
            self.assertEqual(get_publish_target(), "@zombcash_staging_test")

    def test_staging_without_explicit_target_fails_closed(self):
        with patch.dict(os.environ, {"ZOMBCASH_ENV": "staging"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "private test channel"):
                get_publish_target()

    def test_staging_cannot_target_production_channel(self):
        with patch.dict(os.environ, {
            "ZOMBCASH_ENV": "staging",
            "ZOMBCASH_TELEGRAM_CHAT_ID": "@ZOMBCASH",
        }, clear=True):
            with self.assertRaisesRegex(RuntimeError, "forbidden"):
                get_publish_target()

    def test_blank_production_target_preserves_existing_default(self):
        with patch.dict(os.environ, {"ZOMBCASH_TELEGRAM_CHAT_ID": "  "}, clear=True):
            self.assertEqual(get_publish_target(), PRODUCTION_CHAT_ID)


if __name__ == "__main__":
    unittest.main()
