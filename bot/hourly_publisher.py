"""Integrated hourly ZombieCash Telegram + site-queue + Bluesky publisher.

This is the staging replacement for the production bot's _auto_post_job_once /
auto_post_job flow. It is deliberately not started on import; the runtime must
explicitly register it after an approved cutover.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import time
from pathlib import Path

from .auto_post_content import (
    STYLE_NAMES,
    build_bluesky_post_text,
    build_caption_prompt,
    build_telegram_caption,
)
from .auto_post_schedule import AUTO_POST_CATEGORIES, AUTO_POST_INTERVAL_SECONDS, normalize_rotation_index
from .auto_post_state import AutoPostImageStore
from .caption_generator import ask_gemini
from .image_runtime import release_image_memory
from .image_service import generate_zombiecash_image
from .project_memory import load_project_memory
from .publish_target import get_publish_target
from .social_publishers import bluesky_text_fits, publish_bluesky_post, publish_line_meme


STATE_FILE = os.getenv("ZOMBCASH_AUTO_POST_STATE_FILE", "/data/auto_post_state.json")
LOCK_FILE = os.getenv("ZOMBCASH_AUTO_POST_LOCK_FILE", "/data/auto_post_rotation.lock")
PENDING_IMAGE_DIR = os.getenv("ZOMBCASH_AUTO_POST_PENDING_DIR", "/data/auto_post_pending_images")
JOB_NAME = "zombcash_auto_post_rotation"
RETRY_DELAYS = (900, 1800, 3600)


def load_last_auto_post_time(state_file=STATE_FILE):
    """Read the last successful timestamp for the single shared hourly rotation."""
    try:
        with open(state_file, "r", encoding="utf-8") as handle:
            state = json.load(handle)
        value = float(state.get("last_successful_rotation_post", 0) or 0)
        return value if value > 0 else None
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        logging.exception("Could not read automatic-post schedule state")
        return None


def load_next_rotation_index(state_file=STATE_FILE):
    try:
        with open(state_file, "r", encoding="utf-8") as handle:
            state = json.load(handle)
        return normalize_rotation_index(state.get("next_rotation_index", 0))
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def save_rotation_success(timestamp, next_index, image_style, *, state_file=STATE_FILE):
    """Persist rotation state atomically without deleting unrelated state keys."""
    state = {}
    try:
        with open(state_file, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            state.update(loaded)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        logging.exception("Could not read auto-post state before saving rotation")
    state["last_successful_rotation_post"] = timestamp
    state["next_rotation_index"] = normalize_rotation_index(next_index)
    state["last_rotation_image_style"] = image_style
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def schedule_next_auto_post(application, delay=AUTO_POST_INTERVAL_SECONDS):
    """Keep one queued hourly rotation job; do not alter other schedules."""
    for job in application.job_queue.get_jobs_by_name(JOB_NAME):
        job.schedule_removal()
    application.job_queue.run_once(
        auto_post_job,
        when=max(1, int(delay)),
        data={"series": "rotation"},
        name=JOB_NAME,
    )


def schedule_auto_post_retry(application, reason):
    attempt = int(application.bot_data.get("auto_post_retry_attempt", 0))
    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
    application.bot_data["auto_post_retry_attempt"] = attempt + 1
    logging.warning(
        "AUTO POST retry deferred: attempt=%s delay_seconds=%s reason=%s",
        attempt + 1, delay, str(reason)[:160],
    )
    schedule_next_auto_post(application, delay)


async def _generate_caption(context, prompt):
    client = context.application.bot_data.get("gemini_client")
    if client is None:
        raise RuntimeError("gemini_client is not configured in application.bot_data")
    return await ask_gemini(prompt, gemini_client=client)


async def _auto_post_job_once(
    context,
    *,
    image_store=None,
    image_generator=generate_zombiecash_image,
    caption_generator=None,
    line_publisher=publish_line_meme,
    bluesky_publisher=publish_bluesky_post,
    memory_loader=load_project_memory,
    state_file=STATE_FILE,
):
    categories = AUTO_POST_CATEGORIES
    rotation_index = load_next_rotation_index(state_file)
    series, image_style = categories[rotation_index]
    store = image_store or AutoPostImageStore(PENDING_IMAGE_DIR)
    image = None
    image_payload = None
    caption_generator = caption_generator or _generate_caption

    try:
        pending = store.load(image_style)
        if pending and pending.get("sent"):
            sent_id = pending["metadata"].get("telegram_message_id")
            logging.warning(
                "AUTO POST found an already-sent pending marker; finalizing rotation without reposting: message_id=%s",
                sent_id,
            )
            save_rotation_success(
                time.time(), (rotation_index + 1) % len(categories), image_style,
                state_file=state_file,
            )
            context.application.bot_data["auto_post_retry_attempt"] = 0
            store.clear(image_style)
            schedule_next_auto_post(context.application, AUTO_POST_INTERVAL_SECONDS)
            return

        if pending:
            image = pending["image"]
        else:
            image = await image_generator(image_style)
            scene_description = getattr(image, "zombcash_scene", "")
            store.save(image, image_style, scene_description)

        image_payload = image.getvalue()
        scene_description = getattr(image, "zombcash_scene", "")
        caption_prompt = build_caption_prompt(image_style, scene_description)
        answer = await caption_generator(context, caption_prompt)

        memory = memory_loader()
        contract_address = str(memory.get("contract_address", "")).strip()
        x_url = str(memory.get("x_url", "")).strip()
        telegram_url = str(memory.get("telegram_url", "")).strip()
        telegram_caption = build_telegram_caption(
            image_style, answer,
            contract_address=contract_address,
            x_url=x_url,
            telegram_url=telegram_url,
        )

        try:
            # Reserve delivery first. An ambiguous timeout must never resend this image.
            store.mark_sending(image_style)
            telegram_message = await context.bot.send_photo(
                chat_id=get_publish_target(),
                photo=image,
                caption=telegram_caption,
                parse_mode="HTML",
                connect_timeout=30,
                read_timeout=120,
                write_timeout=120,
                pool_timeout=30,
            )
            store.mark_sent(image_style, telegram_message.message_id)
        except Exception:
            logging.exception("AUTO POST Telegram image send failed: category=%s", series)
            raise

        try:
            await asyncio.to_thread(
                line_publisher,
                (answer or "").strip(),
                series,
                image_payload,
                telegram_message.message_id,
            )
        except Exception as line_error:
            logging.warning("LINE meme sync failed (%s)", type(line_error).__name__)

        try:
            bluesky_text = build_bluesky_post_text(
                image_style,
                answer,
                contract_address=contract_address,
                text_fits=bluesky_text_fits,
            )
            await asyncio.to_thread(
                bluesky_publisher,
                bluesky_text,
                image_payload,
                "ZombieCash " + image_style.lower() + " artwork: " + (answer or "")[:120],
            )
        except Exception as bluesky_error:
            logging.warning("Bluesky cross-post failed (%s)", type(bluesky_error).__name__)

        logging.info(
            "AUTO POST: rotation_index=%s series=%s style=%s published to @ZOMBCASH",
            rotation_index, series, image_style,
        )
        image.close()
        image = None
        image_payload = None
        release_image_memory("after_auto_post_" + image_style)

        try:
            save_rotation_success(
                time.time(), (rotation_index + 1) % len(categories), image_style,
                state_file=state_file,
            )
            store.clear(image_style)
        except OSError:
            logging.exception(
                "Could not persist successful automatic-post state; sent marker retained to prevent duplicate posting"
            )
        context.application.bot_data["auto_post_retry_attempt"] = 0
        schedule_next_auto_post(context.application, AUTO_POST_INTERVAL_SECONDS)

    except Exception:
        if image is not None:
            image.close()
        image_payload = None
        release_image_memory("after_auto_post_failure_" + image_style)
        logging.exception("AUTO POST ERROR: category=%s", series)
        schedule_auto_post_retry(context.application, "image_or_send_failure")


async def auto_post_job(context):
    """Run at most one visual publication across all bot processes."""
    lock_file = None
    try:
        os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
        lock_file = open(LOCK_FILE, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logging.warning("AUTO POST skipped: another bot process already owns the rotation lock")
            return
        await _auto_post_job_once(context)
    except Exception as exc:
        logging.exception("AUTO POST LOCK/EXECUTION ERROR")
        schedule_auto_post_retry(context.application, type(exc).__name__)
    finally:
        if lock_file is not None:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            lock_file.close()


def register_hourly_publisher(application, *, state_file=None):
    """Restore the existing hourly due time; explicit opt-in, never auto-started."""
    state_file = STATE_FILE if state_file is None else state_file
    last_post = load_last_auto_post_time(state_file)
    if last_post is None:
        last_post = time.time()
        try:
            save_rotation_success(
                last_post, load_next_rotation_index(state_file), "INITIALIZED",
                state_file=state_file,
            )
        except OSError:
            logging.exception("Could not initialize hourly rotation state")
    elapsed = max(0, time.time() - last_post)
    next_delay = max(1, AUTO_POST_INTERVAL_SECONDS - elapsed)
    logging.info(
        "Next ZOMBCASH hourly rotation post scheduled in %.0f seconds; next category index=%s",
        next_delay, load_next_rotation_index(state_file),
    )
    schedule_next_auto_post(application, next_delay)
