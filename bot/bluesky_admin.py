"""Admin-only Bluesky authentication check; it never publishes a post."""
from __future__ import annotations

import asyncio
import logging
import os
import urllib.error

from telegram.ext import CommandHandler

from bot.social_publishers import check_bluesky_connection


async def bskycheck_command(update, context):
    message = update.effective_message
    is_admin = context.application.bot_data.get("is_admin")
    if not callable(is_admin) or not is_admin(update):
        await message.reply_text("⛔ Admin only.")
        return
    if not os.getenv("BLUESKY_APP_PASSWORD", "").strip():
        await message.reply_text(
            "⚠️ BLUESKY_APP_PASSWORD is not configured in FadeHost. No post was sent."
        )
        return
    try:
        result = await asyncio.to_thread(check_bluesky_connection)
        if result.get("ok"):
            await message.reply_text(
                "✅ Bluesky authentication works for @" + result["handle"] + ". No post was published."
            )
        else:
            await message.reply_text(
                "⚠️ Bluesky check failed: " + str(result.get("reason", "unknown error"))
            )
    except urllib.error.HTTPError as exc:
        logging.warning("Bluesky authentication check failed with HTTP %s", exc.code)
        await message.reply_text(
            "❌ Bluesky rejected the app password (HTTP " + str(exc.code) + "). Check the FadeHost secret."
        )
    except Exception as exc:
        logging.warning("Bluesky authentication check failed (%s)", type(exc).__name__)
        await message.reply_text(
            "❌ Bluesky connection check failed (" + type(exc).__name__ + ")."
        )


def register_bluesky_admin_command(application, is_admin_fn):
    if not callable(is_admin_fn):
        raise ValueError("is_admin_fn is required to preserve admin-only command access")
    application.bot_data["is_admin"] = is_admin_fn
    application.add_handler(CommandHandler("bskycheck", bskycheck_command))
