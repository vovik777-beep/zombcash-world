"""Tests for Telegram QR login fallback."""
import os
import json
import time
import unittest
import base64
import hashlib
import hmac
from unittest.mock import Mock, patch, AsyncMock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test_bot_token_12345")
os.environ.setdefault("SUPABASE_URL", "")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "")

import api_server as api


class TestAuthChallengeCreation(unittest.IsolatedAsyncioTestCase):
    """Test challenge creation endpoint."""

    def setUp(self):
        api.TELEGRAM_AUTH_CHALLENGES.clear()
        api._AUTH_CHALLENGE_RATE_LIMIT.clear()

    async def test_start_rejects_wrong_origin(self):
        """Challenge creation must reject requests from an untrusted origin."""
        request = Mock()
        request.headers = {"Origin": "https://evil.example"}
        request.remote = "192.0.2.10"
        result = await api.telegram_auth_start(request)
        self.assertEqual(result.status, 403)
        self.assertEqual(api.TELEGRAM_AUTH_CHALLENGES, {})

    async def test_start_returns_real_nonce_and_bot_url(self):
        """The endpoint must generate and store a valid nonce in its response."""
        request = Mock()
        request.headers = {
            "Origin": api.ALLOWED_ORIGIN,
            "CF-Connecting-IP": "192.0.2.11",
        }
        request.remote = "192.0.2.11"
        result = await api.telegram_auth_start(request)
        payload = json.loads(result.text)
        nonce = payload["nonce"]
        self.assertEqual(result.status, 200)
        self.assertTrue(payload["ok"])
        self.assertRegex(nonce, r"^[A-Za-z0-9_-]{20,80}$")
        self.assertIn(nonce, api.TELEGRAM_AUTH_CHALLENGES)
        self.assertTrue(payload["botUrl"].endswith("auth_" + nonce))
        self.assertEqual(payload["expiresIn"], 120)

    async def test_challenge_expiry_is_120_seconds(self):
        """The stored challenge must expire 120 seconds after creation."""
        before = int(time.time())
        request = Mock()
        request.headers = {
            "Origin": api.ALLOWED_ORIGIN,
            "CF-Connecting-IP": "192.0.2.12",
        }
        request.remote = "192.0.2.12"
        result = await api.telegram_auth_start(request)
        payload = json.loads(result.text)
        challenge = api.TELEGRAM_AUTH_CHALLENGES[payload["nonce"]]
        self.assertGreaterEqual(challenge["expires"], before + 120)
        self.assertLessEqual(challenge["expires"], int(time.time()) + 120)

    def test_rate_limit_config(self):
        """Rate limit should be 5 per 60 seconds per IP."""
        self.assertEqual(api._AUTH_RATE_LIMIT_MAX, 5)
        self.assertEqual(api._AUTH_RATE_LIMIT_WINDOW, 60)


class TestAuthValidate(unittest.IsolatedAsyncioTestCase):
    """Test challenge validation endpoint."""

    async def test_validate_valid_nonce(self):
        """Valid nonce should return ok=True, valid=True."""
        api.TELEGRAM_AUTH_CHALLENGES.clear()
        nonce = "test_nonce_12345678901234567890"
        api.TELEGRAM_AUTH_CHALLENGES[nonce] = {
            "expires": int(time.time()) + 120,
            "result": None,
            "used": False,
            "confirmed": False,
        }
        # Create a mock request
        request = Mock()
        request.json = AsyncMock(return_value={"nonce": nonce})
        result = await api.telegram_auth_validate(request)
        # Result is a web.Response; check status
        self.assertEqual(result.status, 200)

    async def test_validate_expired_nonce(self):
        """Expired nonce should return 410."""
        api.TELEGRAM_AUTH_CHALLENGES.clear()
        nonce = "expired_nonce_123456789012345"
        api.TELEGRAM_AUTH_CHALLENGES[nonce] = {
            "expires": int(time.time()) - 10,
            "result": None,
            "used": False,
            "confirmed": False,
        }
        request = Mock()
        request.json = AsyncMock(return_value={"nonce": nonce})
        result = await api.telegram_auth_validate(request)
        self.assertEqual(result.status, 410)

    async def test_validate_used_nonce(self):
        """Used nonce should return 410."""
        api.TELEGRAM_AUTH_CHALLENGES.clear()
        nonce = "used_nonce_123456789012345678"
        api.TELEGRAM_AUTH_CHALLENGES[nonce] = {
            "expires": int(time.time()) + 120,
            "result": {"token": "test", "profile": {}},
            "used": True,
            "confirmed": True,
        }
        request = Mock()
        request.json = AsyncMock(return_value={"nonce": nonce})
        result = await api.telegram_auth_validate(request)
        self.assertEqual(result.status, 410)

    async def test_validate_invalid_nonce_format(self):
        """Invalid nonce format should return 400."""
        request = Mock()
        request.json = AsyncMock(return_value={"nonce": "short"})
        result = await api.telegram_auth_validate(request)
        self.assertEqual(result.status, 400)

    async def test_validate_missing_nonce(self):
        """Missing nonce should return 400."""
        request = Mock()
        request.json = AsyncMock(return_value={})
        result = await api.telegram_auth_validate(request)
        self.assertEqual(result.status, 400)

    async def test_validate_nonexistent_nonce(self):
        """Nonexistent nonce should return 410."""
        api.TELEGRAM_AUTH_CHALLENGES.clear()
        request = Mock()
        request.json = AsyncMock(return_value={"nonce": "nonexistent_nonce_12345678901234"})
        result = await api.telegram_auth_validate(request)
        self.assertEqual(result.status, 410)


class TestAuthComplete(unittest.IsolatedAsyncioTestCase):
    """Test challenge completion endpoint."""

    async def test_complete_requires_confirmed(self):
        """Completion without confirmed=True should return 400."""
        api.TELEGRAM_AUTH_CHALLENGES.clear()
        nonce = "test_nonce_complete_12345678901"
        api.TELEGRAM_AUTH_CHALLENGES[nonce] = {
            "expires": int(time.time()) + 120,
            "result": None,
            "used": False,
            "confirmed": False,
        }
        request = Mock()
        request.json = AsyncMock(return_value={
            "nonce": nonce,
            "user": {"id": "123", "first_name": "Test", "auth_date": str(int(time.time()))},
            "confirmed": False,
        })
        result = await api.telegram_auth_complete(request)
        self.assertEqual(result.status, 400)

    async def test_complete_expired_nonce(self):
        """Completion of expired nonce should return 410."""
        api.TELEGRAM_AUTH_CHALLENGES.clear()
        nonce = "expired_complete_1234567890123"
        api.TELEGRAM_AUTH_CHALLENGES[nonce] = {
            "expires": int(time.time()) - 10,
            "result": None,
            "used": False,
            "confirmed": False,
        }
        request = Mock()
        request.json = AsyncMock(return_value={
            "nonce": nonce,
            "user": {"id": "123", "first_name": "Test", "auth_date": str(int(time.time()))},
            "confirmed": True,
        })
        result = await api.telegram_auth_complete(request)
        self.assertEqual(result.status, 410)

    async def test_complete_used_nonce(self):
        """Completion of used nonce should return 410."""
        api.TELEGRAM_AUTH_CHALLENGES.clear()
        nonce = "used_complete_123456789012345"
        api.TELEGRAM_AUTH_CHALLENGES[nonce] = {
            "expires": int(time.time()) + 120,
            "result": {"token": "test", "profile": {}},
            "used": True,
            "confirmed": True,
        }
        request = Mock()
        request.json = AsyncMock(return_value={
            "nonce": nonce,
            "user": {"id": "123", "first_name": "Test", "auth_date": str(int(time.time()))},
            "confirmed": True,
        })
        result = await api.telegram_auth_complete(request)
        self.assertEqual(result.status, 410)

    async def test_complete_missing_user(self):
        """Completion without user payload should return 400."""
        api.TELEGRAM_AUTH_CHALLENGES.clear()
        nonce = "missing_user_12345678901234"
        api.TELEGRAM_AUTH_CHALLENGES[nonce] = {
            "expires": int(time.time()) + 120,
            "result": None,
            "used": False,
            "confirmed": False,
        }
        request = Mock()
        request.json = AsyncMock(return_value={
            "nonce": nonce,
            "confirmed": True,
        })
        result = await api.telegram_auth_complete(request)
        self.assertEqual(result.status, 400)


class TestAuthStatus(unittest.IsolatedAsyncioTestCase):
    """Test challenge status polling endpoint."""

    async def test_status_pending(self):
        """Pending challenge should return status=pending."""
        api.TELEGRAM_AUTH_CHALLENGES.clear()
        nonce = "pending_status_123456789012"
        api.TELEGRAM_AUTH_CHALLENGES[nonce] = {
            "expires": int(time.time()) + 120,
            "result": None,
            "used": False,
            "confirmed": False,
        }
        request = Mock()
        request.headers = {"Origin": "https://zombcash.github.io"}
        request.query = {"nonce": nonce}
        result = await api.telegram_auth_status(request)
        self.assertEqual(result.status, 200)

    async def test_status_expired(self):
        """Expired challenge should return 410."""
        api.TELEGRAM_AUTH_CHALLENGES.clear()
        nonce = "expired_status_123456789012"
        api.TELEGRAM_AUTH_CHALLENGES[nonce] = {
            "expires": int(time.time()) - 10,
            "result": None,
            "used": False,
            "confirmed": False,
        }
        request = Mock()
        request.headers = {"Origin": "https://zombcash.github.io"}
        request.query = {"nonce": nonce}
        result = await api.telegram_auth_status(request)
        self.assertEqual(result.status, 410)

    async def test_status_wrong_origin(self):
        """Wrong origin should return 403."""
        request = Mock()
        request.headers = {"Origin": "https://evil.com"}
        request.query = {"nonce": "test"}
        result = await api.telegram_auth_status(request)
        self.assertEqual(result.status, 403)


class TestRateLimiting(unittest.TestCase):
    """Test rate limiting for auth challenges."""

    def setUp(self):
        api._AUTH_CHALLENGE_RATE_LIMIT.clear()

    def test_rate_limit_allows_initial(self):
        """First request should be allowed."""
        self.assertTrue(api._check_auth_rate_limit("192.168.1.1"))

    def test_rate_limit_blocks_excess(self):
        """Exceeding rate limit should be blocked."""
        ip = "192.168.1.2"
        for _ in range(5):
            api._check_auth_rate_limit(ip)
        self.assertFalse(api._check_auth_rate_limit(ip))

    def test_rate_limit_per_ip(self):
        """Rate limit should be per IP."""
        for _ in range(api._AUTH_RATE_LIMIT_MAX):
            self.assertTrue(api._check_auth_rate_limit("10.0.0.1"))
            self.assertTrue(api._check_auth_rate_limit("10.0.0.2"))
        self.assertFalse(api._check_auth_rate_limit("10.0.0.1"))
        self.assertFalse(api._check_auth_rate_limit("10.0.0.2"))


class TestChallengeSingleUse(unittest.IsolatedAsyncioTestCase):
    """Test single-use consume semantics."""

    def setUp(self):
        api.TELEGRAM_AUTH_CHALLENGES.clear()

    async def test_consumed_challenge_is_rejected_by_validate_endpoint(self):
        """A consumed challenge must be rejected by the real validation handler."""
        nonce = "single_use_test_123456789012"
        api.TELEGRAM_AUTH_CHALLENGES[nonce] = {
            "expires": int(time.time()) + 120,
            "result": {"token": "test", "profile": {}},
            "used": True,
            "confirmed": True,
        }
        request = Mock()
        request.json = AsyncMock(return_value={"nonce": nonce})
        result = await api.telegram_auth_validate(request)
        self.assertEqual(result.status, 410)
        self.assertNotIn(nonce, api.TELEGRAM_AUTH_CHALLENGES)


class TestExistingAuthUnchanged(unittest.TestCase):
    """Verify existing auth flow remains intact."""

    def test_session_key_derivation(self):
        """Session key should be derived from bot token."""
        key = api._survivor_session_key()
        self.assertIsInstance(key, bytes)
        self.assertEqual(len(key), 32)

    def test_session_issue_and_read(self):
        """Issued session should be readable."""
        token = api._issue_survivor_session("12345")
        # Create a mock request
        request = Mock()
        request.headers = {"Authorization": "Bearer " + token}
        result = api._read_survivor_session(request)
        self.assertEqual(result, "12345")

    def test_session_expired(self):
        """Expired session should return None."""
        # Create an expired token
        import base64, json, time
        payload = {"sub": "12345", "exp": int(time.time()) - 100}
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
        signature = hmac.new(api._survivor_session_key(), body.encode(), hashlib.sha256).digest()
        sig = base64.urlsafe_b64encode(signature).decode().rstrip("=")
        token = body + "." + sig
        request = Mock()
        request.headers = {"Authorization": "Bearer " + token}
        result = api._read_survivor_session(request)
        self.assertIsNone(result)

    def test_session_invalid_signature(self):
        """Session with wrong signature should return None."""
        import base64, json
        payload = {"sub": "12345", "exp": int(time.time()) + 3600}
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
        token = body + ".invalidsignature"
        request = Mock()
        request.headers = {"Authorization": "Bearer " + token}
        result = api._read_survivor_session(request)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()