import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bot.auto_post_state import AutoPostImageStore
from bot.hourly_publisher import _auto_post_job_once


class FakeJobQueue:
    def __init__(self):
        self.scheduled = []

    def get_jobs_by_name(self, name):
        return []

    def run_once(self, callback, *, when, data, name):
        self.scheduled.append((callback, when, data, name))


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_photo(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=777)


class FakeContext:
    def __init__(self):
        self.bot = FakeBot()
        self.application = SimpleNamespace(
            bot_data={"auto_post_retry_attempt": 0},
            job_queue=FakeJobQueue(),
        )


class HourlyPublisherTests(unittest.TestCase):
    def test_telegram_success_survives_line_and_bluesky_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = str(Path(directory) / "auto_post_state.json")
            store = AutoPostImageStore(Path(directory) / "pending")
            context = FakeContext()
            async def image_generator(style):
                image = io.BytesIO(b"jpeg")
                image.name = "zombcash_meme.jpg"
                image.zombcash_scene = "A skeleton finds a cucumber candle."
                return image

            async def caption_generator(ctx, prompt):
                self.assertIn("exact image scene", prompt)
                return "The skeleton called it a market recovery."

            def fail_line(*args):
                raise RuntimeError("queue unavailable")

            def fail_bluesky(*args):
                raise RuntimeError("Bluesky unavailable")

            with patch("bot.hourly_publisher.release_image_memory"):
                asyncio.run(_auto_post_job_once(
                    context,
                    image_store=store,
                    image_generator=image_generator,
                    caption_generator=caption_generator,
                    line_publisher=fail_line,
                    bluesky_publisher=fail_bluesky,
                    memory_loader=lambda: {
                        "contract_address": "TEST_CA",
                        "x_url": "https://x.example",
                        "telegram_url": "https://t.me/ZOMBCASH",
                    },
                    state_file=state_file,
                ))

            self.assertEqual(len(context.bot.sent), 1)
            sent = context.bot.sent[0]
            self.assertEqual(sent["chat_id"], "@ZOMBCASH")
            self.assertIn("TEST_CA", sent["caption"])
            self.assertEqual(context.application.bot_data["auto_post_retry_attempt"], 0)
            state = json.loads(Path(state_file).read_text(encoding="utf-8"))
            self.assertEqual(state["next_rotation_index"], 1)
            self.assertEqual(context.application.job_queue.scheduled[-1][1], 3600)
            self.assertIsNone(store.load("CARTOON"))

    def test_ambiguous_sending_marker_finalizes_without_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = str(Path(directory) / "auto_post_state.json")
            store = AutoPostImageStore(Path(directory) / "pending")
            image = io.BytesIO(b"previously-sent-image")
            image.name = "zombcash_meme.jpg"
            image.zombcash_scene = "Already sent scene"
            store.save(image, "CARTOON", image.zombcash_scene)
            store.mark_sending("CARTOON")
            context = FakeContext()

            with patch("bot.hourly_publisher.release_image_memory"):
                asyncio.run(_auto_post_job_once(
                    context, image_store=store, state_file=state_file,
                    image_generator=lambda style: self.fail("must not regenerate"),
                    caption_generator=lambda *args: self.fail("must not regenerate caption"),
                ))

            self.assertEqual(context.bot.sent, [])
            state = json.loads(Path(state_file).read_text(encoding="utf-8"))
            self.assertEqual(state["next_rotation_index"], 1)
            self.assertIsNone(store.load("CARTOON"))


if __name__ == "__main__":
    unittest.main()
