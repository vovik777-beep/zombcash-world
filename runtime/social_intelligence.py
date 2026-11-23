"""Low-cost public social and developer signals for Meme Radar.

No paid X API is used. Signals are based on links exposed by DexScreener,
public GitHub metadata when a repository is linked, and Google News RSS.
Missing data stays unknown; it is never treated as proof of inactivity.
"""
import re
import time
import logging
from urllib.parse import quote, urlparse

import requests

log = logging.getLogger(__name__)
HEADERS = {"User-Agent": "ZombieCashMemeRadar/6.1 (+public market research)"}
CACHE_TTL = 1800
_cache = {}


def _host(url):
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _github_repo(url):
    parts = [p for p in urlparse(url).path.split("/") if p]
    if _host(url) not in {"github.com"} or len(parts) < 2:
        return None
    if parts[0] in {"orgs", "topics", "features", "settings", "marketplace"}:
        return None
    return parts[0], parts[1].removesuffix(".git")


def _github_activity(socials):
    for url in socials:
        repo = _github_repo(url)
        if not repo:
            continue
        owner, name = repo
        api = f"https://api.github.com/repos/{owner}/{name}/commits"
        try:
            response = requests.get(api, headers=HEADERS, params={"per_page": 5}, timeout=5)
            if response.status_code == 404:
                return {"status": "repository not found/publicly unavailable", "recent_commits_5": None, "source": url}
            response.raise_for_status()
            data = response.json()
            return {
                "status": "public repository found",
                "recent_commits_5": len(data) if isinstance(data, list) else None,
                "latest_commit_date": (data[0].get("commit", {}).get("author", {}).get("date") if data else None),
                "source": url,
            }
        except Exception as exc:
            log.info("GitHub public activity lookup unavailable for %s: %s", url, str(exc)[:160])
            return {"status": "lookup unavailable", "recent_commits_5": None, "source": url}
    return {"status": "no public GitHub repository link found", "recent_commits_5": None, "source": None}


def _news_mentions(name, symbol, address):
    # RSS search avoids a paid search API; results are news/index mentions, not a full X firehose.
    query = f'("{name}" OR "{symbol}" OR "{address}") (crypto OR token OR Solana)'
    url = "https://news.google.com/rss/search?q=" + quote(query) + "&hl=en-US&gl=US&ceid=US:en"
    try:
        response = requests.get(url, headers=HEADERS, timeout=6)
        response.raise_for_status()
        xml = response.text[:180000]
        items = re.findall(r"<item>(.*?)</item>", xml, flags=re.S | re.I)
        titles = []
        for item in items[:8]:
            match = re.search(r"<title>(.*?)</title>", item, flags=re.S | re.I)
            if match:
                title = re.sub(r"<[^>]+>", "", match.group(1))
                title = title.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
                titles.append(title[:180])
        return {"count": len(titles), "titles": titles, "source": "Google News RSS", "status": "available"}
    except Exception as exc:
        log.info("News RSS lookup unavailable for %s: %s", symbol, str(exc)[:160])
        return {"count": None, "titles": [], "source": "Google News RSS", "status": "unavailable"}


def collect_social_signals(token):
    """Collect cacheable public social/developer/mention signals for a parsed DexScreener token."""
    address = str(token.get("address") or "")
    key = address.lower()
    now = time.time()
    cached = _cache.get(key)
    if cached and now - cached[0] < CACHE_TTL:
        return dict(cached[1])

    socials = token.get("socials") or []
    websites = token.get("websites") or []
    urls = []
    platform_names = []
    for item in socials:
        url = item.get("url") if isinstance(item, dict) else None
        platform = item.get("type") if isinstance(item, dict) else None
        if url and url not in urls:
            urls.append(url)
        if platform:
            platform_names.append(str(platform).lower())
    for item in websites:
        url = item.get("url") if isinstance(item, dict) else (item if isinstance(item, str) else None)
        if url and url not in urls:
            urls.append(url)

    hosts = sorted({_host(url) for url in urls if _host(url)})
    links = {
        "website": any(h not in {"x.com", "twitter.com", "t.me", "telegram.org", "discord.gg", "discord.com", "github.com"} for h in hosts),
        "x": any(h in {"x.com", "twitter.com"} for h in hosts) or "twitter" in platform_names or "x" in platform_names,
        "telegram": any(h in {"t.me", "telegram.org"} for h in hosts) or "telegram" in platform_names,
        "discord": any(h in {"discord.gg", "discord.com"} for h in hosts) or "discord" in platform_names,
        "github": any(h == "github.com" for h in hosts) or "github" in platform_names,
    }
    found = sum(bool(v) for v in links.values())
    social_score = round(found * 20)
    github = _github_activity(urls)
    mentions = _news_mentions(str(token.get("name") or ""), str(token.get("symbol") or ""), address)

    signals = {
        "social_links": links,
        "social_platforms_found": [k for k, v in links.items() if v],
        "social_presence_score_0_100": social_score,
        "social_urls": urls[:8],
        "github_activity": github,
        "web_news_mentions_approx": mentions["count"],
        "news_mention_titles": mentions["titles"][:5],
        "news_source": mentions["source"],
        "news_status": mentions["status"],
        "limitations": [
            "Social links show presence, not real community engagement or follower authenticity.",
            "Google News RSS is a partial news/index signal, not a complete X mention count.",
            "Developer activity is checked only when a public GitHub repository link is found.",
        ],
    }
    _cache[key] = (now, signals)
    return dict(signals)
