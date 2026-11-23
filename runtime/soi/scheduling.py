"""Safe scheduling defaults for the in-process SOI collector."""
import logging
import os


def get_soi_poll_interval() -> int:
    """Return an interval that prevents overlapping the measured collection cycle."""
    raw = os.getenv("SOI_POLL_SECONDS", "300")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logging.warning("Invalid SOI_POLL_SECONDS; using safe 300-second interval")
        return 300
    if not 300 <= value <= 3600:
        logging.warning("SOI_POLL_SECONDS must be 300..3600; using 300 seconds")
        return 300
    return value
