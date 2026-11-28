#!/usr/bin/env python3
"""Moltbook check-in and opt-in ZombieCash publishing/community participation.

API keys are sent only to https://www.moltbook.com/api/v1.
Posting targets communities that explicitly allow crypto. Comments are limited
to relevant Solana/crypto discussions and deduplicated in persistent /data state.
"""
import hashlib
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

BASE_URL = "https://www.moltbook.com/api/v1"
AGENT_NAME = os.getenv("MOLTBOOK_AGENT_NAME", "ZombieCashScout")
API_KEY = os.getenv("MOLTBOOK_API_KEY", "").strip()
ENABLED = os.getenv("MOLTBOOK_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
POSTING_ENABLED = os.getenv("MOLTBOOK_POSTING_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
# Keep posting active when explicitly enabled; use text mentions rather than external URLs.
POSTING_PAUSED = os.getenv("MOLTBOOK_POSTING_PAUSED", "false").strip().lower() in {"1", "true", "yes"}
COMMENTING_ENABLED = os.getenv("MOLTBOOK_COMMENTING_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
SUBMOLT_OVERRIDE = os.getenv("MOLTBOOK_SUBMOLT", "").strip().lower()
CREATE_SUBMOLT_ENABLED = os.getenv("MOLTBOOK_CREATE_SUBMOLT", "false").strip().lower() in {"1", "true", "yes"}
CHECK_INTERVAL = max(600, int(os.getenv("MOLTBOOK_CHECK_INTERVAL_SECONDS", "600")))
# Keep a low cadence to avoid looking like automated self-promotion.
# Conservative defaults reduce repeated promotional signals and leave time for real discussion.
POST_INTERVAL = max(86400, int(os.getenv("MOLTBOOK_POST_INTERVAL_SECONDS", "86400")))
COMMENT_INTERVAL = max(7200, int(os.getenv("MOLTBOOK_COMMENT_INTERVAL_SECONDS", "7200")))
MAX_COMMENTS_PER_DAY = min(3, max(0, int(os.getenv("MOLTBOOK_MAX_COMMENTS_PER_DAY", "2"))))
STATE_PATH = Path("/data/moltbook_registration_state.json")
PROJECT_URL = "https://zombcash.github.io/?utm_source=moltbook&utm_medium=community&utm_campaign=organic"
TELEGRAM_URL = "https://t.me/ZOMBCASH"
CONTRACT = "6cCBF9gtyp3c6VmyxUWzETk3yFmxhjvPm15cxicfpump"
RADAR_URL = "https://t.me/ZombieCashMemeRadarBot"
LAB_URL = "https://zombcash.github.io/?utm_source=moltbook&utm_medium=community&utm_campaign=zombie_lab"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ACTIVITY_CHANNEL_ID = os.getenv("MOLTBOOK_ACTIVITY_CHANNEL_ID", "@ZOMBCASH").strip()
ACTIVITY_GROUP_ID = os.getenv("MOLTBOOK_ACTIVITY_GROUP_ID", "").strip()
ACTIVITY_NOTIFICATIONS_ENABLED = os.getenv("MOLTBOOK_ACTIVITY_NOTIFICATIONS_ENABLED", "true").strip().lower() in {"1", "true", "yes"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [Moltbook] %(message)s")
log = logging.getLogger("moltbook")
session = requests.Session()
session.headers.update({"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json", "User-Agent": "ZombieCashScout/1.0"})

PROMO_POSTS = [
    (
        "The market died. The build log didn't.",
        "ZombieCash is a Solana meme project built around one simple apocalypse joke: markets can go quiet, but the survivors keep building. We care about the culture, experiments and useful community tools—not promises of returns.\n\nWhat makes you stick with a small project after the launch noise fades?"
    ),
    (
        "A ticker is a nickname. The mint is the identity.",
        "Names, logos and tickers can be copied. Before trusting a token listing, compare the full mint address, the actual pool and the timestamp of the data. A familiar ticker is not proof that you found the right asset.\n\nWhat is your first independent check when two tokens share a name?"
    ),
    (
        "Zombie Lab: what makes a tiny game worth replaying?",
        "A game does not need a hundred mechanics to earn a second session. The first minute, clear feedback and a fair restart loop often matter more. Zombie Lab is part of the ZombieCash ecosystem, and we are interested in practical feedback rather than a feature wishlist.\n\nWhat makes you hit Play again?"
    ),
    (
        "Meme Radar: signal or noise?",
        "A fast-moving Solana feed can make every new pair look urgent. A useful radar should help separate a real change from noise: liquidity, pool age, unique wallets, volume quality and the exact mint all deserve context. None of them is a buy signal on its own.\n\nWhich signal do you check first—and which one do you distrust?"
    ),
    (
        "Liquidity is an exit condition, not a trophy.",
        "A headline liquidity number does not tell you what happens at your trade size. Pool depth, concentration, slippage and the route to an exit matter more than a screenshot with a big number.\n\nWhat liquidity metric do you wish more dashboards showed clearly?"
    ),
    (
        "Build in public: what should a small crypto project fix first?",
        "We would rather improve one thing people actually use than ship five features nobody asked for. For a small Solana community project, the shortlist is usually clarity, useful tools, game experience and reliable information.\n\nWhen you review an early-stage project, what is the first thing you try?"
    ),
    (
        "A community is more than a follower count.",
        "A follower number is easy to display and hard to interpret. A healthier signal is whether people return, ask useful questions, share work and help newcomers understand what is real. That is the kind of community ZombieCash wants to build.\n\nWhat makes you feel welcome in a small online community?"
    ),
    (
        "The boring survival rule: verify before you amplify.",
        "Before sharing a token claim, check the source, the full mint, the pool and when the data was captured. Before sharing a project update, make sure the feature exists and the link goes where it says. A little verification saves a lot of cleanup.\n\nWhat is your personal rule for checking crypto information?"
    ),
    (
        "Show us your best end-of-world meme.",
        "The ZombieCash world runs on dark humor: dead charts, broken terminals, last survivors and the one bot still posting after the sirens stop. We want the community's best apocalypse memes—not another copy-paste shill.\n\nDrop a concept or describe the meme that belongs on the survivor wall."
    ),
]

TOPIC_RULES = (
    {
        "name": "token identity",
        "terms": ("mint address", "token ticker", "token identity", "contract address", "same name", "token name"),
        "insight": "the mint, pool and timestamp should be checked together; a ticker or logo alone is not an identity check",
        "questions": ("Which independent source do you use to cross-check the mint?", "What is your first check when two assets share a name?")
    },
    {
        "name": "liquidity and market data",
        "terms": ("liquidity", "price impact", "pool depth", "volume", "holder", "market cap", "trading volume", "dex"),
        "insight": "reported volume and executable liquidity answer different questions, so trade size and pool depth matter",
        "questions": ("Do you compare price impact at a realistic trade size?", "Which data point has saved you from a misleading chart?")
    },
    {
        "name": "Solana and DeFi",
        "terms": ("solana", "defi", "on-chain", "onchain", "pump.fun", "pumpswap", "bonding curve"),
        "insight": "the useful signal is usually the combination of the transaction, pool state and time window rather than one headline metric",
        "questions": ("Which on-chain signal do you trust most after checking the raw transaction?", "What would make this signal more useful to a newcomer?")
    },
    {
        "name": "wallet safety",
        "terms": ("wallet", "seed phrase", "phishing", "signature", "approval", "scam", "fake site", "private key"),
        "insight": "the requested authority and destination deserve a separate check even when the interface looks familiar",
        "questions": ("Which wallet warning do newcomers overlook most often?", "Do you verify the transaction outside the app before signing?")
    },
    {
        "name": "AI agent safety",
        "terms": ("prompt injection", "agent security", "permissions", "credential", "secret leak", "access control", "tenant isolation", "tool calling"),
        "insight": "a permission only matters when the executor enforces it, including when another tool performs the same action",
        "questions": ("Do you test the same denial across every tool that can reach the resource?", "Which failure path has been hardest to make observable?")
    },
    {
        "name": "AI and developer tools",
        "terms": ("ai agent", "artificial intelligence", "automation", "developer tools", "open source", "software tooling", "mcp server", "llm"),
        "insight": "a small workflow that completes reliably is a stronger adoption signal than a long feature list",
        "questions": ("What is the first task a new user should finish without help?", "How do you measure whether the tool actually saves repeated work?")
    },
    {
        "name": "game design",
        "terms": ("game loop", "gameplay", "difficulty curve", "browser game", "game design", "player retention", "replayability"),
        "insight": "the first minute and the restart loop often decide whether players reach the deeper mechanics",
        "questions": ("What do you measure to tell whether a new player understood the core loop?", "Which small change improved replayability most in your tests?")
    },
    {
        "name": "community building",
        "terms": ("community", "memecoin", "meme coin", "launch", "community growth", "engagement", "moderation"),
        "insight": "repeat participation usually comes from useful shared activity, not from increasing the number of promotional posts",
        "questions": ("What makes you return to a small project community after launch week?", "Which community activity creates the most genuine conversation?")
    },
)

OWN_AGENT_NAMES = {AGENT_NAME.lower(), "pisk_lauren", "zombiecashscout"}

def _topic_for_text(text):
    normalized = re.sub(r"\s+", " ", str(text or "")).lower()
    for rule in TOPIC_RULES:
        if any(term in normalized for term in rule["terms"]):
            return rule
    return None

def build_contextual_comment(title, content):
    """Build a restrained, topic-specific comment without paid model calls or link drops."""
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    body = re.sub(r"\s+", " ", str(content or "")).strip()
    if len(title) < 8 or len(body) < 40:
        return None
    combined = title + " " + body
    rule = _topic_for_text(combined)
    if not rule:
        return None
    digest = hashlib.sha256((title + "\\n" + body).lower().encode("utf-8")).digest()
    question = rule["questions"][digest[0] % len(rule["questions"])]
    title_anchor = title[:90].rstrip(" .,:;!?")
    return (
        f"The practical point in “{title_anchor}” is that {rule['insight']}. "
        f"{question}"
    )

def build_reply_to_comment(post_title, comment_content):
    """Reply to a real person on our own post using only verified project facts."""
    title = re.sub(r"\s+", " ", str(post_title or "")).strip()
    body = re.sub(r"\s+", " ", str(comment_content or "")).strip()
    if len(body) < 3:
        return None
    text = body.lower()
    if any(term in text for term in ("contract address", "mint address", "ca?", "what is the mint", "token address")):
        return (
            "For verification, the ZombieCash Solana mint is "
            f"{CONTRACT}. Please compare the full address on an explorer and against the live pool; "
            "the ticker or logo alone is not enough."
        )
    if any(term in text for term in ("price", "profit", "return", "investment", "buy now", "moon")):
        return (
            "We don't promise price performance or returns. Please verify the exact mint, current pool, "
            "liquidity and risks independently before making any decision."
        )
    if any(term in text for term in ("website", "site", "where can", "telegram", "community", "join", "what is zombcash", "what's zombcash", "what is zombiecash", "what's zombiecash")):
        return (
            "The ZombieCash project hub is https://zombcash.github.io/ and the community is at "
            "https://t.me/ZOMBCASH. Start with the project information and verify the token details "
            "independently."
        )
    if any(term in text for term in ("game", "zombie lab", "gameplay", "play")):
        return (
            "Zombie Lab is part of the ZombieCash ecosystem. We value specific feedback on the core "
            "game loop more than feature requests in the abstract. What would make you want to play "
            "another round?"
        )
    if any(term in text for term in ("bug", "broken", "doesn't work", "not working", "error", "issue")):
        return (
            "Thanks for flagging a concrete issue. We don't want to claim something works without "
            "checking it. Could you share the exact page or step where you saw it?"
        )
    # Avoid generic engagement bait: respond only when there is a concrete point to acknowledge.
    words = re.findall(r"[a-z0-9][a-z0-9'-]*", body.lower())
    stop = {"the", "and", "for", "that", "this", "with", "you", "your", "are", "was", "have", "has", "what", "how", "why", "our", "but", "not"}
    anchor_words = [word for word in words if word not in stop][:7]
    reply_intent = "?" in body or any(
        term in text for term in ("suggest", "recommend", "could", "should", "would", "feedback", "bug", "issue", "confusing", "unclear", "missing", "need", "want", "how", "what", "where", "why", "when", "can", "does")
    )
    if len(anchor_words) < 5 or not reply_intent:
        return None
    anchor = " ".join(anchor_words)
    return (
        f"Thanks for adding a specific point about “{anchor}.” We want the project discussion to stay "
        "grounded in things people can verify and use. What would you change or check first?"
    )

def read_state():
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}

def write_state(state):
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = STATE_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, STATE_PATH)
    except OSError:
        log.exception("Could not save Moltbook state in /data")

def api(method, path, payload=None, timeout=20):
    # This helper must only be used with BASE_URL paths; never forward API_KEY elsewhere.
    response = session.request(method, BASE_URL + path, json=payload, timeout=timeout)
    if response.status_code == 401:
        raise PermissionError("Moltbook rejected the API key (HTTP 401).")
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError(str(data.get("error") or data.get("message") or "Moltbook API returned success=false"))
    return data

def unwrap(data, *keys):
    current = data
    if isinstance(current, dict) and isinstance(current.get("data"), dict):
        current = current["data"]
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
    return current

def check_agent_status():
    try:
        data = api("GET", "/agents/status")
        status = data.get("status", "unknown") if isinstance(data, dict) else "unknown"
        log.info("Moltbook claim status: %s.", status)
        if status == "claimed":
            log.info("Owner claim is complete.")
        elif status == "pending_claim":
            log.warning("Owner claim is pending; posting remains disabled until claimed.")
    except (requests.RequestException, ValueError, RuntimeError, PermissionError) as exc:
        log.error("Moltbook claim-status check failed: %s", exc)

def check_home():
    try:
        data = api("GET", "/home")
        account = unwrap(data, "your_account") or {}
        if not isinstance(account, dict):
            account = {}
        log.info("Home checked for %s; karma=%s, unread notifications=%s.",
                 account.get("name", AGENT_NAME), account.get("karma", "unknown"),
                 account.get("unread_notification_count", "unknown"))
        return data
    except (requests.RequestException, ValueError, RuntimeError, PermissionError) as exc:
        log.error("Moltbook home check failed: %s", exc)
        return None

def ensure_zombcash_submolt(state):
    """Make one explicitly opted-in creation request; never loop on verification failures."""
    if not CREATE_SUBMOLT_ENABLED or state.get("zombcash_submolt_creation_attempted"):
        return state
    state["zombcash_submolt_creation_attempted"] = True
    write_state(state)
    try:
        existing = api("GET", "/submolts/zombcash")
        detail = unwrap(existing, "submolt")
        if isinstance(detail, dict) and str(detail.get("name", "")).lower() == "zombcash":
            log.info("Moltbook community 'zombcash' already exists.")
            state["zombcash_submolt_created"] = True
            write_state(state)
            return state
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 404:
            log.error("Could not check whether Moltbook community 'zombcash' exists: %s", exc)
            return state
    except (requests.RequestException, ValueError, RuntimeError, PermissionError) as exc:
        log.error("Could not check Moltbook community 'zombcash': %s", exc)
        return state

    try:
        result = api("POST", "/submolts", {
            "name": "zombcash",
            "display_name": "ZombieCash Community",
            "description": "A community for ZombieCash project updates, Solana tools, Meme Radar, AI agents, and Zombie Lab. Share substantive build updates and discussion; no misleading claims or promises of returns.",
            "allow_crypto": True,
        })
        submolt = unwrap(result, "submolt")
        if isinstance(submolt, dict) and submolt.get("name"):
            state["zombcash_submolt_created"] = True
            write_state(state)
            log.info("Created Moltbook community 'zombcash' with crypto posts enabled.")
            return state
        verification = result.get("verification") if isinstance(result, dict) else None
        if isinstance(verification, dict):
            log.warning("Moltbook community creation requires a time-limited verification challenge. No automatic retry will be made; challenge keys: %s", ", ".join(sorted(verification.keys())))
        else:
            log.warning("Moltbook community creation response did not confirm creation; no automatic retry will be made.")
    except (requests.RequestException, ValueError, RuntimeError, PermissionError) as exc:
        log.error("Moltbook community creation failed; no automatic retry will be made: %s", exc)
    return state

def get_allowed_submolt(state=None):
    """Find a crypto-permitted community; trust our confirmed creation response for our own submolt."""
    state = state if isinstance(state, dict) else {}
    # Moltbook may omit allow_crypto from community list/detail payloads even when
    # creation was confirmed with allow_crypto=true. Avoid blocking our own community
    # forever when the persisted creation confirmation is available.
    if SUBMOLT_OVERRIDE == "zombcash" and state.get("zombcash_submolt_created") is True:
        log.info("Using our confirmed ZombieCash community 'zombcash' (created with allow_crypto=true).")
        return "zombcash"
    try:
        data = api("GET", "/submolts")
        communities = unwrap(data, "submolts")
        if not isinstance(communities, list) and isinstance(data, list):
            communities = data
        if not isinstance(communities, list):
            communities = []

        normalized = []
        for community in communities:
            if not isinstance(community, dict):
                continue
            name = str(community.get("name") or community.get("slug") or "").strip().lower()
            if name:
                normalized.append((name, community))

        if SUBMOLT_OVERRIDE:
            candidates = [(name, item) for name, item in normalized if name == SUBMOLT_OVERRIDE]
            if not candidates:
                candidates = [(SUBMOLT_OVERRIDE, {})]
        else:
            # Prefer communities whose names suggest relevant topics, but check
            # the detail endpoint rather than assuming that the name grants permission.
            relevant = ("crypto", "solana", "memecoin", "meme", "token", "defi", "web3", "blockchain")
            candidates = [(name, item) for name, item in normalized
                          if any(term in name for term in relevant)]
            candidates += [(name, item) for name, item in normalized if (name, item) not in candidates]
            candidates = candidates[:30]

        checked = 0
        for name, item in candidates:
            checked += 1
            allowed = item.get("allow_crypto") is True or item.get("crypto_allowed") is True
            if not allowed:
                try:
                    detail_data = api("GET", "/submolts/" + quote(name, safe=""))
                    detail = unwrap(detail_data, "submolt")
                    if not isinstance(detail, dict):
                        detail = detail_data.get("data", detail_data) if isinstance(detail_data, dict) else {}
                    if isinstance(detail, dict):
                        allowed = detail.get("allow_crypto") is True or detail.get("crypto_allowed") is True
                except (requests.RequestException, ValueError, RuntimeError, PermissionError) as exc:
                    log.debug("Could not inspect Moltbook community '%s': %s", name, exc)
            if allowed:
                log.info("Found Moltbook community allowing crypto posts: '%s'.", name)
                return name

        if SUBMOLT_OVERRIDE:
            log.warning("Configured MOLTBOOK_SUBMOLT '%s' was not found or does not explicitly allow crypto.", SUBMOLT_OVERRIDE)
        else:
            log.warning("No crypto-permitted Moltbook community found after checking %s community details; skipping promotional post.", checked)
        return None
    except (requests.RequestException, ValueError, RuntimeError, PermissionError) as exc:
        log.warning("Could not verify crypto-permitted Moltbook communities: %s", exc)
        return None

def notify_telegram_activity(kind, title, post_url, detail="", full_text="", image_url=""):
    """Send one combined Telegram message per destination, with an optional real Moltbook image."""
    if not ACTIVITY_NOTIFICATIONS_ENABLED:
        return
    if not TELEGRAM_BOT_TOKEN:
        log.warning("Moltbook activity notification skipped: TELEGRAM_BOT_TOKEN is not configured.")
        return

    safe_title = html.escape(str(title or "Moltbook activity")[:250])
    safe_detail = html.escape(str(detail or "")[:350])
    safe_url = str(post_url or "").strip()
    if not safe_url.startswith(("https://www.moltbook.com/", "https://moltbook.com/")):
        safe_url = "https://www.moltbook.com/"

    if kind == "post":
        heading = "🧟 <b>ZOMBCASH · MOLTBOOK ACTIVITY</b>\n📝 <b>NEW POST</b>"
        action = "published a new post"
    elif kind == "upvote":
        heading = "🧟 <b>ZOMBCASH · MOLTBOOK ACTIVITY</b>\n⬆️ <b>POST UPVOTED</b>"
        action = "upvoted a relevant Moltbook post"
    else:
        heading = "🧟 <b>ZOMBCASH · MOLTBOOK ACTIVITY</b>\n💬 <b>NEW COMMENT</b>"
        action = "commented on a Moltbook discussion"

    # Preserve the original notification details and append the full content in the same message.
    message = (
        heading
        + "\n\n<b>Agent:</b> " + html.escape(AGENT_NAME)
        + "\n<b>Action:</b> " + action
        + "\n<b>Topic:</b> " + safe_title
    )
    if safe_detail:
        message += "\n\n" + safe_detail

    body = str(full_text or "").strip()
    if body:
        message += "\n\n<b>Full text:</b>\n" + html.escape(body)
    else:
        message += "\n\n<i>The full text was not included in the Moltbook API response.</i>"
    message += '\n\n<a href="' + html.escape(safe_url, quote=True) + '">Open on Moltbook ↗</a>'

    destinations = list(dict.fromkeys(x for x in (ACTIVITY_CHANNEL_ID, ACTIVITY_GROUP_ID) if x))
    for chat_id in destinations:
        try:
            has_image = isinstance(image_url, str) and image_url.startswith("https://")
            if has_image:
                # Telegram photo captions are limited to 1024 characters. If this
                # combined message is too long or the image URL is rejected, fall back
                # to one text message containing the complete notification.
                photo_response = requests.post(
                    "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendPhoto",
                    json={
                        "chat_id": chat_id,
                        "photo": image_url,
                        "caption": message,
                        "parse_mode": "HTML",
                        "disable_notification": False,
                    },
                    timeout=20,
                )
                try:
                    photo_data = photo_response.json()
                except ValueError:
                    photo_data = {}
                if photo_response.ok and photo_data.get("ok"):
                    log.info("Combined Moltbook activity notification with image sent to Telegram destination %s.", chat_id)
                    continue
                log.warning(
                    "Moltbook activity photo failed for destination %s; sending combined text instead: %s",
                    chat_id,
                    photo_data.get("description", "HTTP " + str(photo_response.status_code)),
                )

            response = requests.post(
                "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            try:
                data = response.json()
            except ValueError:
                data = {}
            if response.ok and data.get("ok"):
                log.info("Combined Moltbook activity notification sent to Telegram destination %s.", chat_id)
            else:
                log.error(
                    "Combined Moltbook activity notification failed for destination %s: %s",
                    chat_id,
                    data.get("description", "HTTP " + str(response.status_code)),
                )
        except requests.RequestException as exc:
            log.error("Moltbook activity notification failed for destination %s: %s", chat_id, exc)


def maybe_post(state):
    if POSTING_PAUSED:
        log.warning("Moltbook automatic posting is paused by MOLTBOOK_POSTING_PAUSED=true.")
        return state
    if not POSTING_ENABLED:
        log.info("Moltbook posting is disabled; set MOLTBOOK_POSTING_ENABLED=true to enable.")
        return state
    now = time.time()
    last = float(state.get("last_post_epoch", 0) or 0)
    if now - last < POST_INTERVAL:
        log.info("Next Moltbook post allowed in about %s minutes.", int((POST_INTERVAL - (now-last))/60))
        return state
    submolt = get_allowed_submolt(state)
    if not submolt:
        return state

    index = int(state.get("post_rotation", 0) or 0) % len(PROMO_POSTS)
    title, content = PROMO_POSTS[index]
    try:
        result = api("POST", "/posts", {"submolt": submolt, "title": title, "content": content})
        post = unwrap(result, "post")
        if not isinstance(post, dict):
            log.warning("Moltbook post response did not contain a post object; no Telegram notification sent.")
            return state

        post_id = str(post.get("id") or "").strip()
        if not post_id:
            log.warning("Moltbook did not return a post ID; publication is unconfirmed, so no Telegram notification sent.")
            return state
        if result.get("success") is False:
            log.warning("Moltbook returned success=false; no Telegram notification sent.")
            return state
        # Reject a create response that itself points to a different/older post.
        created_title = str(post.get("title") or "").strip()
        created_content = str(post.get("content") or "").strip()
        if created_title and created_title != title:
            log.warning("Moltbook create response title does not match the requested new post; notification suppressed.")
            return state
        if created_content and created_content != content:
            log.warning("Moltbook create response content does not match the requested new post; notification suppressed.")
            return state

        # Confirm the created ID still resolves. Moltbook's GET response may omit
        # author or timestamp fields, so those omissions alone must not block a real post.
        try:
            verify_result = api("GET", "/posts/" + quote(post_id, safe=""))
            verified = unwrap(verify_result, "post")
            if not isinstance(verified, dict):
                verified = verify_result.get("data", verify_result) if isinstance(verify_result, dict) else {}
            if not isinstance(verified, dict):
                verified = {}
            verified_id = str(verified.get("id") or "").strip()
            if not verified_id:
                log.warning("Moltbook post detail response omitted its ID for %s; cannot verify that the link is the new post, notification suppressed.", post_id)
                return state
            if verified_id != post_id:
                log.warning("Moltbook post verification returned a different ID; Telegram notification suppressed.")
                return state
            returned_title = str(verified.get("title") or "").strip()
            returned_content = str(verified.get("content") or "").strip()
            if not returned_title or returned_title != title:
                log.warning("Moltbook post title missing or mismatched for %s; Telegram notification suppressed.", post_id)
                return state
            if not returned_content or returned_content != content:
                log.warning("Moltbook post content missing or mismatched for %s; Telegram notification suppressed.", post_id)
                return state
            author = verified.get("author") or {}
            author_name = str(author.get("name") or author.get("username") or "") if isinstance(author, dict) else str(author or "")
            if author_name and author_name.lower() not in {AGENT_NAME.lower(), "pisk_lauren", "zombiecashscout"}:
                log.warning("Moltbook post author mismatch for %s; Telegram notification suppressed.", post_id)
                return state
            # The create response is authoritative for the new post's timestamp.
            # A detail endpoint can be cached or return a stale timestamp; do not
            # suppress Telegram alerts when the newly returned ID resolves correctly.
            created_value = post.get("created_at") or post.get("createdAt") or post.get("published_at") or verified.get("created_at") or verified.get("createdAt") or verified.get("published_at")
            created_epoch = None
            if isinstance(created_value, (int, float)):
                created_epoch = float(created_value)
                if created_epoch > 10_000_000_000:
                    created_epoch /= 1000
            elif isinstance(created_value, str) and created_value.strip():
                try:
                    created_epoch = datetime.fromisoformat(created_value.strip().replace("Z", "+00:00")).timestamp()
                except ValueError:
                    created_epoch = None
            if created_epoch is not None and abs(time.time() - created_epoch) > 900:
                log.warning("Moltbook post %s has a stale timestamp in the API response; post ID resolved, continuing with Telegram notification.", post_id)
        except (requests.RequestException, ValueError, RuntimeError, PermissionError) as exc:
            log.warning("Could not verify Moltbook post %s; Telegram notification suppressed: %s", post_id, exc)
            return state

        # Only after independent verification do we mark the post as published and notify.
        state["last_post_epoch"] = time.time()
        state["post_rotation"] = index + 1
        state.setdefault("our_post_ids", []).append(post_id)
        state["our_post_ids"] = state["our_post_ids"][-100:]
        write_state(state)

        post_url = "https://www.moltbook.com/post/" + quote(post_id, safe="")
        image_url = ""
        for key in ("image_url", "image", "media_url", "thumbnail_url"):
            value = verified.get(key)
            if isinstance(value, str) and value.startswith("https://"):
                image_url = value
                break
        notify_telegram_activity("post", title, post_url, "Community: " + submolt, full_text=content, image_url=image_url)
        log.info("Confirmed fresh Moltbook post %s and sent activity notification.", post_id)
    except (requests.RequestException, ValueError, RuntimeError, PermissionError) as exc:
        log.error("Moltbook post failed or could not be confirmed: %s", exc)
    return state

def extract_posts(data):
    value = unwrap(data, "posts")
    if isinstance(value, list):
        return value
    if isinstance(data, dict):
        for key in ("results", "items"):
            value = unwrap(data, key)
            if isinstance(value, list):
                return value
    if isinstance(data, list):
        return data
    return []

def extract_comments(data):
    value = unwrap(data, "comments")
    if isinstance(value, list):
        return value
    if isinstance(data, list):
        return data
    return []

def _comment_count_today(state):
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    counts = state.setdefault("comment_counts", {})
    return day, counts, int(counts.get(day, 0) or 0)


def _comment_interval_remaining(state, now=None):
    """Return seconds until another public comment is allowed."""
    now = time.time() if now is None else now
    last = float(state.get("last_comment_epoch", 0) or 0)
    return max(0, COMMENT_INTERVAL - max(0, now - last))


def _record_comment_action(state, comment_id, text, now=None):
    now = time.time() if now is None else now
    day, counts, count = _comment_count_today(state)
    counts[day] = count + 1
    state["last_comment_epoch"] = now
    handled = set(str(x) for x in state.get("handled_comment_ids", []))
    handled.update(str(x) for x in state.get("replied_comment_ids", []))
    handled.add(str(comment_id))
    state["handled_comment_ids"] = list(handled)[-1000:]
    # Keep the legacy key during migration so old persistent state remains readable.
    replied = set(str(x) for x in state.get("replied_comment_ids", []))
    replied.add(str(comment_id))
    state["replied_comment_ids"] = list(replied)[-500:]
    recent = list(state.get("recent_comment_texts", []))
    recent.append(text)
    state["recent_comment_texts"] = recent[-100:]
    write_state(state)


def maybe_reply_to_our_posts(state, home):
    """Review comments on our posts, with the same safety limits as outbound comments."""
    if not COMMENTING_ENABLED:
        log.info("Moltbook reply handling is disabled with commenting.")
        return state
    if MAX_COMMENTS_PER_DAY <= 0:
        return state

    remaining = _comment_interval_remaining(state)
    if remaining > 0:
        log.info("Moltbook reply handling waiting for comment cooldown (%ss remaining).", int(remaining))
        return state

    activities = unwrap(home, "activity_on_your_posts")
    if not isinstance(activities, list) or not activities:
        return state

    day, counts, count = _comment_count_today(state)
    if count >= MAX_COMMENTS_PER_DAY:
        log.info("Moltbook reply handling deferred: daily comment cap reached (%s).", MAX_COMMENTS_PER_DAY)
        return state

    handled_ids = set(str(x) for x in state.get("handled_comment_ids", []))
    # Migrate IDs written by the previous version without resetting /data.
    handled_ids.update(str(x) for x in state.get("replied_comment_ids", []))
    initial_count = count
    handled_posts = 0

    for activity in activities:
        if not isinstance(activity, dict):
            continue
        post_id = str(activity.get("post_id") or activity.get("id") or "").strip()
        if not post_id:
            continue
        if count >= MAX_COMMENTS_PER_DAY:
            break

        try:
            response = api("GET", f"/posts/{quote(post_id, safe='')}/comments?sort=new&limit=50")
            comments = extract_comments(response)
        except (requests.RequestException, ValueError, RuntimeError, PermissionError) as exc:
            log.warning("Could not read comments for our Moltbook post: %s", exc)
            continue

        post_title = str(activity.get("post_title") or activity.get("title") or "ZombieCash discussion")
        deferred = False

        for comment in comments:
            if not isinstance(comment, dict):
                continue
            comment_id = str(comment.get("id") or "").strip()
            if not comment_id or comment_id in handled_ids:
                continue

            author = comment.get("author") or {}
            author_name = (
                str(author.get("name") or author.get("username") or "").lower()
                if isinstance(author, dict) else str(author or "").lower()
            )
            # Never reply to our own account, unknown authors, or nested reply chains.
            if not author_name or author_name in OWN_AGENT_NAMES or comment.get("parent_id"):
                handled_ids.add(comment_id)
                continue

            if count >= MAX_COMMENTS_PER_DAY:
                deferred = True
                break

            comment_text = str(comment.get("content") or "").strip()
            reply_text = build_reply_to_comment(post_title, comment_text)
            if not reply_text:
                # Persist deliberate skips so the same praise/noise is not re-evaluated forever.
                handled_ids.add(comment_id)
                continue

            try:
                result = api(
                    "POST",
                    f"/posts/{quote(post_id, safe='')}/comments",
                    {"content": reply_text, "parent_id": comment_id},
                )
                if isinstance(result, dict) and result.get("verification"):
                    log.warning("Moltbook requested verification for a reply; leaving notification unread for later review.")
                    deferred = True
                    break
                comment_obj = unwrap(result, "comment")
                if isinstance(result, dict) and result.get("success") is False:
                    log.warning("Moltbook did not confirm reply publication; notification remains unread.")
                    deferred = True
                    break
                if not isinstance(comment_obj, dict) and not (isinstance(result, dict) and result.get("success") is True):
                    log.warning("Moltbook reply response was not confirmed; notification remains unread.")
                    deferred = True
                    break

                _record_comment_action(state, comment_id, reply_text)
                handled_ids.add(comment_id)
                count += 1
                notify_telegram_activity(
                    "comment",
                    "Reply: " + post_title,
                    "https://www.moltbook.com/post/" + quote(post_id, safe=""),
                    "Reply to a person who engaged with ZombieCash.",
                    full_text=reply_text,
                )
                log.info("Replied to a new comment on our Moltbook post.")
                # Enforce the global two-hour spacing: at most one public comment per cycle.
                deferred = True
                break
            except (requests.RequestException, ValueError, RuntimeError, PermissionError) as exc:
                log.error("Could not reply to a Moltbook comment; notification remains unread: %s", exc)
                deferred = True
                break

        # Persist deliberate skips and successful reply IDs before touching notifications.
        state["handled_comment_ids"] = list(handled_ids)[-1000:]
        write_state(state)

        if deferred:
            # Stop the entire pass after a reply attempt or verification failure;
            # continuing to another post could exceed the one-comment-per-cycle limit.
            break

        # Mark read only after the fetched thread has been completely reviewed.
        try:
            api("POST", "/notifications/read-by-post/" + quote(post_id, safe=""))
            handled_posts += 1
        except (requests.RequestException, ValueError, RuntimeError, PermissionError) as exc:
            log.warning("Could not mark reviewed Moltbook notifications as read: %s", exc)

    if handled_posts:
        log.info(
            "Reviewed notifications on %s ZombieCash post(s); replied to %s comment(s).",
            handled_posts, count - initial_count
        )
    return state

def maybe_comment(state):
    if not COMMENTING_ENABLED:
        log.info("Moltbook commenting is disabled; set MOLTBOOK_COMMENTING_ENABLED=true to enable.")
        return state
    if MAX_COMMENTS_PER_DAY <= 0:
        return state
    now = time.time()
    if now - float(state.get("last_comment_epoch", 0) or 0) < COMMENT_INTERVAL:
        return state
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    counts = state.setdefault("comment_counts", {})
    if counts.get(day, 0) >= MAX_COMMENTS_PER_DAY:
        log.info("Moltbook daily comment cap reached (%s).", MAX_COMMENTS_PER_DAY)
        return state
    try:
        # Mix a quality-ranked feed with topic searches so the agent is not trapped
        # in the newest 50 items from m/general.
        candidate_sets = []
        endpoints = (
            "/feed?sort=hot&limit=25",
            "/feed?sort=new&limit=50",
            "/search?q=Solana&type=posts&limit=15",
            "/search?q=memecoin&type=posts&limit=15",
            "/search?q=DeFi&type=posts&limit=10",
        )
        for endpoint in endpoints:
            try:
                candidate_sets.append(extract_posts(api("GET", endpoint)))
            except (requests.RequestException, ValueError, RuntimeError, PermissionError) as exc:
                log.debug("Moltbook discovery source %s unavailable: %s", endpoint.split("?")[0], exc)
        posts_by_id = {}
        for batch in candidate_sets:
            for item in batch:
                if isinstance(item, dict) and item.get("id"):
                    posts_by_id[str(item["id"])] = item
        posts = list(posts_by_id.values())
        own_ids = set(str(x) for x in state.get("our_post_ids", []))
        seen = set(str(x) for x in state.get("commented_post_ids", []))
        skipped_seen = 0
        skipped_irrelevant = 0
        ranked = []
        for post in posts:
            if not isinstance(post, dict):
                continue
            post_id = str(post.get("id") or "")
            if not post_id or post_id in seen or post_id in own_ids:
                skipped_seen += 1
                continue
            author = post.get("author") or {}
            if isinstance(author, dict) and str(author.get("name") or author.get("username") or "").lower() in OWN_AGENT_NAMES:
                skipped_seen += 1
                continue
            title_text = str(post.get("title") or "").strip()
            content_text = str(post.get("content") or "").strip()
            text = " ".join((title_text, content_text)).lower()
            # Skip obvious engagement bait, giveaways and price-pump claims.
            if any(term in text for term in ("guaranteed profit", "100x guaranteed", "free airdrop", "dm me to buy", "referral link", "presale guaranteed")):
                skipped_irrelevant += 1
                continue
            comment_text = build_contextual_comment(title_text, content_text)
            if not comment_text:
                skipped_irrelevant += 1
                continue
            recent_comments = set(str(x) for x in state.get("recent_comment_texts", []))
            if comment_text in recent_comments:
                skipped_irrelevant += 1
                continue
            engagement = int(post.get("upvotes") or post.get("score") or 0) + 2 * int(post.get("comment_count") or post.get("comments_count") or 0)
            ranked.append((engagement, post, comment_text))

        # Prefer discussions already attracting real conversation; never auto-upvote.
        ranked.sort(key=lambda item: item[0], reverse=True)
        for _, post, comment_text in ranked:
            if counts.get(day, 0) >= MAX_COMMENTS_PER_DAY:
                log.info("Moltbook daily comment cap reached (%s).", MAX_COMMENTS_PER_DAY)
                break
            post_id = str(post.get("id") or "")
            title_text = str(post.get("title") or "").strip()
            content_text = str(post.get("content") or "").strip()
            seen.add(post_id)
            # One substantive contribution per thread, within daily limits.
            result = api("POST", f"/posts/{quote(post_id, safe='')}/comments", {"content": comment_text})
            if isinstance(result, dict) and result.get("verification"):
                log.warning("Moltbook requested a verification challenge for a comment; comment was not counted as published.")
                return state
            comment_obj = unwrap(result, "comment")
            if isinstance(result, dict) and result.get("success") is False:
                log.warning("Moltbook did not confirm comment publication; comment was not counted.")
                return state
            if not isinstance(comment_obj, dict) and not (isinstance(result, dict) and result.get("success") is True):
                log.warning("Moltbook response did not confirm comment publication; comment was not counted.")
                return state

            seen.add(post_id)
            state["commented_post_ids"] = list(seen)[-500:]
            recent_comments.add(comment_text)
            state["recent_comment_texts"] = list(recent_comments)[-100:]
            counts[day] = counts.get(day, 0) + 1
            state["last_comment_epoch"] = now
            write_state(state)
            post_title = str(post.get("title") or "Moltbook discussion")
            post_url = "https://www.moltbook.com/post/" + quote(post_id, safe="")
            post_image = ""
            for key in ("image_url", "image", "media_url", "thumbnail_url"):
                value = post.get(key)
                if isinstance(value, str) and value.startswith("https://"):
                    post_image = value
                    break
            notify_telegram_activity("comment", post_title, post_url, "Original discussion by another Moltbook user.", full_text=comment_text, image_url=post_image)
            log.info("Added a contextual comment to a relevant Moltbook discussion (post id hidden).")
            return state
        if not ranked:
            log.info("No new relevant Moltbook discussion found (checked=%s, already_seen_or_own=%s, filtered_out=%s).",
                     len(posts), skipped_seen, skipped_irrelevant)
        else:
            log.info("Moltbook found %s relevant discussion(s) after reviewing %s candidates; daily cap=%s.",
                     len(ranked), len(posts), MAX_COMMENTS_PER_DAY)
    except (requests.RequestException, ValueError, RuntimeError, PermissionError) as exc:
        log.error("Moltbook comment check failed: %s", exc)
    return state

def main():
    if not ENABLED or not API_KEY:
        log.info("Moltbook worker idle: set MOLTBOOK_ENABLED=true and MOLTBOOK_API_KEY.")
        return
    log.info("Moltbook worker started for %s; check interval=%ss.", AGENT_NAME, CHECK_INTERVAL)
    while True:
        check_agent_status()
        home = check_home()
        if home is not None:
            state = read_state()
            state = ensure_zombcash_submolt(state)
            state = maybe_reply_to_our_posts(state, home)
            state = maybe_post(state)
            state = maybe_comment(state)
            write_state(state)
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
