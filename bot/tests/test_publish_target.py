import os
import unittest
from unittest.mock import patch

from bot.publish_target import PRODUCTION_CHAT_ID, get_publish_target


class PublishTargetTests(unittest.TestCase):
    def test_defaults_to_existing_production_channel_for_parity(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_publish_target(), PRODUCTION_CHAT_ID)

    def test_uses_explicit_staging_channel(self):
        with patch.dict(os.environ, {"ZOMBCASH_TELEGRAM_CHAT_ID": "@zombcash_staging_test"}):
            self.assertEqual(get_publish_target(), "@zombcash_staging_test")

    def test_blank_value_fails_back_to_production_default(self):
        with patch.dict(os.environ, {"ZOMBCASH_TELEGRAM_CHAT_ID": "  "}):
            self.assertEqual(get_publish_target(), PRODUCTION_CHAT_ID)


if __name__ == "__main__":
    unittest.main()
