"""Mock-only behavioural tests for LINE news publishing."""
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock

from line_publisher import LinePublisher


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status = status
        self.headers = headers or {}
        self.payload = payload or {}
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def read(self): return b"{}"
    async def json(self): return self.payload


class FakeSession:
    responses = []
    posts = []
    def __init__(self, *args, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        value = self.responses.pop(0) if self.responses else FakeResponse()
        if isinstance(value, Exception):
            raise value
        return value

    def get(self, url, **kwargs):
        self.posts.append((url, kwargs))
        value = self.responses.pop(0) if self.responses else FakeResponse()
        if isinstance(value, Exception):
            raise value
        return value


ITEM = {
    "id": "meme-a",
    "kind": "meme",
    "category": "ZombieCash Memes",
    "source": "ZombieCash Community",
    "title": "Market update",
    "link": "https://t.me/ZOMBCASH/100",
    "published": "2026-11-13T10:00:00Z",
    "summary": "Bitcoin moved.",
    "commentary": "Original ZombieCash community artwork.",
    "imageUrl": "",
    "enriched": True,
}
JA = {"title": "市場ニュース", "summary": "ビットコインが動きました。", "commentary": "市場はまたシャベルを見つけました。"}


class TestLinePublisher(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = self.temp.name
        self.items = [dict(ITEM)]
        FakeSession.posts = []
        FakeSession.responses = []
        self.env = {
            "LINE_ENABLED": "true",
            "LINE_BROADCAST_ENABLED": "true",
            "LINE_CHANNEL_ACCESS_TOKEN": "dummy-test-token",
            "LINE_DAILY_LIMIT": "2",
        }
        self.patchers = []
        for name, value in self.env.items():
            patcher = unittest.mock.patch.dict(os.environ, {name: value})
            patcher.start()
            self.patchers.append(patcher)
        self.publisher = LinePublisher(
            lambda: self.items,
            self.data,
            self.data,
            session_factory=FakeSession,
            translate=AsyncMock(return_value=JA),
            sleep=AsyncMock(),
        )
        self.publisher._append({"event": "bootstrap", "count": 0})

    def tearDown(self):
        for patcher in self.patchers:
            patcher.stop()
        self.temp.cleanup()

    async def test_news_items_are_never_broadcast_to_line(self):
        self.items = [{
            **ITEM,
            "id": "news-only",
            "kind": "news",
            "source": "CoinDesk",
            "link": "https://example.com/news/only",
        }]
        self.assertEqual(await self.publisher.run_once(), "idle")
        self.assertEqual(FakeSession.posts, [])

    async def test_default_line_broadcast_cadence_is_hourly(self):
        from line_publisher import publish_interval_seconds, daily_publish_limit
        os.environ.pop("LINE_PUBLISH_INTERVAL_HOURS", None)
        os.environ.pop("LINE_DAILY_LIMIT", None)
        self.assertEqual(publish_interval_seconds(), 3600)
        self.assertEqual(daily_publish_limit(), 24)

    async def test_disabled_has_no_network(self):
        os.environ["LINE_ENABLED"] = "false"
        self.assertEqual(await self.publisher.run_once(), "disabled")
        self.assertEqual(FakeSession.posts, [])

    async def test_broadcast_uses_existing_image_or_text(self):
        self.assertEqual(await self.publisher.run_once(), "sent")
        payload = FakeSession.posts[-1][1]["json"]
        self.assertEqual([m["type"] for m in payload["messages"]], ["text"])
        self.assertIn("市場ニュース", payload["messages"][0]["text"])

    async def test_meme_image_uses_original_asset_url(self):
        image_id = "a" * 64
        with open(os.path.join(self.data, image_id + ".jpg"), "wb") as handle:
            handle.write(b"original")
        self.items[0]["imageUrl"] = "/api/news/image/" + image_id
        self.assertEqual(await self.publisher.run_once(), "sent")
        payload = FakeSession.posts[-1][1]["json"]
        self.assertEqual(payload["messages"][0]["type"], "image")
        self.assertEqual(payload["messages"][0]["originalContentUrl"], payload["messages"][0]["previewImageUrl"])
        self.assertIn("/api/news/image/" + image_id, payload["messages"][0]["originalContentUrl"])

    async def test_meme_uses_japanese_meme_format_and_original_image(self):
        image_id = "b" * 64
        with open(os.path.join(self.data, image_id + ".jpg"), "wb") as handle:
            handle.write(b"original")
        self.items[0].update({
            "kind": "meme",
            "category": "ZombieCash Memes",
            "source": "ZombieCash Community",
            "title": "When the market crashes again",
            "summary": "When the market crashes again",
            "commentary": "Original ZombieCash community artwork.",
            "link": "https://t.me/ZOMBCASH/123",
            "imageUrl": "/api/news/image/" + image_id,
        })
        self.assertEqual(await self.publisher.run_once(), "sent")
        messages = FakeSession.posts[-1][1]["json"]["messages"]
        self.assertEqual([m["type"] for m in messages], ["image", "text"])
        self.assertIn("ミーム", messages[-1]["text"])
        self.assertIn("ZombieCash Memes", messages[-1]["text"])
        self.assertIn("市場ニュース", messages[-1]["text"])

    async def test_oldest_unsent_meme_is_not_starved_by_newer_news(self):
        meme = {
            **ITEM,
            "id": "meme-old",
            "kind": "meme",
            "category": "Undead After Dark",
            "source": "ZombieCash Community",
            "title": "A ZombieCash meme",
            "summary": "A ZombieCash meme",
            "commentary": "Original artwork.",
            "link": "https://t.me/ZOMBCASH/5630",
            "published": "2026-11-14T09:31:00Z",
        }
        newer_news = {
            **ITEM,
            "id": "news-new",
            "link": "https://example.com/news/newer",
            "published": "2026-11-14T09:40:00Z",
        }
        # API stores newest-first; FIFO selection must still pick the older meme.
        self.items = [newer_news, meme]
        self.assertEqual(await self.publisher.run_once(), "sent")
        body = FakeSession.posts[-1][1]["json"]["messages"][-1]["text"]
        self.assertIn("ミーム", body)
        self.assertIn("Undead After Dark", body)

    async def test_broadcast_request_id_is_saved_for_insights(self):
        FakeSession.responses = [FakeResponse(headers={"X-Line-Request-Id": "req-123"})]
        self.assertEqual(await self.publisher.run_once(), "sent")
        rows = [json.loads(row) for row in self.publisher.journal_path.read_text(encoding="utf-8").splitlines()]
        sent = next(row for row in rows if row.get("event") == "sent")
        self.assertEqual(sent["request_id"], "req-123")
        self.assertEqual(sent["kind"], "meme")

    async def test_collect_insights_persists_message_metrics(self):
        self.publisher._append({
            "event": "sent", "source_id": "meme-1", "request_id": "req-meme",
            "kind": "meme", "category": "Undead After Dark",
        })
        FakeSession.responses = [FakeResponse(payload={
            "overview": {"requestId": "req-meme", "delivered": 25, "uniqueImpression": 12, "uniqueClick": 3},
            "messages": [], "clicks": [],
        })]
        self.assertEqual(await self.publisher.collect_insights(), 1)
        rows = [json.loads(row) for row in self.publisher.journal_path.read_text(encoding="utf-8").splitlines()]
        insight = next(row for row in rows if row.get("event") == "insight")
        self.assertEqual(insight["stats"]["overview"]["delivered"], 25)
        self.assertEqual(insight["kind"], "meme")

    async def test_failed_insight_request_is_throttled_for_one_hour(self):
        self.publisher._append({
            "event": "sent", "source_id": "old-broadcast", "request_id": "req-denied",
            "kind": "news",
        })
        FakeSession.responses = [FakeResponse(status=403)]
        self.assertEqual(await self.publisher.collect_insights(), 0)
        first_request_count = len(FakeSession.posts)
        self.assertEqual(first_request_count, 1)

        # The failure journal entry suppresses an immediate retry on the next tick.
        self.assertEqual(await self.publisher.collect_insights(), 0)
        self.assertEqual(len(FakeSession.posts), first_request_count)
        rows = [json.loads(row) for row in self.publisher.journal_path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(row.get("event") == "insight_error" and row.get("request_id") == "req-denied" for row in rows))

    async def test_deduplication(self):
        self.assertEqual(await self.publisher.run_once(), "sent")
        self.assertEqual(await self.publisher.run_once(), "idle")
        self.assertEqual(len(FakeSession.posts), 1)

    async def test_retries_transient_api_error(self):
        FakeSession.responses = [FakeResponse(503), FakeResponse(200)]
        self.assertEqual(await self.publisher.run_once(), "sent")
        self.assertEqual(len(FakeSession.posts), 2)

    async def test_rate_limit_pauses_same_day(self):
        FakeSession.responses = [FakeResponse(429)]
        self.assertEqual(await self.publisher.run_once(), "paused")
        self.assertEqual(await self.publisher.run_once(), "paused")
        self.assertEqual(len(FakeSession.posts), 1)
        events = [json.loads(row) for row in self.publisher.journal_path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(row.get("event") == "paused" for row in events))

    async def test_daily_limit_pauses(self):
        self.publisher._append({"event": "sent", "source_id": "yesterday", "at": __import__("time").time()})
        self.publisher._append({"event": "sent", "source_id": "today-2", "at": __import__("time").time()})
        self.assertEqual(await self.publisher.run_once(), "paused")
        self.assertEqual(FakeSession.posts, [])

    async def test_pauses_immediately_after_reaching_daily_limit(self):
        os.environ["LINE_DAILY_LIMIT"] = "1"
        self.assertEqual(await self.publisher.run_once(), "sent")
        self.items.append({**ITEM, "id": "new-item", "link": "https://example.com/news/new"})
        self.assertEqual(await self.publisher.run_once(), "paused")
        self.assertEqual(len(FakeSession.posts), 1)
        events = [json.loads(row) for row in self.publisher.journal_path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(row.get("event") == "paused" and row.get("reason") == "LINE_DAILY_LIMIT reached" for row in events))

    async def test_test_push_requires_exact_allowlisted_user(self):
        os.environ["LINE_TEST_USER_ID"] = "U123"
        with self.assertRaises(PermissionError):
            await self.publisher.send_test_message("U999")
        self.assertEqual(FakeSession.posts, [])
        self.assertTrue(await self.publisher.send_test_message("U123"))
        self.assertIn("/message/push", FakeSession.posts[-1][0])

    async def test_first_run_marks_existing_content_as_baseline(self):
        self.publisher.journal_path.unlink()
        self.assertEqual(await self.publisher.run_once(), "bootstrapped")
        self.assertEqual(FakeSession.posts, [])
        self.items.append({**ITEM, "id": "new-meme", "link": "https://t.me/ZOMBCASH/101"})
        self.assertEqual(await self.publisher.run_once(), "sent")

    async def test_journal_corruption_fails_closed(self):
        self.publisher.journal_path.write_text("{bad json}\n", encoding="utf-8")
        with self.assertRaises(Exception):
            await self.publisher.run_once()
        self.assertEqual(FakeSession.posts, [])


if __name__ == "__main__":
    unittest.main()



def test_preparation_failure_code_classifies_known_safe_reasons():
    from line_publisher import preparation_failure_code

    assert preparation_failure_code(RuntimeError("Japanese translation API returned HTTP 429")) == "gemini_http_429"
    assert preparation_failure_code(RuntimeError("GEMINI_API_KEY is required to prepare Japanese LINE copy")) == "gemini_key_missing"
    assert preparation_failure_code(RuntimeError("Invalid GEMINI_LINE_JP_MODEL")) == "invalid_gemini_model_config"
    assert preparation_failure_code(RuntimeError("Japanese translation response omitted required fields")) == "gemini_response_missing_fields"
    assert preparation_failure_code(ValueError("Published news item has no HTTPS source link")) == "source_link_invalid"
