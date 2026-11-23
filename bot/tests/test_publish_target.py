import os
import unittest
from unittest.mock import patch

from bot.publish_target import (PRODUCTION_CHAT_ID, PRODUCTION_NEWS_API_URL, get_publish_api_url, get_publish_target)


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

    def test_production_api_endpoint_is_preserved_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_publish_api_url(), PRODUCTION_NEWS_API_URL)

    def test_staging_does_not_inherit_production_api_endpoint(self):
        with patch.dict(os.environ, {"ZOMBCASH_ENV": "staging"}, clear=True):
            self.assertIsNone(get_publish_api_url())

    def test_staging_uses_only_explicit_api_endpoint(self):
        with patch.dict(os.environ, {
            "ZOMBCASH_ENV": "staging",
            "NEWS_PUBLISH_API_URL": "https://staging-api.example.test/news",
        }, clear=True):
            self.assertEqual(get_publish_api_url(), "https://staging-api.example.test/news")


if __name__ == "__main__":
    unittest.main()
