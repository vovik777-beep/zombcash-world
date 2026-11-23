"""Explicit process entry point for the hourly + website Telegram publishers.

Run only in an isolated staging service until a production cutover is separately approved.
The RSS news publisher remains a distinct process (python -m bot.news_worker).
"""
from __future__ import annotations

import logging
import os

from google import genai
from telegram.ext import Application

from bot.publish_target import get_publish_target
from bot.publisher_runtime import register_publishing_schedules


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required to start the ZombieCash publisher")
    return value


def _load_admin_ids() -> set[int]:
    raw = _required_env("ZOMBCASH_ADMIN_IDS")
    try:
        values = {int(item.strip()) for item in raw.split(",") if item.strip()}
    except ValueError as exc:
        raise RuntimeError("ZOMBCASH_ADMIN_IDS must be comma-separated numeric Telegram user IDs") from exc
    if not values:
        raise RuntimeError("ZOMBCASH_ADMIN_IDS must contain at least one Telegram user ID")
    return values


def build_application():
    """Build and register schedules without starting polling or publishing."""
    token = _required_env("TELEGRAM_BOT_TOKEN")
    gemini_key = _required_env("GEMINI_API_KEY")
    _required_env("POLLINATIONS_API_KEY")
    # Resolve and validate the destination before constructing a runnable app.
    get_publish_target()
    admin_ids = _load_admin_ids()

    application = Application.builder().token(token).build()
    gemini_client = genai.Client(api_key=gemini_key)

    def is_admin(update) -> bool:
        user = getattr(update, "effective_user", None)
        user_id = getattr(user, "id", None)
        return user_id in admin_ids

    register_publishing_schedules(application, gemini_client, is_admin)
    return application


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    application = build_application()
    logging.info(
        "ZombieCash publisher runtime starting; environment=%s target=%s",
        os.getenv("ZOMBCASH_ENV", "production").strip().lower() or "production",
        get_publish_target(),
    )
    application.run_polling()


if __name__ == "__main__":
    main()
