"""Centralized Telegram destination selection for all publishing workers."""
import os


PRODUCTION_CHAT_ID = "@ZOMBCASH"


def get_publish_target():
    """Resolve the Telegram destination and fail closed for an unsafe staging config."""
    environment = os.getenv("ZOMBCASH_ENV", "production").strip().lower()
    configured_target = os.getenv("ZOMBCASH_TELEGRAM_CHAT_ID", "").strip()

    if environment in {"staging", "test"}:
        if not configured_target:
            raise RuntimeError(
                "ZOMBCASH_TELEGRAM_CHAT_ID must point to a private test channel in staging"
            )
        if configured_target == PRODUCTION_CHAT_ID:
            raise RuntimeError("Staging publisher is forbidden from targeting @ZOMBCASH")
        return configured_target

    return configured_target or PRODUCTION_CHAT_ID
