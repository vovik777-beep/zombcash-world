"""Single-source schedule configuration for ZombieCash's editorial channel posts."""

AUTO_POST_INTERVAL_SECONDS = 60 * 60

# Balanced four-category cycle: each series receives one post per four slots.
AUTO_POST_CATEGORIES = (
    ("ZombieCash Memes", "CARTOON"),
    ("Undead After Dark", "PHOTO_PINUP"),
    ("Nightmare & Horror", "HORROR"),
    ("HEROINE'S SURVIVAL DIARY", "HEROINE"),
)


def normalize_rotation_index(value):
    """Normalize persisted rotation state for the current weighted cycle."""
    try:
        index = int(value)
    except (TypeError, ValueError, OverflowError):
        index = 0
    return index % len(AUTO_POST_CATEGORIES)
