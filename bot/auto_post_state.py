"""Durable pending-image state for the hourly editorial publisher.

This module is intentionally independent of Telegram, Gemini, image providers and
the monolithic bot entrypoint. It preserves the production retry invariant:
a delivery marked "sending" is ambiguous and must never be automatically resent.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import BinaryIO


SUPPORTED_STYLES = frozenset({"CARTOON", "PHOTO_PINUP", "HORROR"})


class AutoPostImageStore:
    def __init__(self, directory: str | os.PathLike[str] = "/data/auto_post_pending_images"):
        self.directory = Path(directory)

    def _paths(self, style: str) -> tuple[Path, Path]:
        if style not in SUPPORTED_STYLES:
            raise ValueError("Unsupported automatic-post image style")
        self.directory.mkdir(parents=True, exist_ok=True)
        stem = self.directory / style.lower()
        return stem.with_suffix(".json"), stem.with_suffix(".bin")

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def save(self, image: BinaryIO, style: str, scene: str = "") -> None:
        metadata_path, image_path = self._paths(style)
        image_bytes = image.getvalue()
        if not image_bytes:
            raise ValueError("Cannot persist an empty automatic-post image")

        self._atomic_write(image_path, image_bytes)
        metadata = {
            "style": style,
            "scene": str(scene or "")[:2000],
            "created_at": time.time(),
            "status": "pending",
            "filename": os.path.basename(getattr(image, "name", "zombcash_image.bin")),
        }
        self._atomic_write(
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
        )

    def load(self, style: str):
        metadata_path, image_path = self._paths(style)
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("style") != style:
                return None
            if metadata.get("status") in {"sending", "sent"}:
                return {"sent": True, "metadata": metadata}

            image_bytes = image_path.read_bytes()
            if not image_bytes:
                raise ValueError("Persisted pending image is empty")
            import io
            image = io.BytesIO(image_bytes)
            image.zombcash_scene = str(metadata.get("scene") or "")
            image.name = str(metadata.get("filename") or "zombcash_reused_image.bin")
            image.seek(0)
            age = int(max(0, time.time() - float(metadata.get("created_at", time.time()))))
            return {"sent": False, "image": image, "metadata": metadata, "age_seconds": age}
        except FileNotFoundError:
            return None

    def _update_status(self, style: str, status: str, **fields) -> None:
        metadata_path, _ = self._paths(style)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["status"] = status
        metadata.update(fields)
        metadata_tmp = metadata_path.with_name(metadata_path.name + ".tmp")
        self._atomic_write(
            metadata_tmp,
            json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        os.replace(metadata_tmp, metadata_path)

    def mark_sending(self, style: str) -> None:
        self._update_status(style, "sending", send_started_at=time.time())

    def mark_sent(self, style: str, telegram_message_id: int) -> None:
        self._update_status(
            style, "sent",
            telegram_message_id=int(telegram_message_id),
            sent_at=time.time(),
        )

    def clear(self, style: str) -> None:
        metadata_path, image_path = self._paths(style)
        for path in (metadata_path, image_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
