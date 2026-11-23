"""Explicit runtime registration for the two Telegram scheduled publishers.

The news worker remains a separate process with its own 600-second RSS loop.
Nothing is started merely by importing this module.
"""
from __future__ import annotations

from .bluesky_admin import register_bluesky_admin_command
from .caption_generator import ask_gemini
from .hourly_publisher import register_hourly_publisher
from .website_showcase import register_website_showcase


def register_publishing_schedules(
    application, gemini_client, is_admin_fn=None, *, register_admin_command=True
):
    """Register hourly editorial and 3-hour website spotlights exactly once per app.

    Set register_admin_command=False when the host bot already registers its own
    admin-only /bskycheck handler; this avoids duplicate command handlers.
    """
    if gemini_client is None:
        raise ValueError("gemini_client is required for publishing schedules")
    if register_admin_command and not callable(is_admin_fn):
        raise ValueError("is_admin_fn is required when registering /bskycheck")
    application.bot_data["gemini_client"] = gemini_client

    async def ask_gemini_for_scheduled_posts(task):
        return await ask_gemini(task, gemini_client=gemini_client)

    if register_admin_command:
        register_bluesky_admin_command(application, is_admin_fn)
    register_hourly_publisher(application)
    # Website showcase uses a different module; remove any stale copy before restoring it.
    for job in application.job_queue.get_jobs_by_name("zombcash_website_showcase"):
        job.schedule_removal()
    register_website_showcase(application, ask_gemini_for_scheduled_posts)
