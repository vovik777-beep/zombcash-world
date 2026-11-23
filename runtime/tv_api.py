"""ZombieCash TV API routes.

This module is deliberately storage-only: it persists admin requests and serves
published episodes. It does not pretend that a video-generation worker exists.
Authentication is delegated to api_server._read_survivor_session and an explicit
Telegram administrator allow-list from ZOMBCASH_ADMIN_IDS.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import web

MAX_BODY = 12 * 1024
MAX_TITLE = 160
MAX_DESCRIPTION = 1000
MAX_VIDEO_BYTES = 250 * 1024 * 1024
PAGE_SIZE_MAX = 24
RATE_LIMIT = 30
RATE_WINDOW = 60
_rate_events: dict[str, list[float]] = {}


def _db_path(data_dir: str | Path) -> Path:
    root = Path(data_dir).resolve() / "tv"
    root.mkdir(parents=True, exist_ok=True)
    return root / "tv.sqlite3"


def _connect(data_dir: str | Path):
    conn = sqlite3.connect(_db_path(data_dir), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tv_tasks (
            task_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            source_url TEXT,
            status TEXT NOT NULL,
            status_note TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tv_episodes (
            episode_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            duration_seconds INTEGER,
            video_relpath TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ready',
            channel TEXT NOT NULL DEFAULT 'news'
        )
    """)
    # Upgrade existing FadeHost databases without deleting published episodes.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tv_episodes)")}
    if "channel" not in columns:
        conn.execute("ALTER TABLE tv_episodes ADD COLUMN channel TEXT NOT NULL DEFAULT 'news'")
    conn.commit()
    return conn


def _valid_id(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (ValueError, TypeError, AttributeError):
        return False


def _safe_video_path(data_dir: str | Path, episode_id: str, relpath: str) -> Path | None:
    if not _valid_id(episode_id) or not isinstance(relpath, str):
        return None
    root = (Path(data_dir).resolve() / "tv" / "episodes").resolve()
    candidate = (root / episode_id / relpath).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.name != "video.mp4" or candidate.parent.name != episode_id:
        return None
    return candidate


def parse_range_header(header: str, size: int):
    """Return an inclusive byte range; clamp valid end offsets to file size."""
    if size <= 0 or not isinstance(header, str) or not header.startswith("bytes="):
        return None
    spec = header[6:].strip()
    if not spec or "," in spec or spec.count("-") != 1:
        return None
    first, last = spec.split("-", 1)
    try:
        if first == "":
            suffix = int(last)
            if suffix <= 0:
                return None
            return max(0, size - suffix), size - 1
        start = int(first)
        if start < 0 or start >= size:
            return None
        end = size - 1 if last == "" else int(last)
        if end < start:
            return None
        return start, min(end, size - 1)
    except (TypeError, ValueError):
        return None


def _rate_limited(request) -> bool:
    now = time.time()
    ip = request.remote or "unknown"
    events = [stamp for stamp in _rate_events.get(ip, []) if now - stamp < RATE_WINDOW]
    if len(events) >= RATE_LIMIT:
        _rate_events[ip] = events
        return True
    events.append(now)
    if len(_rate_events) > 2000:
        oldest = min(_rate_events, key=lambda key: _rate_events[key][-1] if _rate_events[key] else 0)
        _rate_events.pop(oldest, None)
    _rate_events[ip] = events
    return False


def _is_admin(request, read_session) -> bool:
    telegram_id = read_session(request)
    if not telegram_id or not str(telegram_id).isdigit():
        return False
    # Keep the bot owner's existing administrator identity and honor the bot's
    # established additional-admin environment variable.
    allowed = {"8461681081"}
    allowed.update(
        value.strip()
        for value in os.getenv("ZOMBCASH_ADMIN_IDS", "").split(",")
        if value.strip().isdigit()
    )
    return str(telegram_id) in allowed


def _json_error(message: str, status: int):
    return web.json_response({"error": message}, status=status)


def _is_mp4_header(header: bytes) -> bool:
    """Recognize an ISO base media file (MP4) without trusting its filename."""
    return len(header) >= 12 and header[4:8] == b"ftyp"


def register_tv_routes(app: web.Application, data_dir: str | Path, read_session):
    """Register TV endpoints using the API server's real Telegram HMAC session reader."""
    data_dir = Path(data_dir).resolve()

    async def list_episodes(request):
        if _rate_limited(request):
            return _json_error("Rate limit exceeded", 429)
        try:
            page = int(request.query.get("page", "1"))
            per_page = int(request.query.get("per_page", "8"))
        except (TypeError, ValueError):
            return _json_error("Invalid pagination", 400)
        if page < 1 or per_page < 1:
            return _json_error("Invalid pagination", 400)
        per_page = min(per_page, PAGE_SIZE_MAX)
        with closing(_connect(data_dir)) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM tv_episodes WHERE status='ready'"
            ).fetchone()[0]
            rows = conn.execute(
                """SELECT episode_id,title,description,created_at,duration_seconds,channel
                   FROM tv_episodes WHERE status='ready'
                   ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (per_page, (page - 1) * per_page),
            ).fetchall()
        return web.json_response({
            "episodes": [dict(row) for row in rows],
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": (total + per_page - 1) // per_page,
        })

    async def get_episode(request):
        if _rate_limited(request):
            return _json_error("Rate limit exceeded", 429)
        episode_id = request.match_info.get("episode_id", "")
        if not _valid_id(episode_id):
            return _json_error("Invalid episode ID", 400)
        with closing(_connect(data_dir)) as conn:
            row = conn.execute(
                """SELECT episode_id,title,description,created_at,duration_seconds,channel
                   FROM tv_episodes WHERE episode_id=? AND status='ready'""",
                (episode_id,),
            ).fetchone()
        if row is None:
            return _json_error("Episode not found", 404)
        return web.json_response(dict(row))

    async def get_video(request):
        if _rate_limited(request):
            return _json_error("Rate limit exceeded", 429)
        episode_id = request.match_info.get("episode_id", "")
        if not _valid_id(episode_id):
            return _json_error("Invalid episode ID", 400)
        with closing(_connect(data_dir)) as conn:
            row = conn.execute(
                "SELECT video_relpath FROM tv_episodes WHERE episode_id=? AND status='ready'",
                (episode_id,),
            ).fetchone()
        if row is None:
            return _json_error("Video not found", 404)
        path = _safe_video_path(data_dir, episode_id, row["video_relpath"])
        if path is None or not path.is_file():
            return _json_error("Video not found", 404)
        size = path.stat().st_size
        if size <= 0 or size > MAX_VIDEO_BYTES:
            return _json_error("Video unavailable", 404)
        start, end, status = 0, size - 1, 200
        range_header = request.headers.get("Range")
        if range_header:
            parsed = parse_range_header(range_header, size)
            if parsed is None:
                return web.Response(
                    status=416,
                    headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
                )
            start, end = parsed
            status = 206
        response = web.StreamResponse(status=status, headers={
            "Content-Type": "video/mp4",
            "Content-Length": str(end - start + 1),
            "Accept-Ranges": "bytes",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=60",
        })
        if status == 206:
            response.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        await response.prepare(request)
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = await asyncio.to_thread(handle.read, min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                await response.write(chunk)
        await response.write_eof()
        return response

    async def upload_episode(request):
        """Publish an already-rendered MP4 as an immutable archive episode."""
        if _rate_limited(request):
            return _json_error("Rate limit exceeded", 429)
        if request.headers.get("Origin") != "https://zombcash.github.io":
            return _json_error("Invalid website origin", 403)
        if not _is_admin(request, read_session):
            return _json_error("Administrator Telegram session required", 403)
        if request.content_type != "multipart/form-data":
            return _json_error("Content-Type must be multipart/form-data", 415)
        if request.content_length is not None and request.content_length > MAX_VIDEO_BYTES + 64 * 1024:
            return _json_error("Upload exceeds the 250 MB limit", 413)

        episode_id = str(uuid.uuid4())
        episodes_root = data_dir / "tv" / "episodes"
        episode_dir = episodes_root / episode_id
        episodes_root.mkdir(parents=True, exist_ok=True)
        episode_dir.mkdir(mode=0o700)
        temporary_path = episode_dir / ".video.uploading"
        final_path = episode_dir / "video.mp4"
        title = ""
        description = ""
        duration_seconds = None
        channel = "news"
        total = 0
        header = b""
        file_seen = False
        try:
            reader = await request.multipart()
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name == "video":
                    if file_seen:
                        return _json_error("Only one video file is allowed", 400)
                    file_seen = True
                    if part.filename is None:
                        return _json_error("Video file is required", 400)
                    with temporary_path.open("xb") as output:
                        while True:
                            chunk = await part.read_chunk(size=64 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > MAX_VIDEO_BYTES:
                                return _json_error("Upload exceeds the 250 MB limit", 413)
                            if len(header) < 32:
                                header += chunk[:32 - len(header)]
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                else:
                    value = await part.text()
                    if part.name == "title":
                        title = value.strip()
                    elif part.name == "description":
                        description = value.strip()
                    elif part.name == "duration_seconds":
                        if value.strip():
                            try:
                                duration_seconds = int(value)
                            except ValueError:
                                return _json_error("Invalid duration_seconds", 400)
                    elif part.name == "channel":
                        channel = value.strip().lower()
                    else:
                        return _json_error("Unexpected multipart field", 400)

            if not file_seen or total <= 0 or not temporary_path.is_file():
                return _json_error("A non-empty video file is required", 400)
            if not _is_mp4_header(header):
                return _json_error("Uploaded file is not a valid MP4 container", 415)
            if not title or len(title) > MAX_TITLE:
                return _json_error("Title must be 1–160 characters", 400)
            if len(description) > MAX_DESCRIPTION:
                return _json_error("Description must be at most 1000 characters", 400)
            if duration_seconds is not None and not 1 <= duration_seconds <= 86400:
                return _json_error("duration_seconds must be between 1 and 86400", 400)
            if channel not in {"news", "meme", "lab"}:
                return _json_error("channel must be news, meme, or lab", 400)

            os.replace(temporary_path, final_path)
            now = int(time.time())
            with closing(_connect(data_dir)) as conn:
                conn.execute(
                    """INSERT INTO tv_episodes
                       (episode_id,title,description,created_at,duration_seconds,video_relpath,status,channel)
                       VALUES(?,?,?,?,?,?, 'ready', ?)""",
                    (episode_id, title, description, now, duration_seconds, "video.mp4", channel),
                )
                conn.commit()
            return web.json_response({
                "episode_id": episode_id,
                "title": title,
                "status": "ready",
                "size_bytes": total,
                "duration_seconds": duration_seconds,
                "channel": channel,
                "video_url": f"/api/tv/episodes/{episode_id}/video",
            }, status=201)
        except (ValueError, UnicodeDecodeError):
            return _json_error("Invalid multipart upload", 400)
        except Exception:
            # Never leave a half-published episode or an unindexed video behind.
            raise
        finally:
            if temporary_path.exists():
                temporary_path.unlink(missing_ok=True)
            # Remove the new directory if publication did not commit.
            with closing(_connect(data_dir)) as conn:
                exists = conn.execute(
                    "SELECT 1 FROM tv_episodes WHERE episode_id=?", (episode_id,)
                ).fetchone()
            if not exists:
                if final_path.exists():
                    final_path.unlink(missing_ok=True)
                try:
                    episode_dir.rmdir()
                except OSError:
                    pass

    async def create_task(request):
        if _rate_limited(request):
            return _json_error("Rate limit exceeded", 429)
        if request.headers.get("Origin") != "https://zombcash.github.io":
            return _json_error("Invalid website origin", 403)
        if not _is_admin(request, read_session):
            return _json_error("Administrator Telegram session required", 403)
        if request.content_type != "application/json":
            return _json_error("Content-Type must be application/json", 415)
        if request.content_length is not None and request.content_length > MAX_BODY:
            return _json_error("Request body too large", 413)
        try:
            raw_body = await request.read()
            if len(raw_body) > MAX_BODY:
                return _json_error("Request body too large", 413)
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _json_error("Invalid JSON", 400)
        if not isinstance(payload, dict) or "admin_token" in payload:
            return _json_error("Invalid task payload", 400)
        title = payload.get("title")
        description = payload.get("description", "")
        source_url = payload.get("source_url")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > MAX_TITLE:
            return _json_error("Title must be 1–160 characters", 400)
        if not isinstance(description, str) or len(description) > MAX_DESCRIPTION:
            return _json_error("Description must be at most 1000 characters", 400)
        if source_url is not None:
            if not isinstance(source_url, str) or len(source_url) > 500:
                return _json_error("Invalid source URL", 400)
            parsed = urlparse(source_url)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                return _json_error("Source URL must be a valid HTTPS URL", 400)
        task_id = str(uuid.uuid4())
        now = int(time.time())
        note = "Stored; generation worker is not configured on this host."
        with closing(_connect(data_dir)) as conn:
            conn.execute(
                """INSERT INTO tv_tasks
                   (task_id,title,description,source_url,status,status_note,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (task_id, title.strip(), description.strip(), source_url, "queued", note, now, now),
            )
            conn.commit()
        return web.json_response({
            "task_id": task_id,
            "title": title.strip(),
            "status": "queued",
            "status_note": note,
            "generation_available": False,
            "created_at": now,
        }, status=202)

    async def get_task(request):
        if _rate_limited(request):
            return _json_error("Rate limit exceeded", 429)
        if not _is_admin(request, read_session):
            return _json_error("Administrator Telegram session required", 403)
        task_id = request.match_info.get("task_id", "")
        if not _valid_id(task_id):
            return _json_error("Invalid task ID", 400)
        with closing(_connect(data_dir)) as conn:
            row = conn.execute("SELECT * FROM tv_tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return _json_error("Task not found", 404)
        return web.json_response(dict(row))

    app.router.add_get("/api/tv/episodes", list_episodes)
    app.router.add_post("/api/tv/episodes/upload", upload_episode)
    app.router.add_get("/api/tv/episodes/{episode_id}", get_episode)
    app.router.add_get("/api/tv/episodes/{episode_id}/video", get_video)
    app.router.add_post("/api/tv/tasks", create_task)
    app.router.add_get("/api/tv/tasks/{task_id}", get_task)
