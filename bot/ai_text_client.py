"""Provider fallback policy for the ZombieCash text-generation adapter.

The caller owns prompt composition and supplies provider functions. This keeps
Telegram handlers, project-memory files and unrelated bot modules out of scope.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Callable, Awaitable


DEFAULT_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
)


def retry_after_seconds(error_text, default_seconds=21600):
    """Parse Gemini's 'retry in ...' hint; enforce a five-minute minimum."""
    text_value = str(error_text or "").lower()
    match = re.search(
        r"retry in\s+(?:(\d+(?:\.\d+)?)h)?\s*(?:(\d+(?:\.\d+)?)m)?\s*(?:(\d+(?:\.\d+)?)s)?",
        text_value,
    )
    if not match:
        return default_seconds
    hours, minutes, seconds = (float(value or 0) for value in match.groups())
    parsed = int(hours * 3600 + minutes * 60 + seconds)
    return max(300, parsed) if parsed else default_seconds


async def generate_with_fallback(
    prompt: str,
    *,
    gemini_generate: Callable[[str, str], str],
    pollinations_generate: Callable[[str], Awaitable[str]],
    cooldown_until: float = 0.0,
    cooldown_seconds: int = 21600,
    models=DEFAULT_MODELS,
    monotonic=time.monotonic,
    sleeper=asyncio.sleep,
    logger=logging,
):
    """Return (text, updated_cooldown_until), retaining provider fallback order."""
    if monotonic() < cooldown_until:
        remaining = int(cooldown_until - monotonic())
        logger.info(
            "Gemini quota cooldown active; %s seconds remain; using Pollinations",
            remaining,
        )
        return await pollinations_generate(prompt), cooldown_until

    last_error = None
    quota_error = None
    quota_seen = False

    for model_index, model_name in enumerate(models):
        for attempt in range(2):
            try:
                response_text = await asyncio.to_thread(
                    gemini_generate, model_name, prompt
                )
                if response_text:
                    if model_index > 0:
                        logger.info("Gemini fallback model succeeded: %s", model_name)
                    logger.info("AI provider selected: Gemini model=%s", model_name)
                    return response_text, cooldown_until
                last_error = RuntimeError(f"{model_name} returned an empty response")
                logger.warning("Gemini model %s returned an empty response", model_name)
                break
            except Exception as exc:
                last_error = exc
                error_text = str(exc)
                upper_error = error_text.upper()
                status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
                quota_limited = (
                    status_code == 429
                    or "RESOURCE_EXHAUSTED" in upper_error
                    or "QUOTA_EXCEEDED" in upper_error
                    or "429" in upper_error
                )
                logger.warning(
                    "Gemini model %s attempt %s/2 failed (%s)",
                    model_name, attempt + 1, type(exc).__name__,
                )
                if quota_limited:
                    quota_seen = True
                    quota_error = error_text
                    logger.warning(
                        "Gemini quota exhausted for model %s; checking next Gemini model",
                        model_name,
                    )
                    break
                if attempt == 0:
                    await sleeper(1.5)

    if quota_seen:
        cooldown = retry_after_seconds(quota_error, cooldown_seconds)
        cooldown_until = monotonic() + cooldown
        logger.warning(
            "Gemini quota exhausted across configured fallbacks; Pollinations enabled for %s seconds",
            cooldown,
        )

    try:
        return await pollinations_generate(prompt), cooldown_until
    except Exception as fallback_error:
        logger.exception("Pollinations text fallback failed")
        if last_error:
            raise RuntimeError(
                "Gemini is unavailable and Pollinations fallback failed"
            ) from last_error
        raise fallback_error
