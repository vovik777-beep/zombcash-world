"""External publishing adapters retained from the private ZombieCash bot.

LINE here means the existing shared website/API content queue (not a new LINE
social-account publisher). Bluesky publishes a native image post via AT Protocol.
All credentials are read at call time from environment variables.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import urllib.request
import unicodedata


def publish_line_meme(
    caption,
    category,
    image_bytes,
    telegram_message_id,
    *,
    token=None,
    api_url=None,
    opener=urllib.request.urlopen,
):
    """Sync a successfully published Telegram image to the shared content queue."""
    token = (os.getenv("NEWS_PUBLISH_TOKEN", "") if token is None else token).strip()
    api_url = (
        os.getenv(
            "NEWS_PUBLISH_API_URL",
            "https://zombcash-bot.fadehost.app/api/news/published",
        )
        if api_url is None else api_url
    ).strip()
    if not token:
        logging.info("LINE meme sync skipped: NEWS_PUBLISH_TOKEN is not configured in bot worker")
        return False
    telegram_url = f"https://t.me/ZOMBCASH/{telegram_message_id}"
    payload = {
        "kind": "meme",
        "category": str(category)[:80],
        "source": "ZombieCash Community",
        "title": str(caption or "ZombieCash original artwork")[:240],
        "summary": str(caption or "ZombieCash original artwork")[:700],
        "commentary": "Original ZombieCash community artwork.",
        "link": telegram_url,
        "telegramUrl": telegram_url,
    }
    if image_bytes and len(image_bytes) <= 4 * 1024 * 1024:
        payload["imageBase64"] = base64.b64encode(image_bytes).decode("ascii")
    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "ZombieCashMemeBot/1.0",
        },
        method="POST",
    )
    with opener(request, timeout=20) as response:
        result = json.loads(response.read(100_000).decode("utf-8", errors="replace"))
    if not result.get("ok"):
        raise RuntimeError("Shared LINE meme queue did not confirm storage")
    logging.info(
        "Synced LINE meme item category=%s telegram_message_id=%s",
        category, telegram_message_id,
    )
    return True


def bluesky_grapheme_count(value):
    """Count user-perceived characters for Bluesky's 300-grapheme limit."""
    count = 0
    regional_pending = False
    join_next = False
    for char in value:
        code = ord(char)
        is_variation = 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF
        is_emoji_modifier = 0x1F3FB <= code <= 0x1F3FF
        is_regional = 0x1F1E6 <= code <= 0x1F1FF
        if char == "\u200d":
            join_next = True
            continue
        if is_variation or is_emoji_modifier or unicodedata.combining(char):
            continue
        if is_regional:
            if not regional_pending:
                count += 1
            regional_pending = not regional_pending
            join_next = False
            continue
        regional_pending = False
        if join_next:
            join_next = False
            continue
        count += 1
    return count


def bluesky_text_fits(value):
    """Bluesky post limit: 300 grapheme clusters and 3000 UTF-8 bytes."""
    return bluesky_grapheme_count(value) <= 300 and len(value.encode("utf-8")) <= 3000


def publish_bluesky_post(
    text,
    image_bytes=None,
    image_alt="ZombieCash community artwork",
    *,
    handle=None,
    app_password=None,
    service=None,
    opener=urllib.request.urlopen,
):
    """Publish one native-image AT Protocol post using the configured app password."""
    handle = (os.getenv("BLUESKY_HANDLE", "zombcash.bsky.social") if handle is None else handle).strip()
    app_password = (os.getenv("BLUESKY_APP_PASSWORD", "") if app_password is None else app_password).strip()
    if not app_password:
        logging.info("Bluesky publishing skipped: BLUESKY_APP_PASSWORD is not configured")
        return False
    service = (
        os.getenv("BLUESKY_SERVICE_URL", "https://bsky.social") if service is None else service
    ).rstrip("/")
    headers = {"Content-Type": "application/json"}
    session_request = urllib.request.Request(
        service + "/xrpc/com.atproto.server.createSession",
        data=json.dumps({"identifier": handle, "password": app_password}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with opener(session_request, timeout=25) as response:
        session = json.loads(response.read().decode("utf-8"))
    access_jwt = session.get("accessJwt")
    did = session.get("did")
    if not access_jwt or not did:
        raise RuntimeError("Bluesky session response did not include an account session")

    auth_headers = {"Authorization": "Bearer " + access_jwt}
    post_text = (text or "").strip()
    if not post_text:
        raise ValueError("Bluesky post cancelled: text is empty")
    if not bluesky_text_fits(post_text):
        raise ValueError(
            "Bluesky post cancelled: complete text exceeds the platform limit; "
            "the caption was not truncated."
        )
    record = {
        "$type": "app.bsky.feed.post",
        "text": post_text,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    rich_text_facets = []
    for match in re.finditer(r"(?<![A-Za-z0-9_])#([A-Za-z0-9_]{1,64})", post_text):
        byte_start = len(post_text[:match.start()].encode("utf-8"))
        byte_end = len(post_text[:match.end()].encode("utf-8"))
        rich_text_facets.append({
            "index": {"byteStart": byte_start, "byteEnd": byte_end},
            "features": [{
                "$type": "app.bsky.richtext.facet#tag",
                "tag": match.group(1),
            }],
        })
    for match in re.finditer(r"https?://[^\s<>]+", post_text):
        link = match.group(0).rstrip(".,!?;:)]}")
        if not link:
            continue
        byte_start = len(post_text[:match.start()].encode("utf-8"))
        byte_end = byte_start + len(link.encode("utf-8"))
        rich_text_facets.append({
            "index": {"byteStart": byte_start, "byteEnd": byte_end},
            "features": [{
                "$type": "app.bsky.richtext.facet#link",
                "uri": link,
            }],
        })
    if rich_text_facets:
        record["facets"] = rich_text_facets

    if not image_bytes:
        raise RuntimeError("Bluesky post cancelled: native image attachment is required")

    try:
        from PIL import Image
        import io
        source = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        width, height = source.size
        output = io.BytesIO()
        quality = 88
        source.save(output, format="JPEG", quality=quality, optimize=True)
        while output.tell() > 950_000 and quality > 55:
            quality -= 5
            output = io.BytesIO()
            source.save(output, format="JPEG", quality=quality, optimize=True)
        if output.tell() > 950_000:
            raise RuntimeError("Bluesky image remains above the 950 KB upload limit")

        upload_request = urllib.request.Request(
            service + "/xrpc/com.atproto.repo.uploadBlob",
            data=output.getvalue(),
            headers={**auth_headers, "Content-Type": "image/jpeg"},
            method="POST",
        )
        with opener(upload_request, timeout=45) as response:
            blob_payload = json.loads(response.read().decode("utf-8"))
        blob = blob_payload.get("blob")
        if not isinstance(blob, dict) or not blob.get("ref"):
            raise RuntimeError("Bluesky uploadBlob did not return a valid image blob")
        record["embed"] = {
            "$type": "app.bsky.embed.images",
            "images": [{
                "alt": (image_alt or "ZombieCash artwork")[:1000],
                "image": blob,
                "aspectRatio": {"width": width, "height": height},
            }],
        }
    except Exception as exc:
        logging.error(
            "Bluesky native image upload failed; post cancelled (%s)",
            type(exc).__name__,
        )
        raise RuntimeError(
            "Bluesky post cancelled because the image could not be attached"
        ) from exc

    create_payload = {
        "repo": did,
        "collection": "app.bsky.feed.post",
        "record": record,
    }
    create_request = urllib.request.Request(
        service + "/xrpc/com.atproto.repo.createRecord",
        data=json.dumps(create_payload).encode("utf-8"),
        headers={**auth_headers, "Content-Type": "application/json"},
        method="POST",
    )
    with opener(create_request, timeout=30) as response:
        created = json.loads(response.read().decode("utf-8"))
    uri = created.get("uri", "")
    if not uri:
        raise RuntimeError("Bluesky did not return a post URI")
    logging.info("Bluesky post published successfully: %s", uri)
    return True
