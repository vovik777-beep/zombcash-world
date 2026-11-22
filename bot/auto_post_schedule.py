"""Single-source schedule configuration for ZombieCash's editorial channel posts."""

AUTO_POST_INTERVAL_SECONDS = 60 * 60

# Weighted four-slot cycle: After Dark receives half of all posts.
# Memes and Horror each receive one quarter.
AUTO_POST_CATEGORIES = (
    ("ZombieCash Memes", "CARTOON"),
    ("Undead After Dark", "PHOTO_PINUP"),
    ("Nightmare & Horror", "HORROR"),
    ("Undead After Dark", "PHOTO_PINUP"),
)


def normalize_rotation_index(value):
    """Normalize persisted rotation state for the current weighted cycle."""
    try:
        index = int(value)
    except (TypeError, ValueError, OverflowError):
        index = 0
    return index % len(AUTO_POST_CATEGORIES)
