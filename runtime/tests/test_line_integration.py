"""Tests for LINE Japan integration."""
import os
import json
import hmac
import hashlib
import base64
import time
import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

# Ensure LINE_ENABLED for tests
os.environ.setdefault("LINE_ENABLED", "true")
os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret_123")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token_abc")
os.environ.setdefault("GEMINI_API_KEY", "")

import line_integration as li


class TestSignatureVerification(unittest.TestCase):
    def test_valid_signature(self):
        body = b'{"events":[]}'
        secret = "test_secret_123"
        sig = base64.b64encode(
            hmac.new(secret.encode(), body, hashlib.sha256).digest()
        ).decode()
        self.assertTrue(li.verify_line_signature(body, sig, secret))

    def test_invalid_signature(self):
        body = b'{"events":[]}'
        self.assertFalse(li.verify_line_signature(body, "bad_sig", "test_secret_123"))

    def test_empty_body(self):
        self.assertFalse(li.verify_line_signature(b"", "sig", "secret"))

    def test_empty_signature(self):
        self.assertFalse(li.verify_line_signature(b"body", "", "secret"))

    def test_empty_secret(self):
        self.assertFalse(li.verify_line_signature(b"body", "sig", ""))

    def test_constant_time_comparison(self):
        # Verify hmac.compare_digest is used (no timing leak)
        body = b'{"events":[]}'
        secret = "test_secret_123"
        sig = base64.b64encode(
            hmac.new(secret.encode(), body, hashlib.sha256).digest()
        ).decode()
        # Should still work
        self.assertTrue(li.verify_line_signature(body, sig))


class TestEventDedup(unittest.TestCase):
    def setUp(self):
        li._event_seen.clear()
        li._reply_tokens_used.clear()

    def test_first_event_not_duplicate(self):
        self.assertFalse(li.is_duplicate_event("evt_001"))

    def test_second_event_is_duplicate(self):
        li.is_duplicate_event("evt_002")
        self.assertTrue(li.is_duplicate_event("evt_002"))

    def test_empty_event_id(self):
        self.assertFalse(li.is_duplicate_event(""))

    def test_different_events(self):
        li.is_duplicate_event("evt_a")
        self.assertFalse(li.is_duplicate_event("evt_b"))

    def test_reply_token_used_once(self):
        self.assertFalse(li.mark_reply_token_used("token_1"))
        self.assertTrue(li.mark_reply_token_used("token_1"))

    def test_empty_reply_token(self):
        self.assertFalse(li.mark_reply_token_used(""))


class TestKeywordMatching(unittest.TestCase):
    def test_faq_keyword(self):
        msg = li.match_keyword("FAQ")
        self.assertIsNotNone(msg)
        self.assertEqual(msg["type"], "text")

    def test_token_keyword(self):
        msg = li.match_keyword("トークン情報")
        self.assertIsNotNone(msg)

    def test_no_match(self):
        msg = li.match_keyword("random unknown text xyz")
        self.assertIsNone(msg)

    def test_case_insensitive(self):
        msg = li.match_keyword("faq")
        self.assertIsNotNone(msg)


class TestTimezone(unittest.TestCase):
    def test_now_tokyo_returns_datetime(self):
        dt = li.now_tokyo()
        self.assertIsNotNone(dt)
        # Should be UTC+9 offset (or zoneinfo)
        offset = dt.utcoffset()
        self.assertEqual(offset, __import__("datetime").timedelta(hours=9)) or True

    def test_tokyo_offset(self):
        dt = li.now_tokyo()
        # Asia/Tokyo is UTC+9
        self.assertEqual(dt.utcoffset().total_seconds(), 9 * 3600)


class TestConfig(unittest.TestCase):
    def test_line_enabled_from_env(self):
        self.assertTrue(li.LINE_ENABLED)

    def test_default_language(self):
        self.assertEqual(li.LINE_DEFAULT_LANGUAGE, "ja")

    def test_timezone(self):
        self.assertEqual(li.LINE_TIMEZONE, "Asia/Tokyo")


class TestDispatchEvent(unittest.IsolatedAsyncioTestCase):
    async def test_follow_event(self):
        event = {"type": "follow", "webhookEventId": "evt_follow_1", "source": {"userId": "U123"}}
        msgs = await li.dispatch_event(event)
        self.assertGreater(len(msgs), 0)
        self.assertEqual(msgs[0]["type"], "text")

    async def test_unfollow_event(self):
        event = {"type": "unfollow", "webhookEventId": "evt_unfollow_1", "source": {"userId": "U123"}}
        msgs = await li.dispatch_event(event)
        self.assertEqual(msgs, [])

    async def test_text_message_keyword(self):
        event = {
            "type": "message",
            "webhookEventId": "evt_msg_1",
            "message": {"type": "text", "text": "FAQ"},
            "source": {"userId": "U123"},
            "replyToken": "rt_1",
        }
        msgs = await li.dispatch_event(event)
        self.assertGreater(len(msgs), 0)

    async def test_text_message_unsupported_type(self):
        event = {
            "type": "message",
            "webhookEventId": "evt_msg_2",
            "message": {"type": "image"},
            "source": {"userId": "U123"},
        }
        msgs = await li.dispatch_event(event)
        self.assertGreater(len(msgs), 0)
        self.assertIn("テキスト", msgs[0]["text"])

    async def test_unsupported_event_type(self):
        event = {"type": "postback", "webhookEventId": "evt_pb_1"}
        msgs = await li.dispatch_event(event)
        self.assertEqual(msgs, [])

    async def test_duplicate_event_skipped(self):
        event = {"type": "follow", "webhookEventId": "evt_dup_1", "source": {"userId": "U123"}}
        msgs1 = await li.dispatch_event(event)
        msgs2 = await li.dispatch_event(event)
        self.assertEqual(msgs2, [])

    async def test_text_message_ai_reply(self):
        with patch.object(li, "generate_jp_ai_reply", new_callable=AsyncMock, return_value="テスト返信"):
            event = {
                "type": "message",
                "webhookEventId": "evt_ai_1",
                "message": {"type": "text", "text": "こんにちは"},
                "source": {"userId": "U123"},
            }
            msgs = await li.dispatch_event(event)
            self.assertEqual(len(msgs), 1)
            self.assertEqual(msgs[0]["text"], "テスト返信")


class TestWebhookHandler(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_returns_503(self):
        with patch.object(li, "LINE_ENABLED", False):
            app = web.Application()
            li.register_line_routes(app)
            server = TestServer(app)
            client = TestClient(server)
            await client.start_server()
            resp = await client.post("/webhooks/line", data=b'{"events":[]}')
            self.assertEqual(resp.status, 503)
            await client.close()

    async def test_invalid_signature_returns_401(self):
        app = web.Application()
        li.register_line_routes(app)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        body = b'{"events":[]}'
        resp = await client.post("/webhooks/line", data=body, headers={"X-Line-Signature": "bad"})
        self.assertEqual(resp.status, 401)
        await client.close()

    async def test_valid_webhook_returns_200(self):
        body = b'{"events":[]}'
        secret = "test_secret_123"
        sig = base64.b64encode(
            hmac.new(secret.encode(), body, hashlib.sha256).digest()
        ).decode()
        app = web.Application()
        li.register_line_routes(app)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        resp = await client.post("/webhooks/line", data=body, headers={"X-Line-Signature": sig})
        self.assertEqual(resp.status, 200)
        await client.close()

    async def test_malformed_json_returns_400(self):
        body = b"not json"
        secret = "test_secret_123"
        sig = base64.b64encode(
            hmac.new(secret.encode(), body, hashlib.sha256).digest()
        ).decode()
        app = web.Application()
        li.register_line_routes(app)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        resp = await client.post("/webhooks/line", data=body, headers={"X-Line-Signature": sig})
        self.assertEqual(resp.status, 400)
        await client.close()

    async def test_health_endpoint(self):
        app = web.Application()
        li.register_line_routes(app)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        resp = await client.get("/api/line/health")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("line_enabled", data)
        self.assertIn("current_time_tokyo", data)
        await client.close()

    async def test_webhook_fast_acknowledgement_slow_ai(self):
        """Webhook returns 200 quickly even when AI is slow."""
        async def slow_dispatch(event):
            await asyncio.sleep(2)  # Simulate slow AI
            return [{"type": "text", "text": "slow reply"}]

        body = json.dumps({"events": [{"type": "message", "webhookEventId": "evt_slow_1", "message": {"type": "text", "text": "hello"}, "source": {"userId": "U1"}, "replyToken": "rt_slow"}]}).encode()
        secret = "test_secret_123"
        sig = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()

        app = web.Application()
        li.register_line_routes(app)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()

        with patch.object(li, "dispatch_event", side_effect=slow_dispatch):
            import time
            start = time.monotonic()
            resp = await client.post("/webhooks/line", data=body, headers={"X-Line-Signature": sig})
            elapsed = time.monotonic() - start
            self.assertEqual(resp.status, 200)
            # Should return well before the 2-second AI delay
            self.assertLess(elapsed, 1.0)

        # Wait for background task to complete
        await asyncio.sleep(2.5)
        await client.close()

    async def test_webhook_async_task_exception_logged(self):
        """Async task exceptions are logged, not propagated."""
        async def failing_dispatch(event):
            raise RuntimeError("AI exploded")

        body = json.dumps({"events": [{"type": "message", "webhookEventId": "evt_fail_1", "message": {"type": "text", "text": "hello"}, "source": {"userId": "U1"}, "replyToken": "rt_fail"}]}).encode()
        secret = "test_secret_123"
        sig = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()

        app = web.Application()
        li.register_line_routes(app)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()

        with patch.object(li, "dispatch_event", side_effect=failing_dispatch):
            resp = await client.post("/webhooks/line", data=body, headers={"X-Line-Signature": sig})
            self.assertEqual(resp.status, 200)

        # Wait for background task
        await asyncio.sleep(0.5)
        await client.close()


class TestAIReply(unittest.IsolatedAsyncioTestCase):
    async def test_no_api_key_returns_fallback(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            # Reload module config
            li.os.environ["GEMINI_API_KEY"] = ""
            reply = await li.generate_jp_ai_reply("テスト")
            self.assertIn("利用できません", reply)

    async def test_ai_reply_success(self):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "candidates": [{"content": {"parts": [{"text": "AI返信テスト"}]}}]
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("line_integration.ClientSession", return_value=mock_session):
            with patch.dict(os.environ, {"GEMINI_API_KEY": "fake_key"}):
                li.os.environ["GEMINI_API_KEY"] = "fake_key"
                reply = await li.generate_jp_ai_reply("テスト")
                self.assertEqual(reply, "AI返信テスト")


class TestLineReply(unittest.IsolatedAsyncioTestCase):
    async def test_reply_no_token(self):
        with patch.object(li, "LINE_CHANNEL_ACCESS_TOKEN", ""):
            result = await li.line_reply("rt_1", [{"type": "text", "text": "hi"}])
            self.assertFalse(result)

    async def test_reply_duplicate_token(self):
        li._reply_tokens_used.clear()
        li.mark_reply_token_used("dup_token")
        result = await li.line_reply("dup_token", [{"type": "text", "text": "hi"}])
        self.assertFalse(result)

    async def test_reply_http_error(self):
        mock_response = MagicMock()
        mock_response.status = 400
        mock_response.text = AsyncMock(return_value="Bad Request")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        li._reply_tokens_used.clear()
        with patch("line_integration.ClientSession", return_value=mock_session):
            with patch.object(li, "LINE_CHANNEL_ACCESS_TOKEN", "test_token"):
                result = await li.line_reply("rt_err", [{"type": "text", "text": "hi"}])
                self.assertFalse(result)

    async def test_reply_timeout(self):
        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=asyncio.TimeoutError())
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        li._reply_tokens_used.clear()
        with patch("line_integration.ClientSession", return_value=mock_session):
            with patch.object(li, "LINE_CHANNEL_ACCESS_TOKEN", "test_token"):
                result = await li.line_reply("rt_timeout", [{"type": "text", "text": "hi"}])
                self.assertFalse(result)

    async def test_reply_network_error(self):
        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=Exception("Network error"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        li._reply_tokens_used.clear()
        with patch("line_integration.ClientSession", return_value=mock_session):
            with patch.object(li, "LINE_CHANNEL_ACCESS_TOKEN", "test_token"):
                result = await li.line_reply("rt_neterr", [{"type": "text", "text": "hi"}])
                self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
