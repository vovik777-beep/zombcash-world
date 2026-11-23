"""Persistent scene rotation for ZombieCash hourly editorial images."""
from __future__ import annotations

import json
import logging
import os
import random
from typing import Callable, Sequence


DEFAULT_HISTORY_FILE = "/data/auto_post_history.json"


def choose_fresh_scene(
    style_mode: str,
    scenes: Sequence[str],
    *,
    history_file: str = DEFAULT_HISTORY_FILE,
    recent_limit: int = 8,
    chooser: Callable[[Sequence[str]], str] = random.choice,
    logger=logging,
) -> str:
    """Choose a scene not used recently for this style and persist the choice.

    History is style-specific. Writes use fsync + atomic replace so an
    interrupted process cannot leave a partially written JSON document.
    If history cannot be read or written, selection still proceeds.
    """
    if not isinstance(style_mode, str) or not style_mode:
        raise ValueError("style_mode must be a non-empty string")
    if not scenes:
        raise ValueError("scenes must contain at least one scene")
    if recent_limit < 1:
        raise ValueError("recent_limit must be positive")

    state = {}
    try:
        with open(history_file, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            state = loaded
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        logger.warning("Could not read auto-post scene history; starting a fresh selection")

    recent = state.get(style_mode, [])
    if not isinstance(recent, list):
        recent = []
    recent = [item for item in recent if isinstance(item, str)]
    candidates = [scene for scene in scenes if scene not in recent]
    chosen = chooser(candidates or scenes)

    state[style_mode] = (recent + [chosen])[-min(recent_limit, len(scenes)):]
    try:
        directory = os.path.dirname(history_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temp_file = history_file + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_file, history_file)
    except OSError:
        logger.warning("Could not persist auto-post scene history; continuing without history")

    return chosen
