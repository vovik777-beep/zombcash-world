import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.bluesky_admin import bskycheck_command, register_bluesky_admin_command


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


class BlueskyAdminCommandTests(unittest.TestCase):
    def test_non_admin_is_rejected(self):
        message = FakeMessage()
        context = SimpleNamespace(application=SimpleNamespace(bot_data={"is_admin": lambda update: False}))
        update = SimpleNamespace(effective_message=message)
        asyncio.run(bskycheck_command(update, context))
        self.assertEqual(message.replies, ["⛔ Admin only."])

    def test_missing_secret_explains_no_post_was_sent(self):
        message = FakeMessage()
        context = SimpleNamespace(application=SimpleNamespace(bot_data={"is_admin": lambda update: True}))
        update = SimpleNamespace(effective_message=message)
        with patch.dict("os.environ", {"BLUESKY_APP_PASSWORD": ""}):
            asyncio.run(bskycheck_command(update, context))
        self.assertIn("No post was sent", message.replies[0])

    def test_success_message_confirms_no_post_published(self):
        message = FakeMessage()
        context = SimpleNamespace(application=SimpleNamespace(bot_data={"is_admin": lambda update: True}))
        update = SimpleNamespace(effective_message=message)
        with patch.dict("os.environ", {"BLUESKY_APP_PASSWORD": "test"}), \
             patch("bot.bluesky_admin.asyncio.to_thread", new=AsyncMock(return_value={"ok": True, "handle": "zombcash.bsky.social"})):
            asyncio.run(bskycheck_command(update, context))
        self.assertIn("authentication works", message.replies[0])
        self.assertIn("No post was published", message.replies[0])

    def test_registration_requires_admin_guard_and_is_idempotent(self):
        added = []
        application = SimpleNamespace(bot_data={}, add_handler=lambda handler: added.append(handler))
        with self.assertRaisesRegex(ValueError, "is_admin_fn"):
            register_bluesky_admin_command(application, None)
        register_bluesky_admin_command(application, lambda update: True)
        register_bluesky_admin_command(application, lambda update: True)
        self.assertTrue(callable(application.bot_data["is_admin"]))
        self.assertEqual(len(added), 1)


if __name__ == "__main__":
    unittest.main()
