"""Complete image-generation adapter for the ZombieCash hourly publisher.

Keeps the production memory gate, original prompt builder, Pollinations transport,
and BytesIO metadata contract together without importing the monolithic bot.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os

from .image_prompt import SUPPORTED_STYLES, build_image_prompt
from .image_runtime import ensure_image_generation_memory, log_image_memory, release_image_memory
from .pollinations_image_client import generate_image


IMAGE_GENERATION_LOCK = asyncio.Lock()


async def generate_zombiecash_image(
    style_mode: str = "HORROR",
    *,
    api_key: str | None = None,
    memory_guard=ensure_image_generation_memory,
    image_client=generate_image,
    logger=logging,
):
    """Generate a normalized JPEG BytesIO carrying the original scene metadata."""
    if style_mode not in SUPPORTED_STYLES:
        raise ValueError(f"Unsupported ZombieCash image style: {style_mode!r}")
    key = api_key if api_key is not None else os.getenv("POLLINATIONS_API_KEY", "")
    if not key:
        raise RuntimeError("POLLINATIONS_API_KEY is not configured")

    if IMAGE_GENERATION_LOCK.locked():
        logger.info("IMAGE_GENERATION waiting: another image generation is active")
    async with IMAGE_GENERATION_LOCK:
        memory_guard()
        log_image_memory("before_generate_" + style_mode, logger)
        logger.info("IMAGE_GENERATION started: style=%s", style_mode)
        try:
            prompt, chosen_scene = build_image_prompt(style_mode)
            normalized, attempt_seed = await image_client(prompt, key)
            image = io.BytesIO(normalized)
            image.zombcash_scene = chosen_scene
            image.name = "zombcash_meme.jpg"
            image.seek(0)
            log_image_memory("after_generate_" + style_mode, logger)
            logger.info(
                "AUTO POST IMAGE GENERATED: style=%s seed=%s bytes=%s",
                style_mode, attempt_seed, len(normalized),
            )
            return image
        finally:
            release_image_memory("after_generation_attempt_" + style_mode, logger)
            logger.info("IMAGE_GENERATION slot released: style=%s", style_mode)
