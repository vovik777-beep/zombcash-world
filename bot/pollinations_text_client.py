"""Pollinations OpenAI-compatible text fallback used by ZombieCash."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request


DEFAULT_MODEL = "openai/gpt-5.4-nano"
API_URL = "https://gen.pollinations.ai/v1/chat/completions"
USER_AGENT = "ZombieCashMemeBot/1.0"


async def generate_text(prompt: str, *, api_key=None, model=None, opener=urllib.request.urlopen, logger=logging):
    key = api_key if api_key is not None else os.getenv("POLLINATIONS_API_KEY", "")
    if not key:
        raise RuntimeError("POLLINATIONS_API_KEY is not configured for text fallback")
    selected_model = (model or os.getenv("POLLINATIONS_TEXT_MODEL", DEFAULT_MODEL)).strip()
    payload = {
        "model": selected_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.8,
        "max_tokens": 900,
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    def send_request():
        with opener(request, timeout=45) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Pollinations text response exceeds the 2 MB limit")
        return json.loads(raw.decode("utf-8", errors="replace"))

    result = await asyncio.to_thread(send_request)
    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError("Pollinations returned no text choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Pollinations returned an empty text response")
    logger.info("AI provider selected: Pollinations model=%s", selected_model)
    return content.strip()
