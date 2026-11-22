import asyncio
import unittest

from bot.ai_text_client import generate_with_fallback, retry_after_seconds


class GeminiQuotaError(Exception):
    status_code = 429


class AITextClientTests(unittest.TestCase):
    def test_retry_after_parses_hours_minutes_seconds(self):
        self.assertEqual(retry_after_seconds("Please retry in 1h 2m 3s"), 3723)

    def test_retry_after_enforces_five_minute_minimum(self):
        self.assertEqual(retry_after_seconds("retry in 12s"), 300)

    def test_retry_after_uses_default_when_missing(self):
        self.assertEqual(retry_after_seconds("quota exhausted", 7200), 7200)

    def test_uses_first_successful_gemini_model(self):
        calls = []
        def gemini(model, prompt):
            calls.append(model)
            return "caption" if model == "model-b" else ""
        async def fallback(prompt):
            return "fallback"
        result, cooldown = asyncio.run(generate_with_fallback(
            "prompt", gemini_generate=gemini, pollinations_generate=fallback,
            models=("model-a", "model-b"), sleeper=lambda _: asyncio.sleep(0),
        ))
        self.assertEqual(result, "caption")
        self.assertEqual(cooldown, 0.0)
        self.assertEqual(calls, ["model-a", "model-b"])

    def test_quota_failure_uses_fallback_and_sets_cooldown(self):
        now = [1000.0]
        def gemini(model, prompt):
            raise GeminiQuotaError("quota exhausted")
        async def fallback(prompt):
            return "fallback caption"
        result, cooldown = asyncio.run(generate_with_fallback(
            "prompt", gemini_generate=gemini, pollinations_generate=fallback,
            cooldown_seconds=900, models=("model-a", "model-b"),
            monotonic=lambda: now[0], sleeper=lambda _: asyncio.sleep(0),
        ))
        self.assertEqual(result, "fallback caption")
        self.assertEqual(cooldown, 1300.0)

    def test_active_cooldown_skips_gemini(self):
        calls = []
        def gemini(model, prompt):
            calls.append(model)
            return "gemini"
        async def fallback(prompt):
            return "fallback"
        result, cooldown = asyncio.run(generate_with_fallback(
            "prompt", gemini_generate=gemini, pollinations_generate=fallback,
            cooldown_until=2000, monotonic=lambda: 1500,
        ))
        self.assertEqual(result, "fallback")
        self.assertEqual(calls, [])
        self.assertEqual(cooldown, 2000)

    def test_fallback_failure_chains_original_gemini_error(self):
        original = RuntimeError("Gemini down")
        def gemini(model, prompt):
            raise original
        async def fallback(prompt):
            raise OSError("Pollinations down")
        with self.assertRaisesRegex(RuntimeError, "Gemini is unavailable") as raised:
            asyncio.run(generate_with_fallback(
                "prompt", gemini_generate=gemini, pollinations_generate=fallback,
                models=("model-a",), sleeper=lambda _: asyncio.sleep(0),
            ))
        self.assertIs(raised.exception.__cause__, original)


if __name__ == "__main__":
    unittest.main()
