import asyncio
import base64
import json
import unittest
from unittest.mock import patch

from bot.pollinations_image_client import generate_image


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.payload[:limit]


class PollinationsImageClientTests(unittest.TestCase):
    def test_success_returns_normalized_bytes_and_seed(self):
        encoded = base64.b64encode(b"raw-image").decode("ascii")
        seen = []

        def opener(request, timeout):
            seen.append((request, timeout))
            return FakeResponse(json.dumps({"data": [{"b64_json": encoded}]}).encode())

        result, used_seed = asyncio.run(
            generate_image(
                "test scene", "test-key",
                normalizer=lambda data: b"normalized:" + data,
                opener=opener, seed=123,
            )
        )
        self.assertEqual(result, b"normalized:raw-image")
        self.assertEqual(used_seed, 123)
        self.assertEqual(len(seen), 1)
        request, timeout = seen[0]
        self.assertEqual(timeout, 90)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        payload = json.loads(request.data.decode())
        self.assertEqual(payload["seed"], 123)
        self.assertEqual(payload["size"], "1024x1024")

    def test_retries_once_after_invalid_response(self):
        encoded = base64.b64encode(b"raw-image").decode("ascii")
        calls = []

        def opener(request, timeout):
            payload = json.loads(request.data.decode())
            calls.append(payload)
            if len(calls) == 1:
                return FakeResponse(b'{"data":[]}')
            return FakeResponse(json.dumps({"data": [{"b64_json": encoded}]}).encode())

        result, used_seed = asyncio.run(
            generate_image(
                "scene", "test-key",
                normalizer=lambda data: data,
                opener=opener,
                sleeper=lambda _: asyncio.sleep(0),
                seed=100,
            )
        )
        self.assertEqual(result, b"raw-image")
        self.assertEqual(used_seed, 104829)
        self.assertEqual(len(calls), 2)
        self.assertIn("different camera angle", calls[1]["prompt"])

    def test_missing_key_fails_before_network(self):
        with self.assertRaisesRegex(RuntimeError, "POLLINATIONS_API_KEY"):
            asyncio.run(generate_image("scene", ""))

    def test_empty_prompt_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "prompt"):
            asyncio.run(generate_image(" ", "test-key"))

    def test_oversized_encoded_image_is_rejected(self):
        encoded = "A" * (8 * 1024 * 1024 + 1)
        calls = []

        def opener(request, timeout):
            calls.append(1)
            return FakeResponse(json.dumps({"data": [{"b64_json": encoded}]}).encode())

        with self.assertRaisesRegex(RuntimeError, "failed after 2 attempts"):
            asyncio.run(
                generate_image(
                    "scene", "test-key",
                    normalizer=lambda data: data,
                    opener=opener,
                    sleeper=lambda _: asyncio.sleep(0),
                    seed=9,
                )
            )
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
