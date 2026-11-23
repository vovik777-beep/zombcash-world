"""Resource guards and image normalization for the hourly publisher.

No Telegram, AI-provider, or game imports are required here.
"""
from __future__ import annotations

import io
import logging
import os


IMAGE_GENERATION_MAX_USAGE = 0.65
IMAGE_GENERATION_MIN_HEADROOM_BYTES = 600 * 1024 * 1024

_CGROUP_MEMORY_FILES = (
    ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
    ("/sys/fs/cgroup/memory/memory.usage_in_bytes", "/sys/fs/cgroup/memory/memory.limit_in_bytes"),
)


def container_memory(candidates=_CGROUP_MEMORY_FILES):
    """Return (current_bytes, limit_bytes) from cgroup v2/v1 when available."""
    for current_path, limit_path in candidates:
        try:
            with open(current_path, "r", encoding="ascii") as current_file:
                current_raw = current_file.read().strip()
            with open(limit_path, "r", encoding="ascii") as limit_file:
                limit_raw = limit_file.read().strip()
            if limit_raw == "max":
                continue
            current, limit = int(current_raw), int(limit_raw)
            if 0 < limit < (1 << 60):
                return current, limit
        except (OSError, ValueError):
            continue
    return None, None


def ensure_image_generation_memory(
    memory_reader=container_memory,
    max_usage=IMAGE_GENERATION_MAX_USAGE,
    min_headroom_bytes=IMAGE_GENERATION_MIN_HEADROOM_BYTES,
):
    current, limit = memory_reader()
    if current is None or limit is None:
        raise RuntimeError(
            "Image generation deferred: cgroup memory metrics unavailable; "
            "refusing unsafe allocation"
        )
    headroom = limit - current
    usage = current / limit
    if usage >= max_usage or headroom < min_headroom_bytes:
        raise RuntimeError(
            "Image generation deferred by memory guard: "
            f"usage={usage:.1%}, headroom={headroom // (1024 * 1024)}MB, "
            f"required_headroom={min_headroom_bytes // (1024 * 1024)}MB"
        )


def image_process_rss_mb():
    """Read this worker's resident memory without an extra dependency."""
    try:
        with open("/proc/self/status", "r", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def log_image_memory(stage, logger=logging):
    rss_mb = image_process_rss_mb()
    if rss_mb is not None:
        logger.info("IMAGE_MEMORY stage=%s process_rss_mb=%.1f", stage, rss_mb)


def release_image_memory(stage, logger=logging):
    """Collect image buffers and trim free glibc arenas where supported."""
    import gc
    gc.collect()
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except (OSError, AttributeError):
        pass
    log_image_memory(stage, logger)


def normalize_generated_image(image_bytes):
    """Validate and normalize one bounded 1024px image using low temporary memory."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(image_bytes)) as probe:
        width, height = probe.size
        if width < 1 or height < 1 or width * height > 2_500_000:
            raise ValueError(
                f"Generated image dimensions rejected: {width}x{height}; maximum 2.5 MP"
            )
        probe.verify()

    output = io.BytesIO()
    oriented = None
    converted = None
    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            oriented = ImageOps.exif_transpose(opened)
            converted = oriented.convert("RGB")
            converted.thumbnail((1024, 1024), Image.Resampling.BILINEAR)
            quality = 84
            converted.save(output, format="JPEG", quality=quality, optimize=False)
            while output.tell() > 2_000_000 and quality > 64:
                quality -= 5
                output.seek(0)
                output.truncate(0)
                converted.save(output, format="JPEG", quality=quality, optimize=False)
            if output.tell() > 2_000_000:
                raise ValueError("Normalized image exceeds the 2 MB upload limit")
            return output.getvalue()
    finally:
        if converted is not None:
            converted.close()
        if oriented is not None and oriented is not converted:
            oriented.close()
        output.close()
