"""Safe runtime loader for the private ZombieCash project memory.

The actual project_memory.json is runtime data and is intentionally not copied
into this public staging repository. Configure ZOMBCASH_PROJECT_MEMORY_FILE or
place the file beside this module in the private runtime.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path


DEFAULT_MEMORY = {
    "project_name": "ZOMBCASH",
    "ticker": "$ZOMBCASH",
    "brand_message": "MARKET DIED. ZOMBIECASH DIDN'T.",
}


def get_memory_file() -> Path:
    configured = os.getenv("ZOMBCASH_PROJECT_MEMORY_FILE", "").strip()
    return Path(configured) if configured else Path(__file__).with_name("project_memory.json")


def load_project_memory(*, path=None, logger=logging):
    memory_file = Path(path) if path is not None else get_memory_file()
    try:
        with memory_file.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
        logger.error("project_memory.json must contain a JSON object")
    except FileNotFoundError:
        logger.error("project_memory.json was not found: %s", memory_file)
    except (json.JSONDecodeError, OSError):
        logger.exception("Could not load project memory")
    return dict(DEFAULT_MEMORY)


def get_memory_text(*, path=None, logger=logging):
    return json.dumps(load_project_memory(path=path, logger=logger), ensure_ascii=False, indent=2)
