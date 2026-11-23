"""Project-memory-aware ZombieCash caption generation with provider fallbacks."""
from __future__ import annotations

import logging
import os
import time

from .ai_text_client import generate_with_fallback
from .pollinations_text_client import generate_text as pollinations_generate
from .project_memory import get_memory_text
from .system_prompt import SYSTEM_PROMPT


GEMINI_QUOTA_COOLDOWN_UNTIL = 0.0


async def ask_gemini(task, *, gemini_client, pollinations_client=pollinations_generate,
                     memory_path=None, logger=logging, monotonic=time.monotonic):
    """Compatibility name retained from .py; uses the original system prompt."""
    global GEMINI_QUOTA_COOLDOWN_UNTIL
    prompt = (
        SYSTEM_PROMPT
        + "\n\nPROJECT MEMORY:\n"
        + get_memory_text(path=memory_path, logger=logger)
        + "\n\nTASK:\n"
        + str(task)
    )

    def gemini_generate(model_name, contents):
        response = gemini_client.models.generate_content(model=model_name, contents=contents)
        return response.text if response is not None else ""

    text, GEMINI_QUOTA_COOLDOWN_UNTIL = await generate_with_fallback(
        prompt,
        gemini_generate=gemini_generate,
        pollinations_generate=pollinations_client,
        cooldown_until=GEMINI_QUOTA_COOLDOWN_UNTIL,
        cooldown_seconds=max(300, int(os.getenv("GEMINI_QUOTA_COOLDOWN_SECONDS", "21600"))),
        logger=logger,
        monotonic=monotonic,
    )
    return text
