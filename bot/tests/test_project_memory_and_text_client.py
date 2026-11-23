import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from bot.project_memory import DEFAULT_MEMORY, get_memory_text, load_project_memory
from bot.pollinations_text_client import generate_text


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.payload[:limit]


class ProjectMemoryTests(unittest.TestCase):
    def test_reads_runtime_memory_without_mutating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project_memory.json"
            expected = {"contract_address": "TEST_CA", "x_url": "https://x.example"}
            path.write_text(json.dumps(expected), encoding="utf-8")
            self.assertEqual(load_project_memory(path=path), expected)
            self.assertEqual(json.loads(get_memory_text(path=path)), expected)

    def test_missing_or_invalid_memory_uses_safe_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"
            self.assertEqual(load_project_memory(path=missing), DEFAULT_MEMORY)
            invalid = Path(directory) / "invalid.json"
            invalid.write_text("{broken", encoding="utf-8")
            self.assertEqual(load_project_memory(path=invalid), DEFAULT_MEMORY)


class PollinationsTextClientTests(unittest.TestCase):
    def test_posts_openai_compatible_request_and_returns_content(self):
        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            payload = {
                "choices": [{"message": {"content": "  ZombieCash caption  "}}]
            }
            return FakeResponse(json.dumps(payload).encode())

        result = asyncio.run(generate_text(
            "write a caption", api_key="test-key", model="openai/test-model", opener=opener
        ))
        self.assertEqual(result, "ZombieCash caption")
        request, timeout = calls[0]
        self.assertEqual(timeout, 45)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        payload = json.loads(request.data.decode())
        self.assertEqual(payload["model"], "openai/test-model")
        self.assertEqual(payload["max_tokens"], 900)

    def test_missing_api_key_fails_before_network(self):
        with self.assertRaisesRegex(RuntimeError, "POLLINATIONS_API_KEY"):
            asyncio.run(generate_text("caption", api_key=""))


if __name__ == "__main__":
    unittest.main()
