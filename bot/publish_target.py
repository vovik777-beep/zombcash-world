"""Centralized Telegram destination selection for all publishing workers."""
import os


PRODUCTION_CHAT_ID = "@ZOMBCASH"


def get_publish_target():
    """Return the configured channel; preserve production behavior as the default."""
    return os.getenv("ZOMBCASH_TELEGRAM_CHAT_ID", PRODUCTION_CHAT_ID).strip() or PRODUCTION_CHAT_ID
