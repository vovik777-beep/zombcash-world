import asyncio
import base64
import calendar
import hashlib
import html
import json
import logging
import os
import re
import sqlite3
import urllib.parse
import urllib.request
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from html.parser import HTMLParser

import feedparser
from telegram import Bot
from telegram.request import HTTPXRequest

from .publish_target import get_publish_api_url, get_publish_target
from google import genai

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

RUNTIME_ENV = os.getenv("ZOMBCASH_ENV", "").strip().lower()
if RUNTIME_ENV not in {"staging", "production"}:
    raise RuntimeError("Set ZOMBCASH_ENV explicitly to staging or production before starting the RSS worker")

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID = get_publish_target()
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "").strip()
NEWS_PUBLISH_TOKEN = os.getenv("NEWS_PUBLISH_TOKEN", "").strip()
NEWS_PUBLISH_API_URL = get_publish_api_url() or ""
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.8-flash").strip()
GEMINI_SECONDARY_FALLBACK_MODEL = os.getenv("GEMINI_SECONDARY_FALLBACK_MODEL", "gemini-3.6-flash").strip()
GEMINI_MODELS = tuple(dict.fromkeys(
    model for model in (GEMINI_MODEL, GEMINI_FALLBACK_MODEL, GEMINI_SECONDARY_FALLBACK_MODEL) if model
))
client = genai.Client(api_key=GEMINI_API_KEY)

FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "The Block": "https://www.theblock.co/feed/",
    "Decrypt": "https://decrypt.co/feed",
    "MIT Technology Review": "https://www.technologyreview.com/stories.rss",
    "IEEE Spectrum": "https://spectrum.ieee.org/rss/fulltext",
}
FEED_FALLBACKS = {
    "IEEE Spectrum": ("https://feeds2.feedburner.com/IeeeSpectrum",),
}

NEWS_MAX_AGE_HOURS = max(1, int(os.getenv("NEWS_MAX_AGE_HOURS", "3")))
# No daily publication cap or artificial cooldown: publish fresh eligible stories as they appear.
# RSS polling remains every 10 minutes; one story is delivered per cycle to avoid bursts.
NEWS_RELEVANCE_TERMS = {
    "crypto": ("bitcoin", "ethereum", "solana", "cryptocurrency", "crypto market", "crypto", "digital asset", "digital assets", "blockchain", "stablecoin", "defi", "web3", "memecoin", "meme coin", "pump.fun", "dex", "airdrop", "token launch", "token", "exchange", "wallet", "trading", "traders", "onchain", "on-chain", "custody", "staking", "mining", "bitcoin etf", "ethereum etf", "prediction market", "crypto regulation", "sec", "cftc"),
    "technology": ("artificial intelligence", " ai ", "machine learning", "robotics", "robot", "semiconductor", "chipmaker", "quantum computing", "biotechnology", "cybersecurity", "open-source model", "large language model", "neural network", "researchers develop", "scientists develop", "scientific discovery", "engineering", "energy storage", "battery", "batteries", "fusion energy", "new material", "materials science", "medical technology", "biomedical", "genome editing", "gene therapy", "clinical trial", "organ transplant", "bioengineering", "computer", "software", "hardware", "electronics", "power grid", "aerospace", "telecommunications", "wireless", "manufacturing", "automation", "satellite technology", "spacecraft"),
}
NEWS_PRIORITY_TERMS = {
    100: ("exploit", "rug pull", "drained", "data breach", "security breach", "stolen funds", "hacked", "hackers breached"),
    95: ("solana", "pump.fun", "memecoin", "meme coin", "token launch", "airdrop"),
    85: ("defi", "dex", "stablecoin", "crypto exchange", "bitcoin etf", "ethereum etf", "prediction market"),
    75: ("funding", "raises", "investment", "acquisition", "merger", "mainnet launch", "product launch"),
    65: ("bitcoin", "ethereum", "crypto market", "blockchain", "artificial intelligence", "machine learning", "robotics", "semiconductor", "quantum computing"),
}
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Daily publication counter; reset at midnight in Ukraine.
NEWS_JOURNAL_FILE = os.path.join("/data" if os.path.isdir("/data") and os.access("/data", os.W_OK) else BASE_DIR, "news_daily_journal.json")
NEWS_JOURNAL_MAX_ITEMS = 120
NEWS_STOPWORDS = {"about","after","amid","and","are","been","being","from","have","into","more","over","says","said","that","their","there","these","this","through","under","were","what","when","which","while","with","will","would","report","reports","update","latest","news","breaking","according","company","companies","announces","announced","announcement","launch","launched","new","users","user","platform","technology","research","researchers","study","studies","today","could","would","also","first","second","major","year","years"}
NEWS_ALIASES = {"hacked":"hack","hackers":"hack","hacker":"hack","exploit":"incident","exploited":"incident","attack":"incident","attacked":"incident","breach":"incident","compromised":"incident","drained":"drain","loses":"loss","lost":"loss"}

def news_event_tokens(article):
    text = " ".join((str(article.get("title", "")), str(article.get("summary", ""))[:500])).lower()
    return sorted({NEWS_ALIASES.get(w, w) for w in re.findall(r"[a-z0-9]+", text) if len(w) >= 3 and w not in NEWS_STOPWORDS and not w.isdigit()})

def load_daily_news_journal():
    today = datetime.now(ZoneInfo("Europe/Kyiv")).date().isoformat()
    try:
        with open(NEWS_JOURNAL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("date") != today:
            raise ValueError("new day")
        data.setdefault("published", [])
        data.setdefault("duplicates_blocked", 0)
        data.setdefault("published_by_source", {})
        data.setdefault("duplicates_by_source", {})
        data.setdefault("duplicate_ids", [])
        data.setdefault("published_ids", [])
        data.setdefault("skipped_no_image_ids", [])
        data.setdefault("send_attempted_ids", [])
        data["published"] = [x for x in data["published"] if isinstance(x, dict)][-NEWS_JOURNAL_MAX_ITEMS:]
        return data
    except (OSError, ValueError, json.JSONDecodeError):
        return {"date": today, "published": [], "published_ids": [], "skipped_no_image_ids": [], "send_attempted_ids": [], "duplicates_blocked": 0, "published_by_source": {}, "duplicates_by_source": {}, "duplicate_ids": []}

def refresh_daily_news_journal(journal):
    """Roll the in-memory journal over at Kyiv midnight, even if the worker stays running."""
    today = datetime.now(ZoneInfo("Europe/Kyiv")).date().isoformat()
    if journal.get("date") != today:
        journal = {"date": today, "published": [], "published_ids": [], "skipped_no_image_ids": [], "send_attempted_ids": [], "duplicates_blocked": 0, "published_by_source": {}, "duplicates_by_source": {}, "duplicate_ids": []}
        save_daily_news_journal(journal)
        logging.info("NEWS_DAILY_JOURNAL_RESET date=%s", today)
    return journal


def save_daily_news_journal(journal):
    try:
        os.makedirs(os.path.dirname(NEWS_JOURNAL_FILE), exist_ok=True)
        journal["published"] = journal.get("published", [])[-NEWS_JOURNAL_MAX_ITEMS:]
        journal["published_ids"] = journal.get("published_ids", [])[-500:]
        journal["skipped_no_image_ids"] = journal.get("skipped_no_image_ids", [])[-500:]
        journal["send_attempted_ids"] = journal.get("send_attempted_ids", [])[-500:]
        temp = NEWS_JOURNAL_FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(journal, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, NEWS_JOURNAL_FILE)
    except OSError:
        logging.exception("Could not save daily news journal")

NEWS_DELIVERY_DB = os.path.join(
    "/data" if os.path.isdir("/data") and os.access("/data", os.W_OK) else BASE_DIR,
    "news_delivery.sqlite3",
)

def canonical_news_url(url):
    """Normalize tracking variants so the same story cannot be delivered twice."""
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    tracking = {"fbclid", "gclid", "mc_cid", "mc_eid"}
    query = [
        (key, value) for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in tracking
    ]
    return urllib.parse.urlunsplit((
        parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"),
        urllib.parse.urlencode(sorted(query)), ""
    ))

NEWS_EVENT_LOOKBACK_SECONDS = 7 * 24 * 60 * 60
NEWS_EVENT_SIMILARITY_THRESHOLD = 0.42

def find_recent_news_event_duplicate(article):
    """Compare the story with successfully published events from the last seven days."""
    tokens = set(news_event_tokens(article))
    if len(tokens) < 3:
        return None
    cutoff = int(time.time()) - NEWS_EVENT_LOOKBACK_SECONDS
    try:
        with sqlite3.connect(NEWS_DELIVERY_DB, timeout=15) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS news_event_history (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    published_at INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    tokens_json TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_news_event_history_published ON news_event_history(published_at)")
            rows = db.execute(
                "SELECT source, title, tokens_json FROM news_event_history WHERE published_at >= ? ORDER BY published_at DESC LIMIT 500",
                (cutoff,),
            ).fetchall()
        for source, title, raw_tokens in rows:
            try:
                prior = set(json.loads(raw_tokens))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            shared = tokens & prior
            if len(shared) < 3:
                continue
            union = tokens | prior
            jaccard = len(shared) / max(1, len(union))
            containment = len(shared) / max(1, min(len(tokens), len(prior)))
            # Avoid treating same-topic but different stories as duplicates:
            # require a strong overlap, or very high containment with at least four shared terms.
            if jaccard >= NEWS_EVENT_SIMILARITY_THRESHOLD or (len(shared) >= 4 and containment >= 0.78):
                return {"source": source, "title": title, "shared": sorted(shared)[:12]}
        return None
    except sqlite3.Error:
        logging.exception("NEWS_EVENT_HISTORY_READ_FAILED; stopping this cycle to avoid repeats")
        raise RuntimeError("News event history unavailable; fail-closed") from None


def seed_recent_events_from_daily_journal(journal):
    """Carry today's already-published stories into the new seven-day event index."""
    now = int(time.time())
    cutoff = now - NEWS_EVENT_LOOKBACK_SECONDS
    inserted = 0
    try:
        with sqlite3.connect(NEWS_DELIVERY_DB, timeout=15) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS news_event_history (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    published_at INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    tokens_json TEXT NOT NULL
                )
            """)
            for item in journal.get("published", []):
                try:
                    published_at = int(item.get("at", 0))
                    source = str(item.get("source", ""))[:80]
                    title = str(item.get("title", ""))[:300]
                    url = canonical_news_url(item.get("url", ""))
                    tokens = item.get("tokens") or news_event_tokens({"title": title})
                    if not url or not title or published_at < cutoff or not tokens:
                        continue
                    cursor = db.execute(
                        """INSERT INTO news_event_history(published_at, source, title, canonical_url, tokens_json)
                           SELECT ?, ?, ?, ?, ?
                           WHERE NOT EXISTS (SELECT 1 FROM news_event_history WHERE canonical_url=?)""",
                        (published_at, source, title, url, json.dumps(tokens[:100]), url),
                    )
                    inserted += cursor.rowcount
                except (TypeError, ValueError):
                    continue
            db.execute("DELETE FROM news_event_history WHERE published_at < ?", (cutoff,))
        logging.info("NEWS_EVENT_HISTORY_SEEDED inserted=%s window_days=7", inserted)
    except sqlite3.Error:
        logging.exception("NEWS_EVENT_HISTORY_SEED_FAILED; existing event history retained")

def record_recent_news_event(article):
    """Persist a successful publication for cross-source, seven-day event deduplication."""
    tokens = news_event_tokens(article)
    if len(tokens) < 3:
        return
    now = int(time.time())
    cutoff = now - NEWS_EVENT_LOOKBACK_SECONDS
    try:
        with sqlite3.connect(NEWS_DELIVERY_DB, timeout=15) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS news_event_history (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    published_at INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    tokens_json TEXT NOT NULL
                )
            """)
            db.execute(
                "INSERT INTO news_event_history(published_at, source, title, canonical_url, tokens_json) VALUES (?, ?, ?, ?, ?)",
                (now, str(article.get("source", ""))[:80], str(article.get("title", ""))[:300],
                 canonical_news_url(article.get("link", "")), json.dumps(tokens[:100])),
            )
            db.execute("DELETE FROM news_event_history WHERE published_at < ?", (cutoff,))
    except sqlite3.Error:
        logging.exception("NEWS_EVENT_HISTORY_WRITE_FAILED after Telegram confirmation")

NEWS_DELIVERY_RETRY_LEASE_SECONDS = 30 * 60


def reserve_news_delivery(article):
    """Atomically claim a story; stale failed claims become retryable after a safety lease."""
    article_id = str(article.get("id", "")).strip()
    canonical_url = canonical_news_url(article.get("link", ""))
    if not article_id or not canonical_url:
        return False
    now = int(time.time())
    stale_before = now - NEWS_DELIVERY_RETRY_LEASE_SECONDS
    try:
        with sqlite3.connect(NEWS_DELIVERY_DB, timeout=15) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS news_delivery (
                    article_id TEXT PRIMARY KEY,
                    canonical_url TEXT NOT NULL UNIQUE,
                    claimed_at INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'claimed'
                )
            """)
            cursor = db.execute(
                "INSERT OR IGNORE INTO news_delivery(article_id, canonical_url, claimed_at, status) VALUES (?, ?, ?, 'claimed')",
                (article_id, canonical_url, now),
            )
            claimed = cursor.rowcount == 1
            if not claimed:
                # 'published' is final. An abandoned claim is eligible again only after
                # the lease expires, reducing duplicate risk after ambiguous Telegram timeouts.
                cursor = db.execute(
                    """UPDATE news_delivery
                       SET article_id=?, claimed_at=?, status='claimed'
                       WHERE canonical_url=? AND status!='published' AND claimed_at<=?""",
                    (article_id, now, canonical_url, stale_before),
                )
                claimed = cursor.rowcount == 1
        if not claimed:
            logging.info("NEWS_DELIVERY_HELD_OR_ALREADY_PUBLISHED title=%s url=%s",
                         str(article.get("title", ""))[:120], canonical_url[:220])
        else:
            logging.info("NEWS_DELIVERY_CLAIMED article_id=%s lease_minutes=%s",
                         article_id[:120], NEWS_DELIVERY_RETRY_LEASE_SECONDS // 60)
        return claimed
    except sqlite3.Error:
        # Fail closed: never risk duplicate public posts if idempotency storage is unavailable.
        logging.exception("NEWS_DELIVERY_RESERVATION_FAILED; publication skipped")
        return False


def mark_news_delivery_published(article):
    """Persist Telegram's positive acknowledgement so this story is never sent twice."""
    article_id = str(article.get("id", "")).strip()
    canonical_url = canonical_news_url(article.get("link", ""))
    try:
        with sqlite3.connect(NEWS_DELIVERY_DB, timeout=15) as db:
            cursor = db.execute(
                """UPDATE news_delivery SET status='published', claimed_at=?
                   WHERE article_id=? OR canonical_url=?""",
                (int(time.time()), article_id, canonical_url),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Telegram accepted news but delivery row could not be finalized")
        logging.info("NEWS_DELIVERY_CONFIRMED article_id=%s", article_id[:120])
        return True
    except Exception:
        logging.exception("NEWS_DELIVERY_FINALIZE_FAILED article_id=%s", article_id[:120])
        return False

def record_daily_news_publication(article, journal):
    article_id = str(article.get("id", ""))
    if article_id:
        published_ids = journal.setdefault("published_ids", [])
        if article_id not in published_ids:
            published_ids.append(article_id)
            journal["published_ids"] = published_ids[-500:]
    journal["published"].append({"at": int(time.time()), "source": str(article.get("source", ""))[:80], "title": str(article.get("title", ""))[:240], "url": str(article.get("link", ""))[:1000], "tokens": news_event_tokens(article)[:80]})
    source = str(article.get("source", "Unknown"))[:80]
    counts = journal.setdefault("published_by_source", {})
    counts[source] = int(counts.get(source, 0)) + 1
    save_daily_news_journal(journal)

def log_daily_news_summary(journal):
    logging.info("NEWS_DAILY_SUMMARY date=%s published=%s duplicates_blocked=%s by_source=%s duplicates_by_source=%s", journal.get("date"), len(journal.get("published", [])), journal.get("duplicates_blocked", 0), json.dumps(journal.get("published_by_source", {}), ensure_ascii=False), json.dumps(journal.get("duplicates_by_source", {}), ensure_ascii=False))

class ArticleTextParser(HTMLParser):
    """Extract readable text from article pages without another dependency."""
    SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0
        self.image_url = ""
        self.published_date = ""

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
        if tag == "meta":
            meta_key = str(attributes.get("property") or attributes.get("name") or "").lower()
            meta_content = str(attributes.get("content", "")).strip()
            if not self.image_url and meta_key in {"og:image", "og:image:secure_url", "twitter:image"}:
                self.image_url = meta_content
            if not self.published_date and meta_key in {"article:published_time", "datepublished", "date"}:
                self.published_date = meta_content
        if tag == "img" and not self.image_url and self.skip_depth == 0:
            self.image_url = str(attributes.get("src") or attributes.get("data-src") or "").strip()

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        if not self.skip_depth and data.strip():
            self.parts.append(data.strip())


def get_news(seen):
    articles = []
    now = time.time()
    for source, url in FEEDS.items():
        fetched = 0
        stale = undated = irrelevant = missing = 0
        newest_age_seconds = None
        try:
            feed = feedparser.parse(url)
            fetched = len(feed.entries)
            if not fetched:
                for fallback_url in FEED_FALLBACKS.get(source, ()):
                    fallback_feed = feedparser.parse(fallback_url)
                    if fallback_feed.entries:
                        feed = fallback_feed
                        fetched = len(feed.entries)
                        logging.info("RSS_FEED_FALLBACK_USED source=%s url=%s entries=%s", source, fallback_url, fetched)
                        break
            if not fetched:
                logging.warning("RSS_FEED_EMPTY source=%s primary_url=%s", source, url)
            if getattr(feed, "bozo", False):
                bozo_error = getattr(feed, "bozo_exception", None)
                error_type = type(bozo_error).__name__ if bozo_error is not None else "unknown"
                logging.info("RSS_PARSER_NOTICE source=%s exception=%s entries=%s", source, error_type, fetched) if fetched else logging.warning("RSS_PARSER_FAILED source=%s exception=%s", source, error_type)
            for entry in feed.entries:
                link = str(entry.get("link", "")).strip()
                title = str(entry.get("title", "")).strip()
                if not link or not title:
                    missing += 1
                    continue
                published_struct = entry.get("published_parsed") or entry.get("updated_parsed")
                if not published_struct:
                    undated += 1
                    continue
                published_ts = calendar.timegm(published_struct)
                age_seconds = now - published_ts
                if newest_age_seconds is None or age_seconds < newest_age_seconds:
                    newest_age_seconds = age_seconds
                if age_seconds < -600 or age_seconds > NEWS_MAX_AGE_HOURS * 3600:
                    stale += 1
                    continue
                article_id = hashlib.sha256(canonical_news_url(link).encode("utf-8")).hexdigest()
                if article_id in seen:
                    continue

                media_url = ""
                for media in entry.get("media_content", []):
                    candidate = media.get("url", "")
                    if candidate and (media.get("medium") == "image" or str(media.get("type", "")).startswith("image/")):
                        media_url = candidate
                        break
                if not media_url and entry.get("media_thumbnail"):
                    media_url = entry.media_thumbnail[0].get("url", "")
                if not media_url:
                    for content_item in entry.get("content", []):
                        image_parser = ArticleTextParser()
                        image_parser.feed(str(content_item.get("value", "")))
                        if image_parser.image_url:
                            media_url = urllib.parse.urljoin(link, image_parser.image_url)
                            break
                if not media_url:
                    for enclosure in entry.get("enclosures", []):
                        if str(enclosure.get("type", "")).startswith("image/"):
                            media_url = enclosure.get("href", "") or enclosure.get("url", "")
                            if media_url:
                                break

                raw_summary = str(entry.get("summary", entry.get("description", ""))).strip()
                if not media_url:
                    image_parser = ArticleTextParser()
                    image_parser.feed(raw_summary)
                    if image_parser.image_url:
                        media_url = urllib.parse.urljoin(link, image_parser.image_url)
                summary = html.unescape(" ".join(re.sub(r"<[^>]+>", " ", raw_summary).split()))[:2500]
                title_text = f" {title.lower()} "
                summary_text = f" {summary.lower()} "
                crypto_title_hit = any(term in title_text for term in NEWS_RELEVANCE_TERMS["crypto"])
                crypto_summary_hits = {term for term in NEWS_RELEVANCE_TERMS["crypto"] if term in summary_text}
                crypto_hit = crypto_title_hit or len(crypto_summary_hits) >= 2
                tech_title_hit = any(term in title_text for term in NEWS_RELEVANCE_TERMS["technology"])
                tech_summary_hits = {term for term in NEWS_RELEVANCE_TERMS["technology"] if term in summary_text}
                tech_hit = tech_title_hit or len(tech_summary_hits) >= 2
                tech_source = source in {"MIT Technology Review", "IEEE Spectrum"}
                if not (crypto_hit or (tech_source and tech_hit)):
                    irrelevant += 1
                    continue

                # Headline relevance determines ranking; RSS summaries only confirm topic fit.
                # This prevents incidental mentions buried in summaries from inflating priority.
                priority = max(
                    (score for score, terms in NEWS_PRIORITY_TERMS.items()
                     if any(term in title_text for term in terms)),
                    default=0,
                )
                if tech_source and tech_hit:
                    priority = max(priority, 65)
                if source in {"CoinDesk", "The Block", "Decrypt"} and crypto_hit:
                    priority = max(priority, 65)
                # Reward genuinely new reporting without allowing source volume to dominate relevance.
                age_hours = max(0, age_seconds / 3600)
                freshness_bonus = max(0, 12 - int(age_hours * 4))
                source_bonus = 2 if source in {"CoinDesk", "The Block", "Decrypt"} and crypto_hit else 0
                score = priority + freshness_bonus + source_bonus
                articles.append({
                    "priority": score,
                    "base_priority": priority,
                    "id": article_id,
                    "source": source,
                    "title": title,
                    "link": link,
                    "published": str(entry.get("published", entry.get("updated", ""))),
                    "published_at": published_ts,
                    "summary": summary,
                    "media_url": media_url,
                })
            logging.info(
                "RSS_SOURCE_CHECK source=%s fetched=%s eligible=%s stale=%s undated=%s irrelevant=%s malformed=%s newest_age_min=%s",
                source, fetched, sum(1 for a in articles if a["source"] == source),
                stale, undated, irrelevant, missing,
                round(newest_age_seconds / 60, 1) if newest_age_seconds is not None else "n/a"
            )
        except Exception:
            logging.exception("RSS_SOURCE_ERROR source=%s", source)
    logging.info(
        "NEWS_SELECTION_POOL sources=%s eligible=%s freshness_hours=%s",
        len(FEEDS), len(articles), NEWS_MAX_AGE_HOURS
    )
    return articles

def fetch_article_text(article):
    """Read the original page where possible; use RSS summary if blocked."""
    request = urllib.request.Request(
        article["link"],
        headers={"User-Agent": "Mozilla/5.0 (compatible; ZombieCashNewsBot/1.0)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = response.read(1_500_000).decode("utf-8", errors="replace")
        parser = ArticleTextParser()
        parser.feed(raw)
        if not article.get("media_url") and parser.image_url:
            article["media_url"] = urllib.parse.urljoin(article["link"], parser.image_url)
        if not article.get("published") and parser.published_date:
            article["published"] = parser.published_date
        text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
        if len(text) > 500:
            return text[:9000]
    except Exception as exc:
        logging.info("Full article unavailable for %s (%s); using RSS metadata", article["source"], type(exc).__name__)
    return article.get("summary", "")[:4000]


def _parse_generated_news_json(raw):
    """Parse model JSON even when the provider wraps it in prose or Markdown fences."""
    text = str(raw or "").strip()
    if not text:
        raise ValueError("Gemini returned an empty response body")
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text, flags=re.IGNORECASE)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Gemini response did not contain a JSON object")
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("Gemini response contained malformed JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Gemini response JSON was not an object")
    return data


def _generate_pollinations_news(prompt):
    """Generate validated news copy through Pollinations when Gemini quota is exhausted."""
    if not POLLINATIONS_API_KEY:
        raise RuntimeError("POLLINATIONS_API_KEY is not configured for news fallback")
    payload = {
        "model": os.getenv("POLLINATIONS_TEXT_MODEL", "openai/gpt-5.4-nano").strip(),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_tokens": 900,
    }
    request = urllib.request.Request(
        "https://gen.pollinations.ai/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {POLLINATIONS_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ZombieCashNewsBot/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        result = json.loads(response.read(2 * 1024 * 1024).decode("utf-8", errors="replace"))
    choices = result.get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    raw = message.get("content")
    if isinstance(raw, list):
        raw = "".join(part.get("text", "") for part in raw if isinstance(part, dict))
    data = _parse_generated_news_json(raw)
    logging.info("AI provider selected for news: Pollinations model=%s", payload["model"])
    return data

def prepare_post(article, article_text):
    prompt = f"""
You write ZombieCash News for an English-speaking meme community.
Create a factual short summary and a separate, original, sharply funny dark-satirical comment.

SOURCE: {article['source']}
HEADLINE: {article['title']}
ORIGINAL URL: {article['link']}
AVAILABLE ARTICLE TEXT (may be incomplete):
{article_text[:9000]}

Return valid JSON only, with exactly two string keys: "summary" and "commentary".
- summary: 1-3 concise factual sentences grounded in the supplied material. Never invent details.
- commentary: 2-3 sentences of biting, clever dark humour about human paranoia, conspiracy theories, AI panic, machine uprisings, secret-lab rumours, epidemics, societal collapse, bureaucracy or apocalypse culture. Choose an angle relevant to this specific story and end with a punchline.
- Satirize panic, credulity, absurd systems and human behaviour—not victims of real tragedies.
- Never present an unverified conspiracy, outbreak, cover-up or malicious intent as fact.
- For real-world attacks, serious injuries or deaths, never joke about the victim, their injuries, or the attack itself. If the story is a tragedy, keep the comment restrained and aim satire only at unrelated bureaucracy, media sensationalism or public overreaction; if no safe angle fits, use a brief dry meta-comment instead.
- Commentary must add a fresh joke, not repeat the summary.
- English only. No investment advice, price predictions, hashtags or invented quotes.
- Keep summary under 500 characters and commentary under 450 characters.
"""
    last_error = None
    data = None
    for model_name in GEMINI_MODELS:
        for attempt in range(2):
            try:
                response = client.models.generate_content(model=model_name, contents=prompt)
                data = _parse_generated_news_json(response.text)
                break
            except Exception as exc:
                last_error = exc
                error_text = str(exc).upper()
                status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
                quota_limited = status_code == 429 or "RESOURCE_EXHAUSTED" in error_text or "429" in error_text
                logging.warning(
                    "Gemini news model %s failed (attempt %s/2, error=%s)",
                    model_name, attempt + 1, type(exc).__name__
                )
                if quota_limited:
                    logging.info("Gemini quota reached for %s; trying the reserve model", model_name)
                    break
        if data is not None:
            if model_name != GEMINI_MODEL:
                logging.info("Gemini news reserve model succeeded: %s", model_name)
            break
    if data is None:
        try:
            data = _generate_pollinations_news(prompt)
        except Exception as fallback_error:
            logging.warning("Pollinations news fallback failed (%s); deferring item", type(fallback_error).__name__)
            raise ValueError("All configured Gemini news models and Pollinations fallback failed") from fallback_error

    summary = str(data.get("summary", "")).strip()
    commentary = str(data.get("commentary", "")).strip()
    if not summary or not commentary:
        raise ValueError("Gemini returned an empty summary or Zombie Commentary")
    post = (
        f"📰 NEWS UPDATE\n{article['title']}\n\n{summary}\n\n"
        f"🔗 ORIGINAL SOURCE: {article['link']}\n\n"
        f"🧟 ZOMBIE COMMENTARY\n{commentary}"
    )
    return post, summary, commentary


def publish_to_website(article, summary, commentary, image_bytes, telegram_message_id):
    """Send the same Telegram publication to the website API without blocking the worker."""
    if not NEWS_PUBLISH_TOKEN:
        logging.warning("NEWS_PUBLISH_TOKEN is missing; enriched news will stay Telegram-only")
        return
    if not NEWS_PUBLISH_API_URL:
        logging.warning("NEWS_PUBLISH_API_URL is not configured for this staging environment; website sync skipped")
        return
    payload = {
        "source": article.get("source", "ZombieCash News"),
        "title": article["title"],
        "link": article["link"],
        "published": article.get("published") or "",
        "summary": summary,
        "commentary": commentary,
        "telegramUrl": f"https://t.me/ZOMBCASH/{telegram_message_id}" if telegram_message_id else "",
    }
    # Keep the API request under its body-size limit; commentary still publishes if the image is too large.
    if image_bytes and len(image_bytes) <= 4 * 1024 * 1024:
        payload["imageBase64"] = base64.b64encode(image_bytes).decode("ascii")
    request = urllib.request.Request(
        NEWS_PUBLISH_API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {NEWS_PUBLISH_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "ZombieCashNewsBot/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read(100_000).decode("utf-8", errors="replace"))
        if result.get("ok"):
            logging.info("Published enriched news to website API: %s", article["title"])
        else:
            logging.warning("Website API did not confirm enriched news publication: %s", str(result)[:300])
    except Exception as exc:
        # Telegram has already published successfully; never cause a duplicate Telegram post on API failure.
        logging.exception("Could not sync published news to website API: %s", type(exc).__name__)


VISUAL_HISTORY_FILE = os.path.join("/data" if os.path.isdir("/data") and os.access("/data", os.W_OK) else BASE_DIR, "recent_visual_styles.json")
VISUAL_HISTORY_LIMIT = 12
def download_article_image(article):
    """Use the publisher's own image when available; never spend AI credits for it."""
    url = str(article.get("media_url", "") or "").strip()
    if not url.startswith(("https://", "http://")):
        return None
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ZombieCashNewsBot/1.0)"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            content_type = str(response.headers.get("Content-Type", "")).lower()
            image_bytes = response.read(8 * 1024 * 1024 + 1)
        if len(image_bytes) > 8 * 1024 * 1024 or not content_type.startswith("image/"):
            return None
        if not (image_bytes.startswith(b"\x89PNG\r\n\x1a\n") or image_bytes.startswith(b"\xff\xd8\xff") or (image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP") or image_bytes.startswith(b"GIF8")):
            return None
        return image_bytes
    except Exception as exc:
        logging.info("Source image unavailable for %s (%s)", str(article.get("title", ""))[:80], type(exc).__name__)
        return None


def load_visual_history():
    try:
        with open(VISUAL_HISTORY_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except (OSError, ValueError, json.JSONDecodeError):
        return []


def save_visual_history(history):
    try:
        os.makedirs(os.path.dirname(VISUAL_HISTORY_FILE), exist_ok=True)
        temp = VISUAL_HISTORY_FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(history[-VISUAL_HISTORY_LIMIT:], handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, VISUAL_HISTORY_FILE)
    except OSError:
        logging.exception("Could not save visual style history")


def choose_visual_direction(article, commentary):
    """Choose a story-led art direction with category-specific motifs and restrained variation."""
    title = str(article.get("title", ""))
    context = str(article.get("summary", ""))
    # Classify the actual article, not Zombie commentary (which often mentions crypto regardless of the news topic).
    story = " ".join([title, context]).lower()
    digest = hashlib.sha256(str(article.get("id", article.get("link", ""))).encode("utf-8")).hexdigest()
    history = load_visual_history()
    recent_genres = {str(row.get("genre", "")) for row in history[-5:] if isinstance(row, dict)}
    recent_compositions = {str(row.get("composition", "")) for row in history[-4:] if isinstance(row, dict)}
    recent_palettes = {str(row.get("palette", "")) for row in history[-3:] if isinstance(row, dict)}

    def pick_fresh(options, recent, salt):
        options = list(options)
        available = [item for item in options if item not in recent] or options
        number = int(hashlib.sha256((digest + "|" + salt).encode("utf-8")).hexdigest()[:8], 16)
        return available[number % len(available)]

    # More specific categories must win over broad security/technology keywords.
    serious = any(word in story for word in (
        "killed", "death", "dead", "injur", "shoot", "stabbing", "bomb", "warfare",
        "victim", "hospital", "missing person", "crash", "fatal", "earthquake",
        "flood", "fire kills", "rape", "hostage"
    ))
    security = any(word in story for word in (
        "hack", "hacked", "hacker", "breach", "exploit", "vulnerability", "zero-day",
        "ransomware", "phishing", "wallet drained", "stolen funds", "data leak",
        "cyberattack", "cyber attack", "security flaw", "scam", "fraud"
    ))
    meme_coin = any(word in story for word in (
        "meme coin", "memecoin", "dogecoin", "shiba inu", "pump.fun", "pumpfun", "degen"
    ))
    bitcoin = "bitcoin" in story or re.search(r"\bbtc\b", story) is not None
    crypto = any(word in story for word in (
        "crypto", "token", "market", "trading", "price", "investor", "funding",
        "finance", "bank", "etf", "coinbase", "stablecoin", "ethereum", "solana",
        "exchange", "liquidity"
    ))
    tech = any(word in story for word in (
        "artificial intelligence", " ai ", "robot", "technology", "software",
        "cyber", "data", "startup", "internet", "computer", "algorithm", "space", "satellite",
        "chip", "semiconductor", "cloud", "app", "model release"
    ))
    politics = any(word in story for word in (
        "politic", "president", "minister", "government", "election", "parliament",
        "court", "official", "law", "policy", "diplomat", "regulation"
    ))
    nature = any(word in story for word in (
        "animal", "shark", "whale", "wildlife", "forest", "climate", "ocean",
        "nature", "rescue", "planet", "environment"
    ))

    if serious:
        category, genres = "serious", [
            "restrained photojournalistic editorial illustration, natural light and documentary framing",
            "serious graphic-novel reportage, subdued linework and carefully observed details",
            "hand-painted magazine illustration with realistic environments and humane perspective",
            "minimal symbolic editorial art, strong negative space and muted tones",
        ]
        palettes = ["muted natural colours with a small amber accent", "cool slate and steel blue", "warm documentary tones", "soft charcoal and dusty gold"]
        motif = "Show the real-world setting or a respectful symbolic detail; no graphic injury, sensationalism, or victim-focused humour."
    elif security:
        category, genres = "security", [
            "premium cybersecurity magazine cover illustration, precise 3D materials and cinematic rim light",
            "forensic editorial still life with physical evidence and restrained noir lighting",
            "clean technical cutaway illustration of a compromised digital system",
            "architectural metaphor for a breached perimeter, rendered as sophisticated concept art",
            "minimal investigative poster with one unmistakable security symbol and generous negative space",
        ]
        palettes = ["midnight navy with forensic cyan", "graphite and signal amber", "cold violet and brushed silver", "bone white, black and restrained vermilion", "deep teal and ice blue"]
        motif = "Represent the actual exploit or breach: a cracked access-control layer, exposed data conduit, compromised wallet, or vulnerable system component. No random masked hacker, zombie horde, coins, or rockets."
    elif meme_coin:
        category, genres = "memecoin", [
            "high-end 3D internet-culture editorial art with one expressive mascot-like focal subject",
            "bold screen-printed meme-culture magazine illustration with disciplined shapes",
            "surreal collectible-character editorial scene, premium materials and cinematic lighting",
            "playful but polished underground-comic cover with crisp silhouettes",
            "miniature speculative-market diorama photographed like a luxury product editorial",
        ]
        palettes = ["toxic lime with deep ink and warm cream", "acid yellow and cobalt", "hot coral and midnight purple", "mint green and charcoal with a single orange accent", "silver and ultraviolet"]
        motif = "Use one memorable fictional character or visual gag tied to the specific token/community story. Keep silhouettes clean and expressive; avoid coin piles, rockets, random crowds, logos, and visual clutter."
    elif bitcoin:
        category, genres = "bitcoin", [
            "institutional-grade crypto-finance magazine cover with photoreal materials",
            "minimalist editorial sculpture illustrating decentralised value, no readable symbols",
            "cinematic macro still life of a secure digital vault and warm metallic light",
            "geometric financial editorial illustration with precise architecture and a clear focal point",
            "noir market-structure concept art, sophisticated shadows and tactile surfaces",
        ]
        palettes = ["burnished copper and midnight blue", "black titanium with warm amber", "ice blue and graphite", "cream, deep navy and restrained orange", "emerald-black with soft gold"]
        motif = "Show Bitcoin's role in the specific headline through secure custody, scarcity, institutional flows, market tension, or network infrastructure. Avoid generic coin close-ups unless the article is directly about custody or coin design."
    elif crypto:
        category, genres = "crypto market structure", [
            "modern financial-news editorial illustration with a precise symbolic metaphor",
            "cinematic trading-floor architecture without legible screens or numbers",
            "luxury editorial still life with tactile market-related objects",
            "isometric miniature financial ecosystem with clean geometry",
            "surreal but disciplined paper collage about liquidity and risk",
        ]
        palettes = ["electric cyan and deep navy", "warm orange, crimson and charcoal", "violet and silver", "cream paper, ink black and vivid red", "cobalt and bright yellow", "emerald and warm ivory", "rust orange and petrol blue"]
        motif = "Build one story-specific metaphor—liquidity flowing through a narrow channel, a fragile market bridge, a regulatory maze, or a payment network. No default rockets, random charts, token piles, or hooded figures."
    elif tech:
        category, genres = "technology", [
            "premium technology-journal editorial concept art with realistic engineered materials",
            "technical cutaway illustration with precise layers and a clear focal component",
            "retro-futurist magazine art with disciplined geometry and tactile print texture",
            "photorealistic product-scale still life with one surreal technical detail",
            "isometric architecture illustrating the system described in the article",
            "graphic-novel reportage with dynamic but uncluttered framing",
        ]
        palettes = ["cold cyan and graphite", "magenta and midnight blue", "sunset orange with petrol blue", "silver and violet", "coral and navy", "warm cream and ultramarine"]
        motif = "Make the specific device, system, scientific result, or consequence the central subject. Prefer a believable scene or diagram-like metaphor over character crowds."
    elif politics:
        category, genres = "politics", [
            "sharp editorial illustration with symbolic, non-identifiable figures",
            "political satire staged like a theatre set with symbolic props",
            "ink-and-watercolour magazine illustration with dry visual irony",
            "minimal editorial poster with one powerful symbol",
            "surreal paper collage with bureaucratic forms and impossible architecture",
        ]
        palettes = ["paper cream, ink black and vermilion", "navy, gold and muted red", "desaturated blue-grey with a bright accent", "ochre and dark teal", "black, white and acid yellow"]
        motif = "Use symbolic settings, documents, or architecture; avoid likenesses of real politicians and avoid generic zombie imagery."
    elif nature:
        category, genres = "nature", [
            "high-detail wildlife editorial illustration with naturalistic anatomy",
            "environmental magazine cover art grounded in a believable habitat",
            "botanical editorial illustration with fine textures and strong focal hierarchy",
            "cinematic landscape concept art with a small human-scale focal point",
        ]
        palettes = ["ocean teal and sandy gold", "lush natural colours with coral accents", "storm blue and sea-glass green", "terracotta and leaf green"]
        motif = "Make the actual animal, habitat, landscape, or environmental effect the focal point."
    else:
        category, genres = "general", [
            "realistic editorial feature illustration like a striking magazine cover",
            "expressive caricature with one clear visual punchline",
            "eerie cinematic horror illustration with atmospheric shadows and no gore",
            "hand-painted editorial illustration with visible brushwork",
            "photorealistic scene with one absurd darkly comic element",
            "bold vector editorial art with simple graphic shapes",
            "retro magazine illustration with screen-print texture",
            "miniature diorama photographed with macro lens and practical lighting",
        ]
        palettes = ["burnt orange and deep blue", "lavender, black and cream", "copper and midnight teal", "muted cinematic colours with vivid red", "warm yellow and cobalt", "forest green and pale pink", "coral and ink black", "monochrome with one electric accent"]
        motif = "Illustrate the specific event or a clear visual metaphor from the headline, not a generic zombie scene."

    genre = pick_fresh(genres, recent_genres, "genre")
    palette = pick_fresh(palettes, recent_palettes, "palette")
    compositions = [
        "extreme close-up of one story-relevant object, strong texture, shallow depth of field",
        "wide environmental shot with a small focal subject and cinematic negative space",
        "overhead forensic or still-life arrangement with deliberate geometry",
        "low-angle architectural view with dramatic but controlled perspective",
        "off-centre hero subject, asymmetric balance and clean empty space",
        "diagonal cutaway through foreground, midground and background layers",
        "macro material detail that hints at the larger story",
        "side-lit silhouette against a contrasting architectural backdrop",
        "three-quarter isometric miniature scene with crisp readable shapes",
        "diptych-like visual contrast in a single frame, without borders or text",
    ]
    composition = pick_fresh(compositions, recent_compositions, "composition")
    # Return the style record; only successful renders should be written to history.
    record = {"genre": genre, "palette": palette, "composition": composition, "category": category}
    logging.info("VISUAL_DIRECTION category=%s article=%s genre=%s palette=%s composition=%s",
                 category, title[:100], genre, palette, composition)
    variation_note = (
        f"STORY-SPECIFIC VISUAL METAPHOR: {motif} "
        f"COMPOSITION: {composition}. "
        "Prioritize strong art direction, one dominant subject, controlled detail, realistic anatomy and materials, "
        "intentional lighting, editorial restraint, and visual clarity at mobile size. "
        "No generic stock-photo layout and no repeated character crowd."
    )
    return genre, palette, variation_note, record


def _decode_generated_image(result):
    """Validate the generation service response before passing bytes to Telegram."""
    image_data = result.get("data")
    if not isinstance(image_data, list) or not image_data or not isinstance(image_data[0], dict):
        raise ValueError("Image service returned an unexpected JSON response")
    encoded = image_data[0].get("b64_json")
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("Image service response did not contain b64_json image data")
    image_bytes = base64.b64decode(encoded, validate=True)
    if not image_bytes or len(image_bytes) > 12 * 1024 * 1024:
        raise ValueError("Generated illustration was empty or exceeded 12 MB")
    if not (
        image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        or image_bytes.startswith(b"\xff\xd8\xff")
        or image_bytes.startswith(b"RIFF")
    ):
        raise ValueError("Image service did not return recognizable image data")
    return image_bytes


def generate_illustration(article, commentary):
    """Generate a story-specific illustration, retry transient failures, and retain style history only on success."""
    if not POLLINATIONS_API_KEY:
        raise RuntimeError("POLLINATIONS_API_KEY is not configured")

    genre, palette, variation_note, style_record = choose_visual_direction(article, commentary)
    title = str(article.get("title", ""))
    scene_prompt = (
        "Create one premium 16:9 editorial illustration for a real news article. Accuracy to the headline is more important than style. "
        f"SUBJECT (must dominate the image): {title[:220]}. "
        f"FACTUAL CONTEXT: {str(article.get('summary', ''))[:650]}. "
        "Before drawing, identify the single concrete subject and event in the headline. Show that subject literally or with one unmistakable visual metaphor. "
        "For a bank-security warning, show a banking login/password credential being protected from an AI assistant; for a company earnings story, show the named company's real business activity; "
        "for a policy story, show the specific policy mechanism; for a market story, show the specific asset or market mechanism. These are examples, not extra objects to combine. "
        "Do not default to zombies, skulls, crypto coins, rockets, charts, crowds, generic hackers, fantasy buildings, or game-like dioramas. Include only objects directly supported by this article. "
        f"Art direction: {genre}. Palette: {palette}. {variation_note} "
        "One focal subject, simple background, believable scale and materials, clear cause-and-effect, clean professional composition. "
        "No text, labels, numbers, logos, watermarks, fake interfaces, gore, or identifiable real-person likenesses. "
        "Do not use the editorial commentary as a source of facts; it is tone only. "
        f"Headline: {title[:220]}. Context: {str(article.get('summary', ''))[:650]}."
    )
    seed = int(hashlib.sha256((str(article.get("id", "")) + "|" + genre + "|" + palette + "|" + commentary).encode("utf-8")).hexdigest()[:8], 16)
    payload = {
        "model": "flux",
        "prompt": scene_prompt,
        "size": "1024x576",
        "seed": seed,
        "response_format": "b64_json",
    }

    last_error = None
    for attempt in range(1, 3):
        # Change only the seed on retry to request a genuinely different render.
        attempt_payload = dict(payload)
        if attempt > 1:
            attempt_payload["seed"] = (seed + 104729) % (2**31 - 1)
            attempt_payload["prompt"] += (
                " RETRY ART DIRECTION: simplify the scene further; use one unmistakable subject, "
                "less visual noise, more negative space, and a clearly different camera angle."
            )
        request = urllib.request.Request(
            "https://gen.pollinations.ai/v1/images/generations",
            data=json.dumps(attempt_payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {POLLINATIONS_API_KEY}",
                "Content-Type": "application/json",
                "User-Agent": "ZombieCashNewsBot/1.0",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                result = json.loads(response.read(20 * 1024 * 1024).decode("utf-8", errors="replace"))
            image_bytes = _decode_generated_image(result)
            history = load_visual_history()
            history.append(style_record)
            save_visual_history(history)
            logging.info("IMAGE_GENERATED article=%s attempt=%s bytes=%s", title[:100], attempt, len(image_bytes))
            return image_bytes
        except Exception as exc:
            last_error = exc
            logging.warning("Image generation attempt %s/2 failed for %s: %s: %s",
                            attempt, title[:80], type(exc).__name__, str(exc)[:180])
            if attempt == 1:
                import time
                time.sleep(2)

    raise RuntimeError(f"Image generation failed after 2 attempts: {type(last_error).__name__}") from last_error


async def run_worker():
    logging.info("Zombie News worker starting; channel=%s", CHANNEL_ID)
    daily_journal = load_daily_news_journal()
    seed_recent_events_from_daily_journal(daily_journal)
    seen = set(daily_journal.get("published_ids", [])) | set(daily_journal.get("skipped_no_image_ids", []))
    log_daily_news_summary(daily_journal)
    if not seen:
        logging.info("No previous news history found. Current RSS feed will be checked.")
    bot = Bot(
        token=TELEGRAM_TOKEN,
        request=HTTPXRequest(read_timeout=100, write_timeout=100, connect_timeout=20, pool_timeout=20),
    )

    async with bot:
        while True:
            try:
                daily_journal = refresh_daily_news_journal(daily_journal)
                seen = set(daily_journal.get("published_ids", [])) | set(daily_journal.get("skipped_no_image_ids", []))
                articles = await asyncio.to_thread(get_news, seen)
                fresh = [article for article in articles if article["id"] not in seen]
                eligible = []
                duplicate_count = 0
                for candidate in fresh:
                    match = find_recent_news_event_duplicate(candidate)
                    if match:
                        duplicate_count += 1
                        logging.info(
                            "NEWS_EVENT_DUPLICATE_BLOCKED source=%s title=%s matches=%s from=%s shared=%s",
                            candidate.get("source"), candidate.get("title", "")[:120],
                            match.get("title", "")[:120], match.get("source"),
                            ",".join(match.get("shared", []))
                        )
                    else:
                        eligible.append(candidate)
                logging.info("NEWS_EVENT_DEDUPE checked=%s blocked=%s window_days=7 eligible=%s",
                             len(fresh), duplicate_count, len(eligible))
                # Sort by relevance first, then freshness; source popularity never outranks story value.
                eligible.sort(key=lambda item: (item.get("priority", 0), item.get("published_at", 0)), reverse=True)
                if eligible:
                    logging.info("NEWS_TOP_CANDIDATES %s", json.dumps([
                        {"source": x["source"], "score": x["priority"], "title": x["title"][:110]}
                        for x in eligible[:5]
                    ], ensure_ascii=False))
                # Daily counts are statistics only; they never block eligible news.
                logging.info("NEWS_DAILY_COUNT informational_only=%s", len(daily_journal.get("published", [])))
                for article in eligible[:1]:
                    try:
                        article_text = await asyncio.to_thread(fetch_article_text, article)
                        post, summary, commentary = await asyncio.to_thread(prepare_post, article, article_text)
                        # Telegram photo captions are limited to 1024 characters.
                        # Keep the headline and source URL intact; shorten generated copy instead of
                        # discarding an otherwise publishable article and retrying it forever.
                        if len(post) > 980:
                            fixed = (
                                f"📰 NEWS UPDATE\n{article['title']}\n\n"
                                f"\n\n🔗 ORIGINAL SOURCE: {article['link']}\n\n"
                                f"🧟 ZOMBIE COMMENTARY\n"
                            )
                            budget = max(0, 980 - len(fixed))
                            # Prioritize the factual summary, then retain as much commentary as fits.
                            summary_budget = min(len(summary), max(180, int(budget * 0.58)))
                            summary = summary[:summary_budget].rsplit(" ", 1)[0].rstrip(" ,;:-") if len(summary) > summary_budget else summary
                            remaining = max(0, budget - len(summary))
                            commentary = commentary[:remaining].rsplit(" ", 1)[0].rstrip(" ,;:-") if len(commentary) > remaining else commentary
                            post = (
                                f"📰 NEWS UPDATE\n{article['title']}\n\n{summary}\n\n"
                                f"🔗 ORIGINAL SOURCE: {article['link']}\n\n"
                                f"🧟 ZOMBIE COMMENTARY\n{commentary}"
                            )
                        if len(post) > 980:
                            # Extremely long headlines/source URLs can consume the whole caption.
                            # Fall back to a compact caption while still preserving the original URL.
                            summary = summary[:180].rsplit(" ", 1)[0].rstrip(" ,;:-")
                            commentary = commentary[:180].rsplit(" ", 1)[0].rstrip(" ,;:-")
                            post = (
                                f"📰 NEWS UPDATE\n{article['title'][:180]}\n\n{summary}\n\n"
                                f"🔗 ORIGINAL SOURCE: {article['link']}\n\n"
                                f"🧟 ZOMBIE COMMENTARY\n{commentary}"
                            )
                        if len(post) > 1024:
                            raise ValueError("Caption exceeds Telegram limit even after compact formatting")
                        if article["link"] not in post:
                            raise ValueError("Original source URL missing from prepared post")

                        # News is photo-only: never publish a text fallback.
                        image_bytes = await asyncio.to_thread(download_article_image, article)
                        if not image_bytes:
                            skipped = daily_journal.setdefault("skipped_no_image_ids", [])
                            if article["id"] not in skipped:
                                skipped.append(article["id"])
                                daily_journal["skipped_no_image_ids"] = skipped[-500:]
                                save_daily_news_journal(daily_journal)
                            seen.add(article["id"])
                            logging.info("NEWS_SKIPPED_NO_IMAGE title=%s", article["title"][:120])
                            continue

                        # Atomically reserve the story before calling Telegram. The SQLite
                        # UNIQUE constraints also protect against parallel worker instances.
                        if not reserve_news_delivery(article):
                            # Leave it eligible: the delivery lease will permit a retry after expiry.
                            continue
                        attempted = daily_journal.setdefault("send_attempted_ids", [])
                        if article["id"] not in attempted:
                            attempted.append(article["id"])
                            daily_journal["send_attempted_ids"] = attempted[-500:]
                            save_daily_news_journal(daily_journal)
                        seen.add(article["id"])

                        # Do not fall back to send_message on any send_photo exception:
                        # Telegram may already have accepted the photo before a timeout.
                        sent_message = await bot.send_photo(
                            chat_id=CHANNEL_ID,
                            photo=image_bytes,
                            caption=post,
                            read_timeout=100,
                            write_timeout=100,
                            connect_timeout=20,
                        )
                        # Telegram returned a message object: finalize idempotency immediately,
                        # before website sync or any other operation that may fail.
                        delivery_finalized = await asyncio.to_thread(mark_news_delivery_published, article)
                        await asyncio.to_thread(record_recent_news_event, article)
                        if not delivery_finalized:
                            logging.critical(
                                "NEWS_TELEGRAM_SENT_BUT_DELIVERY_FINALIZE_FAILED article_id=%s; daily journal will protect this worker",
                                article.get("id", "")[:120],
                            )
                        await asyncio.to_thread(
                            publish_to_website, article, summary, commentary, image_bytes,
                            getattr(sent_message, "message_id", None)
                        )
                        record_daily_news_publication(article, daily_journal)
                        seen.add(article["id"])
                        log_daily_news_summary(daily_journal)
                        logging.info("Published news + Zombie Commentary; source_image=%s: %s", bool(image_bytes), article["title"])
                        await asyncio.sleep(2)
                    except ValueError as exc:
                        logging.warning("News item deferred until next cycle (generation/validation): %s: %s",
                                        article.get("title", "unknown"), str(exc)[:220])
                    except Exception:
                        logging.exception("News item not published (will retry on a later cycle): %s", article.get("title", "unknown"))

                # Poll every 10 minutes; publish at most one eligible article per cycle.
                await asyncio.sleep(600)
            except Exception:
                logging.exception("RSS worker cycle failed")
                await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(run_worker())
