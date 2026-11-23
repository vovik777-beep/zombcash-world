import ast
import hashlib
import math
import re
from pathlib import Path

API_PATH = Path("api_server.py")
SCHEMA_PATH = Path("supabase/schema.sql")
API = API_PATH.read_text(encoding="utf-8")
SCHEMA = SCHEMA_PATH.read_text(encoding="utf-8")

tree = ast.parse(API)
functions = {
    node.name: node
    for node in ast.walk(tree)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
required_functions = {
    "_read_survivor_session",
    "_allow_gallery_like",
    "gallery_like_stats",
    "gallery_like_toggle",
}
missing = sorted(required_functions - set(functions))
assert not missing, f"Missing API functions: {missing}"

# Identity must come from the signed session, never from caller-supplied IDs.
toggle = ast.get_source_segment(API, functions["gallery_like_toggle"])
assert "_read_survivor_session(request)" in toggle
assert "payload.get(\"telegram_id\")" not in toggle
assert "payload.get(\"telegramId\")" not in toggle
assert "GALLERY_IMAGE_ID_PATTERN.fullmatch(image_id)" in toggle
assert "isinstance(liked, bool)" in toggle
assert "await _allow_gallery_like(telegram_id)" in toggle
assert 'status=429' in toggle

stats = ast.get_source_segment(API, functions["gallery_like_stats"])
assert "1–50 valid gallery image IDs" in stats
assert "_read_survivor_session(request)" in stats
assert "auth_header and not telegram_id" in stats

# Route registration must bind both public stats and authenticated toggle handlers.
assert 'app.router.add_get("/api/gallery/likes", gallery_like_stats)' in API
assert 'app.router.add_post("/api/gallery/like", gallery_like_toggle)' in API

# Database permissions and uniqueness prevent duplicate likes and browser bypass.
assert "create table if not exists public.gallery_likes" in SCHEMA
assert "primary key (telegram_id, image_id)" in SCHEMA
assert "alter table public.gallery_likes enable row level security" in SCHEMA
assert "revoke all on table public.gallery_likes from public, anon, authenticated" in SCHEMA
assert "grant select, insert, delete on table public.gallery_likes to service_role" in SCHEMA
assert "security invoker" in SCHEMA.lower()
assert "revoke all on function public.get_gallery_like_stats" in SCHEMA
assert "revoke all on function public.toggle_gallery_like" in SCHEMA

# Regression guard: an unlike of the last row must return {image_id, 0, false}.
toggle_sql_start = SCHEMA.index("create or replace function public.toggle_gallery_like")
toggle_sql_end = SCHEMA.index("$function$;", toggle_sql_start)
toggle_sql = SCHEMA[toggle_sql_start:toggle_sql_end]
assert "left join public.gallery_likes l" in toggle_sql.lower()
assert "group by requested.image_id" in toggle_sql.lower()
assert "coalesce(bool_or(l.telegram_id = p_telegram_id), false)" in toggle_sql
assert "on conflict do nothing" in toggle_sql.lower()
assert "existing_like.image_id = p_image_id" in toggle_sql


# Price-less DexScreener pair records must fall through to GeckoTerminal.
price_helpers = [
    node for node in ast.walk(tree)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name in {"_positive_usd_price", "_has_positive_usd_price", "_gecko_pool_price"}
]
namespace = {"math": math}
exec(compile(ast.Module(body=price_helpers, type_ignores=[]), "api_server.py", "exec"), namespace)
positive_price = namespace["_positive_usd_price"]
has_price = namespace["_has_positive_usd_price"]
gecko_pool_price = namespace["_gecko_pool_price"]
assert positive_price("0.000001") == 0.000001
assert positive_price(0.01) == 0.01
assert has_price({"priceUsd": "0.000001"}) is True
assert has_price({"priceUsd": 0.01}) is True
for invalid in (None, "", "0", 0, "-1", "not-a-price", "NaN", "Infinity", "-Infinity"):
    assert positive_price(invalid) is None, f"Invalid price accepted: {invalid!r}"
    assert has_price({"priceUsd": invalid}) is False, f"Invalid pair price accepted: {invalid!r}"

base_pool = {"data": {"attributes": {"base_token_price_usd": "0.0000042", "quote_token_price_usd": "142.0"}, "relationships": {"base_token": {"data": {"id": "solana_6cCBF9gtyp3c6VmyxUWzETk3yFmxhjvPm15cxicfpump"}}, "quote_token": {"data": {"id": "solana_So11111111111111111111111111111111111111112"}}}}}
quote_pool = {"data": {"attributes": {"base_token_price_usd": "142.0", "quote_token_price_usd": "0.0000042"}, "relationships": {"base_token": {"data": {"id": "solana_So11111111111111111111111111111111111111112"}}, "quote_token": {"data": {"id": "solana_6cCBF9gtyp3c6VmyxUWzETk3yFmxhjvPm15cxicfpump"}}}}}
mint = "6cCBF9gtyp3c6VmyxUWzETk3yFmxhjvPm15cxicfpump"
assert gecko_pool_price(base_pool, mint) == 0.0000042
assert gecko_pool_price(quote_pool, mint) == 0.0000042
assert gecko_pool_price(base_pool, "wrong-mint") is None
fetch_zombcash = ast.get_source_segment(API, functions["fetch_zombcash"])
assert "if not any(_has_positive_usd_price(pair) for pair in valid):" in fetch_zombcash
assert "valid_with_price = [pair for pair in valid if _has_positive_usd_price(pair)]" in fetch_zombcash
assert "if not pool_id:" in fetch_zombcash
assert 'await resolve_geckoterminal_pool({"address": ZOMBCASH_ADDRESS})' in fetch_zombcash
assert "if pool_id:" in fetch_zombcash
assert "pool_price = _gecko_pool_price(pool_payload, ZOMBCASH_ADDRESS)" in fetch_zombcash
assert "if price is None:" in fetch_zombcash
assert "pool_txns = pool_attributes.get(\"transactions\") or {}" in fetch_zombcash
assert '"activityAvailable": bool(pool_volume or pool_txns)' in fetch_zombcash
assert "/pools/{pool_id}?include=base_token,quote_token" in fetch_zombcash
assert '"marketDataAvailable": True' in fetch_zombcash


# RSS publication timestamps must sort chronologically across ISO-8601 and RFC-822 formats.
news_date_helper = next(
    node for node in ast.walk(tree)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_news_timestamp"
)
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
news_namespace = {"datetime": datetime, "timezone": timezone, "parsedate_to_datetime": parsedate_to_datetime}
exec(compile(ast.Module(body=[news_date_helper], type_ignores=[]), "api_server.py", "exec"), news_namespace)
news_timestamp = news_namespace["_news_timestamp"]
old_iso = "2026-10-21T08:07:00Z"
new_rfc = "Wed, 04 Nov 2026 23:50:40 GMT"
assert news_timestamp(new_rfc) > news_timestamp(old_iso), "Mixed RSS dates must compare by actual publication time"
assert news_timestamp("2026-11-05T09:00:00+02:00") == news_timestamp("2026-11-05T07:00:00Z")
assert news_timestamp("not-a-date") == 0.0
merge_news = ast.get_source_segment(API, functions["merge_published_news"])
assert 'combined.sort(key=lambda item: _news_timestamp(item.get("published")), reverse=True)' in merge_news
assert API.count('articles.sort(key=lambda item: _news_timestamp(item.get("published")), reverse=True)') == 2

# RSS original-image extraction must prefer source media and safely resolve relative URLs.
import re
import html
image_helpers = [
    node for node in ast.walk(tree)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name in {"_rss_image_url", "_meta_content"}
]
image_namespace = {"re": re, "html": html}
exec(compile(ast.Module(body=image_helpers, type_ignores=[]), "api_server.py", "exec"), image_namespace)
rss_image_url = image_namespace["_rss_image_url"]
meta_content = image_namespace["_meta_content"]
assert rss_image_url(
    {"media_content": [{"url": "//cdn.example.com/original.jpg", "type": "image/jpeg"}]},
    "https://news.example.com/story"
) == "https://cdn.example.com/original.jpg"
assert rss_image_url(
    {"summary": '<p>Story</p><img src="/images/story.webp">'},
    "https://news.example.com/story"
) == "https://news.example.com/images/story.webp"
assert rss_image_url(
    {"links": [{"href": "https://cdn.example.com/video.mp4", "type": "video/mp4", "rel": "enclosure"}]},
    "https://news.example.com/story"
) == ""
assert meta_content('<meta content="https://cdn.example.com/a.jpg" property="og:image">', "content") == "https://cdn.example.com/a.jpg"

# News must use the publisher's original article image only; no generated illustrations.
NEWS_PATH = Path("news_worker.py")
NEWS = NEWS_PATH.read_text(encoding="utf-8")
news_tree = ast.parse(NEWS)
news_functions = {
    node.name: node
    for node in ast.walk(news_tree)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
run_worker = ast.get_source_segment(NEWS, news_functions["run_worker"])
assert "download_article_image" in run_worker
assert "NEWS_SKIPPED_NO_IMAGE" in run_worker
assert "generate_illustration(" not in run_worker
assert "send_photo(" in run_worker
print("RSS mixed-date chronological sorting checks passed.")
print("Market price provider fallback and side-specific GeckoTerminal pool checks passed.")
print("Gallery likes API contract checks passed.")
print("Regression covered: last unlike returns one row with count=0 and liked=false.")
