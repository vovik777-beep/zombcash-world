"""Explicit runtime registration for the two Telegram scheduled publishers.

The news worker remains a separate process with its own 600-second RSS loop.
Nothing is started merely by importing this module.
"""
from __future__ import annotations

from bot.caption_generator import ask_gemini
from bot.hourly_publisher import register_hourly_publisher
from bot.website_showcase import register_website_showcase


def register_publishing_schedules(application, gemini_client):
    """Register hourly editorial and 3-hour website spotlights exactly once per app."""
    if gemini_client is None:
        raise ValueError("gemini_client is required for publishing schedules")
    application.bot_data["gemini_client"] = gemini_client

    async def ask_gemini_for_scheduled_posts(task):
        return await ask_gemini(task, gemini_client=gemini_client)

    register_hourly_publisher(application)
    register_website_showcase(application, ask_gemini_for_scheduled_posts)
