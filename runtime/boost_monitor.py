import json
import hashlib
import math
import os
import time
from datetime import datetime, timezone
from html import escape
from urllib.parse import quote
import requests

DEX = "https://api.dexscreener.com"
STATE_DIR = "/data"
STATE_FILE = os.path.join(STATE_DIR, "boost_seen_tokens.json")
BASELINE_FILE = os.path.join(STATE_DIR, "boost_monitor_initialized.json")
INTERVAL = 900
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 ZombieCashBoostMonitor/5.0"})

def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default

def save_json(path, data):
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp, path)

def get_json(url):
    r = session.get(url, timeout=15)
    r.raise_for_status()
    return r.json()

def get_boosts():
    data = get_json(f"{DEX}/token-boosts/latest/v1")
    return data if isinstance(data, list) else []


def get_pairs(chain, address):
    data = get_json(f"{DEX}/token-pairs/v1/{chain}/{address}")
    if not isinstance(data, list):
        return []
    return sorted(data, key=lambda p: ((p.get("liquidity") or {}).get("usd") or 0), reverse=True)

def get_pumpfun_livestream_status(address):
    """
    Read pump.fun's public coin metadata and its is_currently_live flag.
    Return LIVE, OFFLINE, UNKNOWN, or NOT_PUMPFUN. Never infer OFFLINE from
    an API error or a missing field.
    """
    address = str(address or "").strip()
    if not address.endswith("pump"):
        return "NOT_PUMPFUN"

    url = f"https://frontend-api-v3.pump.fun/coins/{quote(address, safe='')}"
    try:
        response = session.get(url, timeout=8, headers={"Accept": "application/json"})
        if response.status_code == 404:
            return "UNKNOWN"
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        if not isinstance(data, dict):
            return "UNKNOWN"

        live = data.get("is_currently_live")
        if live is None:
            live = data.get("isCurrentlyLive")
        if live is True:
            return "LIVE"
        if live is False:
            return "OFFLINE"
        return "UNKNOWN"
    except (requests.RequestException, ValueError, TypeError) as exc:
        print(f"Pump.fun livestream status unavailable for {address}: {exc}")
        return "UNKNOWN"


def livestream_label(status):
    return {
        "LIVE": "🔴 <b>Pump.fun LIVE NOW</b>",
        "OFFLINE": "⚫ Pump.fun LIVE: <b>OFFLINE</b>",
        "UNKNOWN": "⚪ Pump.fun LIVE: <b>UNKNOWN</b> (status unavailable)",
        "NOT_PUMPFUN": "➖ Pump.fun LIVE: <b>N/A</b> (not a pump.fun mint)",
    }.get(status, "⚪ Pump.fun LIVE: <b>UNKNOWN</b>")


def get_profile_image(address):
    # Prefer the dedicated token profile icon over a generic boost/banner image.
    try:
        data = get_json(f"{DEX}/token-profiles/latest/v1")
        if isinstance(data, list):
            for x in data:
                if str(x.get("chainId", "")).lower() == "solana" and str(x.get("tokenAddress", "")) == address:
                    icon = x.get("icon")
                    if isinstance(icon, str) and icon.startswith(("https://", "http://")):
                        return icon
    except (requests.RequestException, ValueError) as exc:
        print(f"Token profile lookup failed: {exc}")
    return None


def image_is_icon(url):
    # Validate the actual downloaded image bytes. Some image hosts return
    # application/octet-stream or omit Content-Length, so do not reject on headers alone.
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        content = r.content
        r.close()
        if not content or len(content) > 8 * 1024 * 1024:
            return False
        try:
            from PIL import Image
            import io
            with Image.open(io.BytesIO(content)) as im:
                im.verify()
            with Image.open(io.BytesIO(content)) as im:
                w, h = im.size
                if w < 80 or h < 80:
                    return False
                # Allow near-square icons, but reject clearly wide banner images.
                if max(w / h, h / w) > 1.35:
                    return False
        except ImportError:
            print("Pillow is not installed; run: python -m pip install Pillow")
            return False
        except Exception:
            return False
        return True
    except (requests.RequestException, ValueError, OSError):
        return False

def clean(v, limit=240):
    s = " ".join(str(v or "").split())
    return s if len(s) <= limit else s[:limit-1] + "…"

def money(v):
    try:
        n = float(v)
        if n >= 1e9: return f"${n/1e9:.2f}B"
        if n >= 1e6: return f"${n/1e6:.2f}M"
        if n >= 1e3: return f"${n/1e3:.1f}K"
        return f"${n:,.2f}" if n >= 1 else f"${n:.6f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "N/A"

def price(v):
    """Show small token prices in ordinary decimal notation, preserving leading zeros."""
    try:
        n = float(v)
        if not math.isfinite(n) or n <= 0:
            return "N/A"
        if n < 1e-12:
            # Extremely tiny values cannot be displayed usefully with a reasonable
            # fixed decimal width; scientific notation is the fallback only here.
            return f"${n:.3e}"
        if n < 1:
            decimals = min(12, max(4, int(-math.floor(math.log10(n))) + 4))
            return "$" + f"{n:.{decimals}f}".rstrip("0").rstrip(".")
        return "$" + f"{n:,.8f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError, OverflowError):
        return "N/A"

def age_from_ms(ms):
    try: seconds = max(0, int(time.time() - int(ms)/1000))
    except (TypeError, ValueError, OverflowError): return None
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    return f"{days}d {hours}h" if days else (f"{hours}h {minutes}m" if hours else f"{minutes}m")

def make_caption(item, pair, event_type, old=None):
    token = (pair or {}).get("baseToken") or {}
    name = clean(token.get("name") or item.get("name") or "Unknown token", 80)
    symbol = clean(token.get("symbol") or item.get("symbol") or "UNKNOWN", 24)
    address = str(item.get("tokenAddress") or "")
    # Telegram supports native expandable blockquotes. The user sees a compact
    # preview and can tap Telegram's "Show more" affordance to reveal the rest.
    raw_desc = clean(item.get("description") or "", 3000)
    desc = raw_desc
    created = (pair or {}).get("pairCreatedAt")
    pair_age = age_from_ms(created)
    liq = (pair or {}).get("liquidity") or {}
    vol = (pair or {}).get("volume") or {}
    h24 = ((pair or {}).get("txns") or {}).get("h24") or {}
    dex_url = (pair or {}).get("url") or f"https://dexscreener.com/solana/{quote(address, safe='')}"
    heading = "🆕 NEW DEX SCREENER BOOST" if event_type == "new" else "📈 BOOST INCREASED"
    # Make high-boost tokens stand out. Trigger when either the current boost
    # amount or the accumulated total reaches 100, since both are shown below.
    amount_value = number(item.get("amount"))
    total_value = number(item.get("totalAmount"))
    hot_boost = ((amount_value is not None and amount_value >= 100) or
                 (total_value is not None and total_value >= 100))

    lines = [
        ("<b>🚨🔥 100+ BOOSTS — HOT TOKEN 🔥🚨</b>" if hot_boost else f"<b>{heading}</b>"),
        *([f"<b>⚡ HIGH BOOST ACTIVITY · {escape(str(item.get('totalAmount', item.get('amount', '100+'))))} BOOSTS ⚡</b>"] if hot_boost else []),
        "",
        f"<b>{escape(name)} (${escape(symbol)})</b>",
        "🌐 Network: <b>Solana</b>",
        f"📋 CA: <code>{escape(address)}</code>",
    ]

    if desc:
        lines += ["", f"📝 <blockquote expandable>{escape(desc)}</blockquote>"]
    if pair_age:
        lines.append(f"🕒 Pair age: <b>{escape(pair_age)}</b> <i>(not necessarily token age)</i>")
        try:
            if int(time.time() - int(created) / 1000) < 86400:
                lines.append("<b>🆕 NEW PAIR · Under 24h</b>")
        except (TypeError, ValueError, OverflowError):
            pass

    if event_type == "increase" and old:
        lines += [
            "",
            (f"🔥🚀 Boosts: <b>{escape(str(old.get('amount', 'N/A')))} → {escape(str(item.get('amount', 'N/A')))}</b> 🔥" if hot_boost else
             f"🚀 Boosts: <b>{escape(str(old.get('amount', 'N/A')))} → {escape(str(item.get('amount', 'N/A')))}</b>"),
            (f"💥 📊 Total: <b>{escape(str(old.get('totalAmount', 'N/A')))} → {escape(str(item.get('totalAmount', 'N/A')))}</b> 💥" if hot_boost else
             f"📊 Total: <b>{escape(str(old.get('totalAmount', 'N/A')))} → {escape(str(item.get('totalAmount', 'N/A')))}</b>"),
        ]
    else:
        lines += [
            "",
            (f"🔥🚀 BOOSTS: <b>{escape(str(item.get('amount', 'N/A')))}</b>  |  💥 TOTAL: <b>{escape(str(item.get('totalAmount', 'N/A')))}</b> 🔥" if hot_boost else
             f"🚀 Boosts: <b>{escape(str(item.get('amount', 'N/A')))}</b>  |  Total: <b>{escape(str(item.get('totalAmount', 'N/A')))}</b>"),
        ]

    live_status = get_pumpfun_livestream_status(address)

    market_cap = (pair or {}).get("marketCap")
    cap_value = money(market_cap) if market_cap is not None else "N/A"
    price_value = price((pair or {}).get("priceUsd"))
    liquidity_value = money(liq.get("usd"))
    volume_value = money(vol.get("h24"))
    buys = h24.get("buys", 0)
    sells = h24.get("sells", 0)

    # Use ordinary HTML text instead of <pre>: Telegram renders <pre> as a
    # selectable/copy-style block. These compact rows remain easy to scan.
    lines += [
        "",
        "<b>📊 MARKET DATA</b>",
        livestream_label(live_status),
        f"💵 Price: <b>{escape(price_value)}</b>     🧢 Market Cap: <b>{escape(cap_value)}</b>",
        f"💧 Liquidity: <b>{escape(liquidity_value)}</b>",
        f"📈 24h Volume: <b>{escape(volume_value)}</b>",
        f"🟢 Buys: <b>{escape(str(buys))}</b>     🔴 Sells: <b>{escape(str(sells))}</b>",
        f"<i>Detected: {escape(utc_now())}</i>",
    ]
    project_links = format_project_links(pair, item)
    if project_links:
        lines += ["", project_links]
    caption = "\n".join(lines)
    # Telegram captions are limited to 1024 characters. Never slice raw HTML:
    # that can cut an <a href="..."> tag in half and make Telegram reject the post.
    if len(caption) > 1024:
        compact_lines = list(lines)
        # Drop optional content first while preserving the core token details.
        if project_links and project_links in compact_lines:
            compact_lines.remove(project_links)
        if desc:
            compact_lines = [line for line in compact_lines if "<blockquote expandable>" not in line]
        caption = "\n".join(compact_lines)
    if len(caption) > 1024:
        # Last-resort compact HTML-safe caption. Each line is independently valid
        # markup; only complete lines are retained, never a partial tag.
        kept = []
        for line in lines:
            candidate = "\n".join(kept + [line])
            if len(candidate) <= 1024:
                kept.append(line)
        caption = "\n".join(kept)
    return caption


def keyboard(address, pair, item=None):
    """Only trading-platform buttons are shown below the message."""
    a = quote(address, safe="")
    rows = [
        [
            {"text": "DEX Screener", "url": f"https://dexscreener.com/solana/{a}"},
            {"text": "Photon", "url": f"https://photon-sol.tinyastro.io/en/lp/{a}"},
        ],
        [
            {"text": "GMGN", "url": f"https://gmgn.ai/sol/token/{a}"},
            {"text": "BullX", "url": f"https://bullx.io/terminal?chainId=1399811149&address={a}"},
        ],
        [
            {"text": "Jupiter", "url": f"https://jup.ag/swap/SOL-{a}"},
            {"text": "Pump.fun", "url": f"https://pump.fun/coin/{a}"},
        ],
    ]
    return {"inline_keyboard": rows}


def valid_http_url(value):
    """Accept only ordinary HTTP(S) URLs for Telegram links."""
    return isinstance(value, str) and value.startswith(("https://", "http://"))


def format_project_links(pair, item=None):
    """Compact linked labels for token website and social accounts in the caption."""
    found = []
    info = (pair or {}).get("info") or {}

    for site in info.get("websites") or []:
        if isinstance(site, dict):
            url = site.get("url")
            label = clean(site.get("label") or "Website", 24)
            if valid_http_url(url):
                found.append((f"🌐 {label}", url))

    sources = []
    for social in info.get("socials") or []:
        if isinstance(social, dict):
            sources.append((social.get("platform") or social.get("type") or social.get("label") or "", social.get("url")))
    for link in (item or {}).get("links") or []:
        if isinstance(link, dict):
            sources.append((link.get("type") or link.get("label") or "", link.get("url")))

    for platform, url in sources:
        if not valid_http_url(url):
            continue
        kind = str(platform).lower()
        lower_url = url.lower()
        if "telegram" in kind or "t.me/" in lower_url:
            label = "✈️ Telegram"
        elif "twitter" in kind or kind == "x" or "x.com/" in lower_url or "twitter.com/" in lower_url:
            label = "𝕏 X"
        elif "discord" in kind or "discord.gg/" in lower_url or "discord.com/" in lower_url:
            label = "🎮 Discord"
        elif "instagram" in kind or "instagram.com/" in lower_url:
            label = "📸 Instagram"
        elif "youtube" in kind or "youtu.be/" in lower_url:
            label = "▶️ YouTube"
        elif "tiktok" in kind or "tiktok.com/" in lower_url:
            label = "🎵 TikTok"
        elif "github" in kind or "github.com/" in lower_url:
            label = "💻 GitHub"
        elif "medium" in kind or "medium.com/" in lower_url:
            label = "📝 Medium"
        else:
            label = "🔗 Social"
        found.append((label, url))

    seen = set()
    unique = []
    for label, url in found:
        key = url.rstrip("/")
        if key not in seen:
            seen.add(key)
            unique.append((label, url))
    if not unique:
        return ""
    return "  ".join(
        f'<a href="{escape(url, quote=True)}">{escape(label)}</a>'
        for label, url in unique
    )

def telegram_call(token, method, payload):
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=20)
    try: data = r.json()
    except ValueError:
        r.raise_for_status()
        raise RuntimeError("Telegram returned unreadable response")
    if not r.ok or not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data.get('description', f'HTTP {r.status_code}')}")
    return data["result"]

def make_fallback_icon(address, item):
    """Create a generic square boost card when no usable token logo is available."""
    import io
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (512, 512), (9, 18, 22))
    draw = ImageDraw.Draw(image)
    for radius, color, width in [
        (190, (25, 105, 82), 5),
        (140, (22, 77, 67), 4),
        (90, (22, 77, 67), 3),
    ]:
        draw.ellipse((256-radius, 215-radius, 256+radius, 215+radius), outline=color, width=width)
    draw.line((256, 35, 256, 395), fill=(34, 139, 100), width=2)
    draw.line((76, 215, 436, 215), fill=(34, 139, 100), width=2)
    draw.ellipse((245, 204, 267, 226), fill=(96, 255, 166))
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 30)
        small_font = ImageFont.truetype("DejaVuSans.ttf", 20)
    except OSError:
        title_font = ImageFont.load_default()
        small_font = ImageFont.load_default()
    title = "SOLANA BOOST"
    box = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((512 - (box[2] - box[0])) / 2, 405), title, font=title_font, fill=(135, 255, 190))
    short_address = address[:6] + "..." + address[-6:]
    box = draw.textbbox((0, 0), short_address, font=small_font)
    draw.text(((512 - (box[2] - box[0])) / 2, 455), short_address, font=small_font, fill=(205, 218, 218))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def send_photo(token, chat_id, image_source, caption, buttons):
    # Upload a real token icon, or a generated neutral square card if none exists.
    if isinstance(image_source, bytes):
        photo_bytes = image_source
        filename = "solana-boost-fallback.png"
        content_type = "image/png"
    else:
        r = session.get(image_source, timeout=15)
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "").lower()
        if not content_type.startswith("image/"):
            raise RuntimeError("Image URL did not return an image")
        photo_bytes = r.content
        filename = "token-icon"
    if len(photo_bytes) > 8 * 1024 * 1024:
        raise RuntimeError("Image exceeds Telegram upload limit")
    files = {"photo": (filename, photo_bytes, content_type.split(";")[0])}
    data = {
        "chat_id": chat_id,
        "caption": caption[:1024],
        "parse_mode": "HTML",
        "reply_markup": json.dumps(buttons, ensure_ascii=False),
    }
    response = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto",
                             data=data, files=files, timeout=30)
    try:
        result = response.json()
    except ValueError:
        response.raise_for_status()
        raise RuntimeError("Telegram returned unreadable response")
    if not response.ok or not result.get("ok"):
        description = result.get("description", f"HTTP {response.status_code}")
        # Retry entity-parse failures once as plain text. Keep the generated
        # fallback image and inline trading buttons; malformed optional links
        # must not block the token alert itself.
        if "can't parse entities" in str(description).lower() or "parse entities" in str(description).lower():
            import re
            from html import unescape
            plain_caption = unescape(re.sub(r"<[^>]*>", "", caption))[:1024]
            retry_data = {"chat_id": chat_id, "caption": plain_caption,
                          "reply_markup": json.dumps(buttons, ensure_ascii=False)}
            retry = requests.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data=retry_data, files=files, timeout=30
            )
            try:
                retry_result = retry.json()
            except ValueError:
                retry.raise_for_status()
                raise RuntimeError("Telegram returned unreadable response on plain-caption retry")
            if retry.ok and retry_result.get("ok"):
                print("Published with plain-text caption after Telegram rejected HTML entities.")
                return
            description = retry_result.get("description", f"HTTP {retry.status_code}")
        raise RuntimeError(f"Telegram sendPhoto failed: {description}")

def number(v):
    try: return float(v)
    except (TypeError, ValueError): return None

def main():
    token = os.getenv("BOOST_MONITOR_BOT_TOKEN", "").strip()
    chat_id = os.getenv("BOOST_MONITOR_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Boost monitor disabled: set BOOST_MONITOR_BOT_TOKEN and BOOST_MONITOR_CHAT_ID in FadeHost.")
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    seen = load_json(STATE_FILE, {})
    initialized = os.path.exists(BASELINE_FILE)
    print("ZombieCash Boost Monitor started.")
    print(f"Destination: configured group only ({chat_id}).")
    print("Filter: Solana only. Image required. Polling every 15 minutes. Ctrl+C to stop.")
    print("New token and boost-count increases can trigger posts; count increases are not proof of a separate payment.")

    while True:
        try:
            raw = get_boosts()
            boosts = [x for x in raw if str(x.get("chainId", "")).lower() == "solana" and x.get("tokenAddress")]
            if not initialized:
                now = utc_now()
                for item in boosts:
                    key = f"solana:{item['tokenAddress']}"
                    seen.setdefault(key, {"amount":number(item.get("amount")), "totalAmount":number(item.get("totalAmount")),
                                          "first_seen":now, "baseline":True})
                save_json(STATE_FILE, seen)
                save_json(BASELINE_FILE, {"initialized_at":now})
                initialized = True
                print(f"Initial Solana baseline saved: {len(boosts)} records. No messages sent.")
            else:
                for item in boosts:
                    address = str(item["tokenAddress"])
                    key = f"solana:{address}"
                    old = seen.get(key)
                    amount, total = number(item.get("amount")), number(item.get("totalAmount"))
                    if old is None:
                        event = "new"
                    elif ((amount is not None and old.get("amount") is not None and amount > old["amount"]) or
                          (total is not None and old.get("totalAmount") is not None and total > old["totalAmount"])):
                        event = "increase"
                    else:
                        continue
                    try:
                        pairs = get_pairs("solana", address)
                        pair = pairs[0] if pairs else {}
                        # Prefer a token-profile icon, then pair icon. The boost record
                        # may contain a banner or unrelated image, so use it only as a last resort.
                        candidates = [
                            get_profile_image(address),
                            (pair.get("info") or {}).get("imageUrl"),
                            item.get("icon"),
                        ]
                        image = next((u for u in candidates
                                      if isinstance(u, str)
                                      and u.startswith(("https://", "http://"))
                                      and image_is_icon(u)), None)
                        if image:
                            print(f"Using token icon for {address}.")
                        else:
                            # Do not retry forever when a boosted token has no accessible logo.
                            image = make_fallback_icon(address, item)
                            print(f"No valid token icon for {address}; using generated square fallback card.")
                        cap = make_caption(item, pair, event, old)
                        send_photo(token, chat_id, image, cap, keyboard(address, pair, item))
                        seen[key] = {"amount":amount, "totalAmount":total, "first_seen":(old or {}).get("first_seen", utc_now()),
                                     "last_posted":utc_now(), "baseline":False}
                        save_json(STATE_FILE, seen)
                        print(f"Published {event} with image: {address}")
                    except (requests.RequestException, ValueError, RuntimeError) as exc:
                        print(f"Could not publish Solana token {address}: {exc}; will retry.")
            print(f"{datetime.now():%H:%M:%S}: {len(boosts)} Solana boost records checked.")
        except (requests.RequestException, ValueError) as exc:
            print(f"DEX Screener API error: {exc}")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: print("\nBoost Monitor stopped.")