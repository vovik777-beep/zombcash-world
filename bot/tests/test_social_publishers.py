import base64
import io
import json
import unittest
from unittest.mock import patch

from PIL import Image

from bot.social_publishers import (
    bluesky_grapheme_count,
    bluesky_text_fits,
    publish_bluesky_post,
    publish_line_meme,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit=-1):
        return json.dumps(self.payload).encode("utf-8")


class SocialPublisherTests(unittest.TestCase):
    def test_bluesky_counts_combining_and_emoji_sequences(self):
        self.assertEqual(bluesky_grapheme_count("e\u0301"), 1)
        self.assertEqual(bluesky_grapheme_count("👩‍🚀"), 1)
        self.assertTrue(bluesky_text_fits("🧟" * 300))
        self.assertFalse(bluesky_text_fits("🧟" * 301))

    def test_line_sync_skips_cleanly_without_secret(self):
        with patch.dict("os.environ", {"NEWS_PUBLISH_TOKEN": ""}):
            self.assertFalse(publish_line_meme("caption", "CARTOON", b"image", 123))

    def test_line_sync_payload_and_image_are_preserved(self):
        captured = {}
        def opener(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse({"ok": True})
        result = publish_line_meme(
            "caption", "CARTOON", b"image-bytes", 123,
            token="test-token", api_url="https://example.invalid/publish",
            opener=opener,
        )
        payload = json.loads(captured["request"].data.decode("utf-8"))
        self.assertTrue(result)
        self.assertEqual(captured["timeout"], 20)
        self.assertEqual(payload["kind"], "meme")
        self.assertEqual(payload["telegramUrl"], "https://t.me/ZOMBCASH/123")
        self.assertEqual(base64.b64decode(payload["imageBase64"]), b"image-bytes")

    def test_bluesky_skips_cleanly_without_secret(self):
        self.assertFalse(publish_bluesky_post("caption", b"image", app_password=""))

    def test_bluesky_requires_native_image(self):
        def opener(request, timeout):
            return FakeResponse({"accessJwt": "jwt", "did": "did:plc:test"})
        with self.assertRaisesRegex(RuntimeError, "native image"):
            publish_bluesky_post(
                "caption", None, app_password="test", opener=opener
            )

    def test_bluesky_uploads_image_and_creates_record(self):
        image = Image.new("RGB", (24, 16), (10, 20, 30))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        image.close()
        calls = []
        def opener(request, timeout):
            calls.append((request, timeout))
            if "createSession" in request.full_url:
                return FakeResponse({"accessJwt": "jwt", "did": "did:plc:test"})
            if "uploadBlob" in request.full_url:
                return FakeResponse({"blob": {"ref": {"$link": "blob-ref"}}})
            if "createRecord" in request.full_url:
                payload = json.loads(request.data.decode("utf-8"))
                calls.append(payload)
                return FakeResponse({"uri": "at://did:plc:test/app.bsky.feed.post/1"})
            raise AssertionError(request.full_url)
        self.assertTrue(publish_bluesky_post(
            "Hello #ZombieCash https://zombcash.github.io/",
            buffer.getvalue(),
            app_password="test",
            opener=opener,
        ))
        self.assertEqual(len(calls), 4)
        record = calls[-1]["record"]
        self.assertIn("embed", record)
        self.assertGreaterEqual(len(record["facets"]), 2)
        self.assertEqual(record["embed"]["images"][0]["alt"], "ZombieCash community artwork")


if __name__ == "__main__":
    unittest.main()
