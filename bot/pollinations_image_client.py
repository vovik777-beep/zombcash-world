"""Pollinations image-generation transport for the ZombieCash publisher.

Prompt composition stays with the editorial layer. This module handles only
authenticated API transport, bounded response parsing, retry and normalization.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import urllib.request

from .image_runtime import normalize_generated_image


API_URL = "https://gen.pollinations.ai/v1/images/generations"
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_ENCODED_BYTES = 8 * 1024 * 1024
MAX_SOURCE_BYTES = 6 * 1024 * 1024
USER_AGENT = "ZombieCashMemeBot/1.0"


def _request_payload(prompt: str, seed: int):
    return {
        "model": "flux",
        "prompt": prompt,
        "size": "1024x1024",
        "seed": seed,
        "response_format": "b64_json",
    }


def _download_json(request, opener):
    with opener(request, timeout=90) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Image API response exceeds the 10 MB limit")
    return json.loads(raw.decode("utf-8", errors="replace"))


async def generate_image(
    prompt: str,
    api_key: str,
    *,
    normalizer=normalize_generated_image,
    opener=urllib.request.urlopen,
    sleeper=asyncio.sleep,
    seed: int | None = None,
    logger=logging,
):
    """Generate one normalized JPEG image, retrying once with a varied seed."""
    if not api_key:
        raise RuntimeError("POLLINATIONS_API_KEY is not configured")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Image prompt must not be empty")

    initial_seed = seed if seed is not None else random.randint(1, 2_000_000_000)
    last_error = None
    for attempt in range(1, 3):
        attempt_seed = initial_seed if attempt == 1 else (initial_seed + 104729) % (2**31 - 1)
        attempt_prompt = prompt
        if attempt > 1:
            attempt_prompt += (
                " Retry with a clearly different camera angle and simpler, more readable composition."
            )
        payload = _request_payload(attempt_prompt, attempt_seed)
        request = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            result = await asyncio.to_thread(_download_json, request, opener)
            image_data = result.get("data")
            if not isinstance(image_data, list) or not image_data or not isinstance(image_data[0], dict):
                raise ValueError("Image API returned an unexpected response structure")
            encoded = image_data[0].get("b64_json")
            if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_ENCODED_BYTES:
                raise ValueError("Image API response is missing b64_json or exceeds the 8 MB encoded limit")
            image_bytes = base64.b64decode(encoded, validate=True)
            if not image_bytes or len(image_bytes) > MAX_SOURCE_BYTES:
                raise ValueError("Generated image is empty or exceeds the 6 MB source limit")
            del result, image_data, encoded
            normalized = normalizer(image_bytes)
            del image_bytes
            if not normalized or len(normalized) > 2_000_000:
                raise ValueError("Normalized image is empty or exceeds the 2 MB limit")
            logger.info(
                "AUTO POST IMAGE GENERATED: attempt=%s bytes=%s",
                attempt, len(normalized),
            )
            return normalized, attempt_seed
        except Exception as exc:
            last_error = exc
            logger.warning(
                "AUTO POST IMAGE GENERATION FAILED: attempt=%s/2 error=%s detail=%s",
                attempt, type(exc).__name__, str(exc)[:180],
            )
            if attempt == 1:
                await sleeper(2)

    raise RuntimeError(
        "Pollinations image generation failed after 2 attempts: "
        + type(last_error).__name__
    ) from last_error
