import os
import html
import json
import asyncio
import logging
import time
import math
import re
import requests
import sqlite3
from urllib.parse import urlparse

from google import genai
from social_intelligence import collect_social_signals
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

BOT_TOKEN = os.getenv("ZOMBCASH_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Comma-separated Telegram numeric user IDs allowed to remove community coins.
ADMIN_USER_IDS = {int(value.strip()) for value in os.getenv("ZOMBCASH_ADMIN_IDS", "").split(",") if value.strip().isdigit()}
GEMINI_MODELS = ["gemini-3.6-flash", "gemini-3.5-flash-lite"]

DEX_API = "https://api.dexscreener.com"
HEADERS = {"User-Agent": "ZombieCashMemeRadar/6.0"}
MAX_RESULTS = 5
AI_ANALYSIS_COUNT = 5
MAX_DISCOVERY_ADDRESSES = 100
MIN_LIQUIDITY_USD = 10_000
MIN_VOLUME_24H_USD = 25_000
MIN_TRADES_24H = 100

# Public Telegram channel and Meme Radar bot.
RADAR_CHANNEL = "@ZOMBCASH"
RADAR_BOT_URL = "https://t.me/ZombieCashMemeRadarBot"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("zombcash_radar")
logging.getLogger("httpx").setLevel(logging.WARNING)

LANGUAGES = {
    "en": "🇬🇧 English", "uk": "🇺🇦 Українська", "ru": "🇷🇺 Русский",
    "tr": "🇹🇷 Türkçe", "zh": "🇨🇳 中文", "ja": "🇯🇵 日本語",
}
LANGUAGE_NAMES = {
    "en": "English", "uk": "Ukrainian", "ru": "Russian",
    "tr": "Turkish", "zh": "Simplified Chinese", "ja": "Japanese",
}
T = {
    "en": {
        "welcome": "🧟 <b>ZOMBCASH MEME RADAR</b>\n\nDiscover active Solana meme coins using DexScreener market data.\n\nChoose an option:",
        "hot": "🔥 Hot Solana Coins", "ai": "🧠 AI Recommendations", "zombcash": "🧟 $ZOMBCASH Market", "addcoin": "➕ Submit a meme coin", "rating": "⭐ Community ratings", "community_ai": "🤖 AI pick from community coins",
        "language": "🌐 Choose your language:", "loading": "🔎 Scanning Solana markets…",
        "ai_loading": "🧠 Gemini is analyzing the market snapshot…",
        "empty": "🔍 No coins passed the current filters. Try again later.",
        "market_error": "⚠️ Market data is temporarily unavailable. Please try again later.",
        "ai_error": "⚠️ AI analysis is temporarily unavailable. Please try again later.",
        "menu": "👇 Choose an option:", "change": "24h change", "volume": "24h volume",
        "trades": "24h trades", "buys": "Buys", "sells": "Sells",
        "liquidity": "Liquidity", "score": "Activity score", "chart": "📈 Open DexScreener",
        "disclaimer": "⚠️ Market information only, not financial advice. Meme coins are highly risky.",
        "report": "🧠 GEMINI MARKET REPORT",
        "ai_note": "AI interpretation of the supplied market snapshot. Contract safety, holder distribution and liquidity locks have not been independently verified.",
    },
    "uk": {
        "welcome": "🧟 <b>ZOMBCASH MEME RADAR</b>\n\nПошук активних мемкойнів Solana за даними DexScreener.\n\nОберіть дію:",
        "hot": "🔥 Гарячі монети Solana", "ai": "🧠 Рекомендації ШІ", "zombcash": "🧟 Ринок $ZOMBCASH", "addcoin": "➕ Додати мемкойн", "rating": "⭐ Рейтинг спільноти", "community_ai": "🤖 Вибір ШІ з монет спільноти",
        "language": "🌐 Оберіть мову:", "loading": "🔎 Переглядаю ринки Solana…",
        "ai_loading": "🧠 Gemini аналізує ринкові дані…",
        "empty": "🔍 Монет, що відповідають фільтрам, не знайдено.",
        "market_error": "⚠️ Ринкові дані тимчасово недоступні. Спробуйте пізніше.",
        "ai_error": "⚠️ Аналіз ШІ тимчасово недоступний. Спробуйте пізніше.",
        "menu": "👇 Оберіть дію:", "change": "Зміна за 24 год.", "volume": "Обсяг за 24 год.",
        "trades": "Угоди за 24 год.", "buys": "Купівлі", "sells": "Продажі",
        "liquidity": "Ліквідність", "score": "Оцінка активності", "chart": "📈 Відкрити DexScreener",
        "disclaimer": "⚠️ Лише ринкова інформація, не фінансова порада. Мемкойни дуже ризиковані.",
        "report": "🧠 ЗВІТ GEMINI",
        "ai_note": "Аналіз ШІ за наданими даними. Безпеку контракту, розподіл власників і блокування ліквідності окремо не перевірено.",
    },
    "ru": {
        "welcome": "🧟 <b>ZOMBCASH MEME RADAR</b>\n\nПоиск активных мемкойнов Solana по данным DexScreener.\n\nВыберите действие:",
        "hot": "🔥 Горячие монеты Solana", "ai": "🧠 Рекомендации ИИ", "zombcash": "🧟 Рынок $ZOMBCASH", "addcoin": "➕ Добавить мемкойн", "rating": "⭐ Рейтинг сообщества", "community_ai": "🤖 Выбор ИИ из монет сообщества",
        "language": "🌐 Выберите язык:", "loading": "🔎 Просматриваю рынки Solana…",
        "ai_loading": "🧠 Gemini анализирует рыночные данные…",
        "empty": "🔍 Монет, прошедших фильтры, не найдено.",
        "market_error": "⚠️ Рыночные данные временно недоступны. Попробуйте позже.",
        "ai_error": "⚠️ Анализ ИИ временно недоступен. Попробуйте позже.",
        "menu": "👇 Выберите действие:", "change": "Изменение за 24 ч.", "volume": "Объём за 24 ч.",
        "trades": "Сделки за 24 ч.", "buys": "Покупки", "sells": "Продажи",
        "liquidity": "Ликвидность", "score": "Оценка активности", "chart": "📈 Открыть DexScreener",
        "disclaimer": "⚠️ Только рыночная информация, не финансовая рекомендация. Мемкойны очень рискованны.",
        "report": "🧠 ОТЧЁТ GEMINI",
        "ai_note": "Анализ ИИ по предоставленным данным. Безопасность контракта, распределение владельцев и блокировка ликвидности отдельно не проверялись.",
    },
    "tr": {
        "welcome": "🧟 <b>ZOMBCASH MEME RADAR</b>\n\nDexScreener verileriyle aktif Solana meme coin'lerini keşfedin.\n\nBir seçenek seçin:",
        "hot": "🔥 Popüler Solana Coin'leri", "ai": "🧠 Yapay Zekâ Analizi", "zombcash": "🧟 $ZOMBCASH Piyasası", "addcoin": "➕ Meme coin ekle", "rating": "⭐ Topluluk puanları", "community_ai": "🤖 Topluluktan AI seçimi",
        "language": "🌐 Dil seçin:", "loading": "🔎 Solana piyasaları taranıyor…",
        "ai_loading": "🧠 Gemini piyasa verilerini analiz ediyor…",
        "empty": "🔍 Filtrelere uyan coin bulunamadı.",
        "market_error": "⚠️ Piyasa verileri geçici olarak kullanılamıyor.",
        "ai_error": "⚠️ Yapay zekâ analizi geçici olarak kullanılamıyor.",
        "menu": "👇 Bir seçenek seçin:", "change": "24 saatlik değişim", "volume": "24 saatlik hacim",
        "trades": "24 saatlik işlem", "buys": "Alış", "sells": "Satış",
        "liquidity": "Likidite", "score": "Aktivite puanı", "chart": "📈 DexScreener'ı aç",
        "disclaimer": "⚠️ Yalnızca piyasa bilgisi, yatırım tavsiyesi değildir. Meme coin'ler yüksek risklidir.",
        "report": "🧠 GEMINI PİYASA RAPORU",
        "ai_note": "Sunulan piyasa verilerinin AI yorumudur. Sözleşme güvenliği, yatırımcı dağılımı ve likidite kilidi bağımsız doğrulanmamıştır.",
    },
    "zh": {
        "welcome": "🧟 <b>ZOMBCASH MEME RADAR</b>\n\n使用 DexScreener 市场数据发现活跃的 Solana Meme 币。\n\n请选择操作：",
        "hot": "🔥 Solana 热门代币", "ai": "🧠 AI 市场分析", "zombcash": "🧟 $ZOMBCASH 市场", "addcoin": "➕ 提交 Meme 币", "rating": "⭐ 社区评分", "community_ai": "🤖 AI推荐社区代币",
        "language": "🌐 请选择语言：", "loading": "🔎 正在扫描 Solana 市场…",
        "ai_loading": "🧠 Gemini 正在分析市场数据…", "empty": "🔍 没有代币符合当前筛选条件。",
        "market_error": "⚠️ 市场数据暂时不可用，请稍后重试。", "ai_error": "⚠️ AI 分析暂时不可用，请稍后重试。",
        "menu": "👇 请选择操作：", "change": "24小时涨跌", "volume": "24小时交易量",
        "trades": "24小时交易笔数", "buys": "买入", "sells": "卖出", "liquidity": "流动性",
        "score": "活跃度评分", "chart": "📈 打开 DexScreener",
        "disclaimer": "⚠️ 仅提供市场信息，不构成投资建议。Meme 币风险很高。",
        "report": "🧠 GEMINI 市场报告",
        "ai_note": "AI 根据提供的市场快照进行分析。合约安全、持有人分布和流动性锁定尚未独立核实。",
    },
    "ja": {
        "welcome": "🧟 <b>ZOMBCASH MEME RADAR</b>\n\nDexScreenerの市場データで活発なSolanaミームコインを探します。\n\n操作を選択してください：",
        "hot": "🔥 Solana注目コイン", "ai": "🧠 AI市場分析", "zombcash": "🧟 $ZOMBCASH市場", "addcoin": "➕ ミームコインを追加", "rating": "⭐ コミュニティ評価", "community_ai": "🤖 コミュニティからAI推薦",
        "language": "🌐 言語を選択してください：", "loading": "🔎 Solana市場を検索中…",
        "ai_loading": "🧠 Geminiが市場データを分析しています…", "empty": "🔍 現在の条件に合うコインがありません。",
        "market_error": "⚠️ 市場データを取得できません。後でもう一度お試しください。",
        "ai_error": "⚠️ AI分析を一時的に利用できません。後でもう一度お試しください。",
        "menu": "👇 操作を選択してください：", "change": "24時間の価格変動",
        "volume": "24時間の取引高", "trades": "24時間の取引回数",
        "buys": "買い", "sells": "売り", "liquidity": "流動性",
        "score": "市場活性度スコア", "chart": "📈 DexScreenerを開く",
        "disclaimer": "⚠️ 市場情報のみであり、投資助言ではありません。ミームコインには高いリスクがあります。",
        "report": "🧠 GEMINI市場レポート",
        "ai_note": "提供された市場データに基づくAI分析です。コントラクトの安全性、保有者分布、流動性ロックは独立して検証されていません。",
    },
}

HOT_UI = {
    "en": {
        "home": "🏠 Main Menu",
        "period": "🔥 <b>Hot Solana Coins</b>\nChoose a period to see up to five coins:",
        "choose": "Choose another period:",
        "p5": "⚡ Last 5 minutes",
        "p1": "🕐 Last hour",
        "p24": "📅 Last 24 hours",
        "s5": "⚡ 5 min",
        "s1": "🕐 1 h",
        "s24": "📅 24 h"
    },
    "ru": {
        "home": "🏠 Главное меню",
        "period": "🔥 <b>Горячие монеты Solana</b>\nВыберите период, чтобы увидеть до пяти монет:",
        "choose": "Выберите другой период:",
        "p5": "⚡ За 5 минут",
        "p1": "🕐 За час",
        "p24": "📅 За 24 часа",
        "s5": "⚡ 5 минут",
        "s1": "🕐 1 час",
        "s24": "📅 24 часа"
    },
    "uk": {
        "home": "🏠 Головне меню",
        "period": "🔥 <b>Гарячі монети Solana</b>\nОберіть період, щоб побачити до п’яти монет:",
        "choose": "Оберіть інший період:",
        "p5": "⚡ За 5 хвилин",
        "p1": "🕐 За годину",
        "p24": "📅 За 24 години",
        "s5": "⚡ 5 хв",
        "s1": "🕐 1 год",
        "s24": "📅 24 год"
    },
    "tr": {
        "home": "🏠 Ana Menü",
        "period": "🔥 <b>Popüler Solana Coin'leri</b>\nBeş coin'e kadar görmek için dönem seçin:",
        "choose": "Başka bir dönem seçin:",
        "p5": "⚡ Son 5 dakika",
        "p1": "🕐 Son 1 saat",
        "p24": "📅 Son 24 saat",
        "s5": "⚡ 5 dk",
        "s1": "🕐 1 sa",
        "s24": "📅 24 sa"
    },
    "zh": {
        "home": "🏠 主菜单",
        "period": "🔥 <b>Solana 热门代币</b>\n请选择时间范围，最多显示五个代币：",
        "choose": "选择其他时间段：",
        "p5": "⚡ 最近5分钟",
        "p1": "🕐 最近1小时",
        "p24": "📅 最近24小时",
        "s5": "⚡ 5分钟",
        "s1": "🕐 1小时",
        "s24": "📅 24小时"
    },
    "ja": {
        "home": "🏠 メインメニュー",
        "period": "🔥 <b>Solana注目コイン</b>\n最大5件を表示する期間を選択してください：",
        "choose": "別の期間を選択：",
        "p5": "⚡ 直近5分",
        "p1": "🕐 直近1時間",
        "p24": "📅 直近24時間",
        "s5": "⚡ 5分",
        "s1": "🕐 1時間",
        "s24": "📅 24時間"
    }
}
def hot_ui(lang, key):
    return HOT_UI.get(lang, HOT_UI['en'])[key]

SEARCH_QUERIES = [
    "SOL", "pump", "meme", "raydium", "bonk", "wif", "pepe", "dog",
    "cat", "frog", "ai", "trump", "inu", "moon", "community",
]


# ------------------------ DexScreener ------------------------

def api_get(path, params=None):
    response = requests.get(
        DEX_API + path, params=params, headers=HEADERS, timeout=15
    )
    response.raise_for_status()
    return response.json()


def discover_candidates():
    addresses = set()
    endpoints = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
        "/community-takeovers/latest/v1",
    ]

    for endpoint in endpoints:
        try:
            data = api_get(endpoint)
            if isinstance(data, list):
                for item in data:
                    if item.get("chainId") == "solana" and item.get("tokenAddress"):
                        addresses.add(item["tokenAddress"])
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Discovery endpoint %s failed: %s", endpoint, exc)

    for term in SEARCH_QUERIES:
        try:
            data = api_get("/latest/dex/search", {"q": term})
            for pair in data.get("pairs") or []:
                if pair.get("chainId") == "solana":
                    base = pair.get("baseToken") or {}
                    if base.get("address"):
                        addresses.add(base["address"])
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Search query %s failed: %s", term, exc)

    return sorted(addresses)[:MAX_DISCOVERY_ADDRESSES]


def get_token_pairs(address):
    try:
        data = api_get(f"/token-pairs/v1/solana/{address}")
        return data if isinstance(data, list) else []
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Token lookup failed for %s: %s", address, exc)
        return []


def parse_pair(pair, apply_filters=True):
    base = pair.get("baseToken") or {}
    liquidity = (pair.get("liquidity") or {}).get("usd") or 0
    volumes = pair.get("volume") or {}
    txns_all = pair.get("txns") or {}
    changes = pair.get("priceChange") or {}
    volume = volumes.get("h24") or 0
    txns = txns_all.get("h24") or {}
    buys, sells = txns.get("buys") or 0, txns.get("sells") or 0
    change, price = changes.get("h24"), pair.get("priceUsd")

    if pair.get("chainId") != "solana" or not base.get("address") or price is None or change is None:
        return None

    try:
        price, change = float(price), float(change)
        volume, liquidity = float(volume), float(liquidity)
        buys, sells = int(buys), int(sells)
    except (TypeError, ValueError):
        return None

    trades = buys + sells
    if apply_filters and (liquidity < MIN_LIQUIDITY_USD or volume < MIN_VOLUME_24H_USD or trades < MIN_TRADES_24H):
        return None

    address = base["address"]
    pair_info = pair.get("info") or {}
    return {
        "name": base.get("name") or "Unknown",
        "symbol": base.get("symbol") or "?",
        "address": address,
        "price": price,
        "change": change,
        "volume": volume,
        "buys": buys,
        "sells": sells,
        "trades": trades,
        "change_5m": float(changes.get("m5") or 0),
        "change_1h": float(changes.get("h1") or 0),
        "volume_5m": float(volumes.get("m5") or 0),
        "volume_1h": float(volumes.get("h1") or 0),
        "buys_5m": int((txns_all.get("m5") or {}).get("buys") or 0),
        "sells_5m": int((txns_all.get("m5") or {}).get("sells") or 0),
        "buys_1h": int((txns_all.get("h1") or {}).get("buys") or 0),
        "sells_1h": int((txns_all.get("h1") or {}).get("sells") or 0),
        "liquidity": liquidity,
        "url": pair.get("url") or f"https://dexscreener.com/solana/{address}",
        "websites": pair_info.get("websites") or [],
        "socials": pair_info.get("socials") or [],
    }


def score_token(token, period="h24"):
    if period == "m5":
        volume, buys, sells, change = token["volume_5m"], token["buys_5m"], token["sells_5m"], token["change_5m"]
    elif period == "h1":
        volume, buys, sells, change = token["volume_1h"], token["buys_1h"], token["sells_1h"], token["change_1h"]
    else:
        volume, buys, sells, change = token["volume"], token["buys"], token["sells"], token["change"]
    trades = buys + sells
    volume_score = math.log10(max(volume, 1))
    liquidity_score = math.log10(max(token["liquidity"], 1))
    trades_score = math.log10(max(trades, 1))
    buy_balance = buys / trades if trades else 0.5
    momentum = (max(-50, min(change, 100)) + 50) / 150
    token["score"] = 2 * volume_score + 1.5 * liquidity_score + 2 * trades_score + buy_balance + momentum
    token["period_change"] = change
    token["period_volume"] = volume
    token["period_buys"] = buys
    token["period_sells"] = sells
    token["period_trades"] = trades
    token["period"] = period
    return token["score"]


HOT_CACHE_TTL = {"m5": 300, "h1": 3600, "h24": 86400}
_hot_tokens_cache = {}


def get_hot_tokens_cached(period="h24", limit=MAX_RESULTS):
    period = period if period in HOT_CACHE_TTL else "h24"
    now = time.time()
    cached = _hot_tokens_cache.get(period)
    if cached and now - cached["saved_at"] < HOT_CACHE_TTL[period]:
        logger.info("Using cached Hot Coins period=%s age=%ss", period, int(now - cached["saved_at"]))
        return [dict(token) for token in cached["tokens"][:limit]]
    tokens = get_hot_tokens(period, limit)
    _hot_tokens_cache[period] = {"saved_at": now, "tokens": [dict(token) for token in tokens]}
    logger.info("Refreshed Hot Coins cache period=%s count=%s", period, len(tokens))
    return [dict(token) for token in tokens[:limit]]


def get_hot_tokens(period='h24', limit=MAX_RESULTS):
    addresses = set(discover_candidates())
    # Always include user-submitted Community Ratings tokens in the radar scan.
    try:
        community_addresses = get_community_addresses()
        addresses.update(community_addresses)
    except Exception:
        logger.exception("Could not load Community Ratings addresses")
        community_addresses = set()
    addresses = sorted(addresses)
    logger.info("Discovered %s unique Solana addresses (%s from Community Ratings)", len(addresses), len(community_addresses))
    by_address = {}

    for address in addresses:
        for pair in get_token_pairs(address):
            token = parse_pair(pair, apply_filters=(address not in community_addresses))
            if not token:
                continue
            key = token["address"]
            old = by_address.get(key)
            if old is None or token["liquidity"] > old["liquidity"]:
                by_address[key] = token

    tokens = list(by_address.values())
    for token in tokens:
        score_token(token, period)

    # First-pass Social & Community Intelligence for the scanner.
    # Only use observable DexScreener links here: no extra API calls or paid services.
    if tokens:
        raw_scores = [float(token.get("score") or 0) for token in tokens]
        low_score, high_score = min(raw_scores), max(raw_scores)
        score_span = high_score - low_score
        for token in tokens:
            market_score = 50.0 if score_span <= 0 else 100.0 * (float(token.get("score") or 0) - low_score) / score_span
            social_urls = []
            for item in token.get("socials") or []:
                url = item.get("url") if isinstance(item, dict) else None
                if url and url not in social_urls:
                    social_urls.append(url)
            for item in token.get("websites") or []:
                url = item.get("url") if isinstance(item, dict) else (item if isinstance(item, str) else None)
                if url and url not in social_urls:
                    social_urls.append(url)
            hosts = set()
            for url in social_urls:
                try:
                    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
                    if host:
                        hosts.add(host)
                except Exception:
                    continue
            social_links = {
                "website": any(h not in {"x.com", "twitter.com", "t.me", "telegram.org", "discord.gg", "discord.com", "github.com"} for h in hosts),
                "x": bool(hosts & {"x.com", "twitter.com"}),
                "telegram": bool(hosts & {"t.me", "telegram.org"}),
                "discord": bool(hosts & {"discord.gg", "discord.com"}),
                "github": "github.com" in hosts,
            }
            social_score = round(sum(social_links.values()) * 20)
            token["market_score_100"] = round(market_score)
            token["social_score_100"] = social_score
            token["social_links"] = social_links
            token["overall_score_100"] = round(market_score * 0.70 + social_score * 0.30)
    tokens.sort(key=lambda item: (
        item.get("overall_score_100", 0),
        item.get("score", 0),
        item.get("period_volume", 0),
        item.get("period_trades", 0),
    ), reverse=True)
    return tokens[:max(1, min(int(limit), MAX_RESULTS))]


# ------------------------ Gemini ------------------------

def gemini_generate_with_fallback(prompt, operation):
    """Call configured Gemini models with bounded retries for transient API failures."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is missing")
    client = genai.Client(api_key=GEMINI_API_KEY)
    errors = []
    transient_markers = ("503", "unavailable", "429", "timeout", "temporarily", "high demand", "500", "502", "504")
    for model in GEMINI_MODELS:
        for attempt in range(2):
            try:
                response = client.models.generate_content(model=model, contents=prompt)
                response_text = getattr(response, "text", None)
                if response_text and response_text.strip():
                    return response_text
                errors.append(f"{model}: empty response")
                break
            except Exception as exc:
                message = str(exc)
                low = message.lower()
                logger.warning("Gemini %s failed on %s attempt %s: %s", operation, model, attempt + 1, message[:240])
                errors.append(f"{model}: {message[:120]}")
                if attempt == 0 and any(marker in low for marker in transient_markers):
                    time.sleep(2)
                    continue
                break
    raise RuntimeError(f"Gemini {operation} failed for all configured models. " + " | ".join(errors))


def gemini_analyze(tokens, lang, period="h24"):
    """Ask Gemini to choose exactly one strongest candidate from the selected Hot Coins period."""
    if not tokens:
        raise RuntimeError("No market candidates supplied to Gemini.")

    period = period if period in ("m5", "h1", "h24") else "h24"
    period_names = {
        "en": {"m5": "last 5 minutes", "h1": "last hour", "h24": "last 24 hours"},
        "ru": {"m5": "последние 5 минут", "h1": "последний час", "h24": "последние 24 часа"},
        "uk": {"m5": "останні 5 хвилин", "h1": "остання година", "h24": "останні 24 години"},
        "tr": {"m5": "son 5 dakika", "h1": "son 1 saat", "h24": "son 24 saat"},
        "zh": {"m5": "最近5分钟", "h1": "最近1小时", "h24": "最近24小时"},
        "ja": {"m5": "直近5分", "h1": "直近1時間", "h24": "直近24時間"},
    }
    period_name = period_names.get(lang, period_names["en"])[period]
    candidates = []
    for token in tokens[:MAX_RESULTS]:
        candidates.append({
            "name": token["name"], "symbol": token["symbol"], "address": token["address"],
            "price_usd": token["price"],
            "change_percent_in_selected_period": token.get("period_change", token["change"]),
            "volume_usd_in_selected_period": token.get("period_volume", token["volume"]),
            "trades_in_selected_period": token.get("period_trades", token["trades"]),
            "buys_in_selected_period": token.get("period_buys", token["buys"]),
            "sells_in_selected_period": token.get("period_sells", token["sells"]),
            "liquidity_usd": token["liquidity"],
            "activity_score_for_selected_period": round(token["score"], 2),
            "social_intelligence": collect_social_signals(token),
        })

    prompt = f"""
You are a cautious Solana meme-coin analyst. Choose ONE best candidate from the supplied list,
not five options and not a general list. The Hot Solana Coins screen selected this period: {period_name}.
Rank candidates using the supplied market metrics for that period and the supplied public social/developer signals, while treating liquidity and risk as important. A coin with a sharp price increase but weak liquidity or very few trades should not automatically win. Do not simply select the highest activity score without analysis.
Treat social links as evidence of presence only, never proof of real users or engagement. Google News RSS is a partial indexed-news sample, not a complete X mention count. Do not claim X activity, follower growth, Telegram member counts, or developer activity unless those facts are explicitly present in INPUT DATA. If GitHub is absent or unavailable, say it was not verified. Distinguish zero search results from an unavailable source. Never infer scam/fraud from missing social links alone.
Language: {LANGUAGE_NAMES.get(lang, 'English')}.
Return ONLY valid JSON with this exact structure:
{{
  "winner_address": "copy the exact address of exactly one candidate",
  "reason": "why this candidate is the strongest among the supplied candidates, in the requested language",
  "momentum": "brief interpretation of price movement in the selected period",
  "activity": "brief interpretation of volume and buys versus sells in the selected period",
  "social_assessment": "assess available social links and what is or is not verified about real community engagement",
  "web_mentions": "summarize indexed news mentions; state clearly if no data or no results are available",
  "dev_activity": "summarize public developer activity if a GitHub repository is linked; otherwise say not verified/not applicable",
  "social_risk": "one concise community/social manipulation or evidence-quality concern, without inventing facts",
  "risk": "the most relevant risk visible in the supplied data",
  "checks": "what the user should verify independently before considering a purchase",
  "disclaimer": "brief risk disclaimer in the requested language"
}}
Use only addresses from INPUT DATA. Never claim to have checked mint/freeze authority, holder concentration,
taxes, contract safety, or liquidity locks because those facts are not supplied. Do not promise profit,
guaranteed returns, or certainty. If data is insufficient, still identify the most defensible candidate
but clearly say confidence is low. Keep each field concise. No Markdown or text outside JSON.
INPUT DATA:
{json.dumps(candidates, ensure_ascii=False)}
"""
    response_text = gemini_generate_with_fallback(prompt, "single-coin recommendation")
    raw = re.sub(r"^\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60$", "", response_text.strip(), flags=re.I)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini returned non-JSON recommendation: {response_text[:400]}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Gemini returned an unexpected recommendation format.")

    by_address = {token["address"]: token for token in tokens[:MAX_RESULTS]}
    winner_address = str(result.get("winner_address") or "").strip()
    winner = by_address.get(winner_address)
    if winner is None:
        raise RuntimeError("Gemini recommendation did not select an exact address from the supplied candidates.")

    labels = {
        "en": {"title":"AI'S TOP PICK", "period":"Selected period", "price":"Price", "change":"Price change", "volume":"Volume", "trades":"Trades", "buys":"Buys", "sells":"Sells", "liquidity":"Liquidity", "reason":"Why AI selected it", "momentum":"Momentum", "activity":"Trading activity", "risk":"Main risk", "checks":"Checks before buying", "address":"Contract address", "source":"Source: DexScreener market data + Gemini AI"},
        "ru": {"title":"ВЫБОР ИИ — ЛУЧШАЯ МОНЕТА", "period":"Выбранный период", "price":"Цена", "change":"Изменение цены", "volume":"Объём", "trades":"Сделок", "buys":"Покупки", "sells":"Продажи", "liquidity":"Ликвидность", "reason":"Почему ИИ выбрал её", "momentum":"Динамика", "activity":"Торговая активность", "risk":"Главный риск", "checks":"Что проверить перед покупкой", "address":"Адрес контракта", "source":"Источник: рыночные данные DexScreener + анализ Gemini AI"},
        "uk": {"title":"ВИБІР ШІ — НАЙКРАЩА МОНЕТА", "period":"Обраний період", "price":"Ціна", "change":"Зміна ціни", "volume":"Обсяг", "trades":"Угод", "buys":"Купівлі", "sells":"Продажі", "liquidity":"Ліквідність", "reason":"Чому ШІ обрав її", "momentum":"Динаміка", "activity":"Торгова активність", "risk":"Головний ризик", "checks":"Що перевірити перед купівлею", "address":"Адреса контракту", "source":"Джерело: ринкові дані DexScreener + аналіз Gemini AI"},
        "tr": {"title":"AI'NIN EN İYİ SEÇİMİ", "period":"Seçilen dönem", "price":"Fiyat", "change":"Fiyat değişimi", "volume":"Hacim", "trades":"İşlem", "buys":"Alış", "sells":"Satış", "liquidity":"Likidite", "reason":"AI neden seçti", "momentum":"Fiyat hareketi", "activity":"İşlem etkinliği", "risk":"Ana risk", "checks":"Almadan önce kontrol edin", "address":"Kontrat adresi", "source":"Kaynak: DexScreener verileri + Gemini AI"},
        "zh": {"title":"AI 最佳推荐", "period":"所选时间段", "price":"价格", "change":"价格变化", "volume":"成交量", "trades":"交易笔数", "buys":"买入", "sells":"卖出", "liquidity":"流动性", "reason":"AI 选择理由", "momentum":"价格走势", "activity":"交易活跃度", "risk":"主要风险", "checks":"买入前请核查", "address":"合约地址", "source":"数据来源：DexScreener + Gemini AI"},
        "ja": {"title":"AIが選ぶ最有力コイン", "period":"選択期間", "price":"価格", "change":"価格変動", "volume":"取引高", "trades":"取引数", "buys":"買い", "sells":"売り", "liquidity":"流動性", "reason":"AIの選定理由", "momentum":"価格動向", "activity":"取引状況", "risk":"主なリスク", "checks":"購入前の確認事項", "address":"コントラクトアドレス", "source":"データ元：DexScreener + Gemini AI"},
    }
    L = labels.get(lang, labels["en"])
    social_labels = {
        "en": ("Community / socials", "Indexed web mentions", "Developer activity", "Social signal caveat"),
        "ru": ("Соцсети и сообщество", "Упоминания в поиске/новостях", "Активность разработчиков", "Ограничения соцсигнала"),
        "uk": ("Соцмережі та спільнота", "Згадки в пошуку/новинах", "Активність розробників", "Обмеження соцсигналу"),
        "tr": ("Topluluk / sosyal ağlar", "Web haberlerinde anılma", "Geliştirici etkinliği", "Sosyal sinyal sınırlaması"),
        "zh": ("社区 / 社交媒体", "网页新闻提及", "开发者活动", "社交信号限制"),
        "ja": ("コミュニティ / SNS", "検索・ニュースでの言及", "開発者の活動", "ソーシャル指標の注意点"),
    }.get(lang, ("Community / socials", "Indexed web mentions", "Developer activity", "Social signal caveat"))
    change = winner.get("period_change", winner["change"])
    sign = "+" if change > 0 else ""
    direction = "🟢" if change > 0 else ("🔴" if change < 0 else "⚪")
    out = [
        f"🧠 <b>{L['title']}</b>",
        f"🕒 <b>{L['period']}:</b> {period_name}",
        f"📡 {L['source']}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🪙 <b>{html.escape(winner['name'])} (${html.escape(winner['symbol'])})</b>",
        f"💵 <b>{L['price']}:</b> {price_text(winner['price'])}",
        f"{direction} <b>{L['change']}:</b> {sign}{change:.2f}%",
        f"💰 <b>{L['volume']}:</b> {money(winner.get('period_volume', winner['volume']))}",
        f"🔄 <b>{L['trades']}:</b> {winner.get('period_trades', winner['trades']):,}",
        f"🟢 <b>{L['buys']}:</b> {winner.get('period_buys', winner['buys']):,}  |  🔴 <b>{L['sells']}:</b> {winner.get('period_sells', winner['sells']):,}",
        f"💧 <b>{L['liquidity']}:</b> {money(winner['liquidity'])}",
        f"📋 <b>{L['address']}:</b>\n<code>{html.escape(winner['address'])}</code>",
        f"🔗 https://dexscreener.com/solana/{html.escape(winner['address'])}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🏆 <b>{L['reason']}:</b> {html.escape(str(result.get('reason') or ''))}",
        f"📈 <b>{L['momentum']}:</b> {html.escape(str(result.get('momentum') or ''))}",
        f"📊 <b>{L['activity']}:</b> {html.escape(str(result.get('activity') or ''))}",
        f"🌐 <b>{social_labels[0]}:</b> {html.escape(str(result.get('social_assessment') or 'Not enough social data'))}",
        f"🔎 <b>{social_labels[1]}:</b> {html.escape(str(result.get('web_mentions') or 'No indexed mention data'))}",
        f"🛠 <b>{social_labels[2]}:</b> {html.escape(str(result.get('dev_activity') or 'Not verified'))}",
        f"🧩 <b>{social_labels[3]}:</b> {html.escape(str(result.get('social_risk') or 'Social metrics may be incomplete'))}",
        f"⚠️ <b>{L['risk']}:</b> {html.escape(str(result.get('risk') or ''))}",
        f"🔎 <b>{L['checks']}:</b> {html.escape(str(result.get('checks') or ''))}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"⚠️ {html.escape(str(result.get('disclaimer') or T[lang]['disclaimer']))}",
    ]
    return "\n".join(out)


# ------------------------ Scanner AI Social & Community Intelligence ------------------------
_SCANNER_AI_SCORE_CACHE = {}
_SCANNER_AI_SCORE_TTL = 900


def _score_bar(value, width=14):
    value = max(0, min(100, int(value)))
    filled = round(value / 100 * width)
    return "█" * filled + "░" * (width - filled)


def gemini_score_scanner_tokens(tokens, lang="en", period="h24"):
    """Score the scanner's current top candidates in one Gemini request and rank by total score."""
    if not tokens:
        return tokens
    period = period if period in ("m5", "h1", "h24") else "h24"
    now = time.time()
    uncached = []
    for token in tokens:
        cache_key = (token.get("address", ""), period)
        cached = _SCANNER_AI_SCORE_CACHE.get(cache_key)
        if cached and now - cached["saved_at"] < _SCANNER_AI_SCORE_TTL:
            token.update(cached["scores"])
        else:
            uncached.append(token)

    if uncached and GEMINI_API_KEY:
        candidates = []
        for token in uncached:
            try:
                signals = collect_social_signals(token)
            except Exception:
                logger.exception("Scanner social signals failed for %s", token.get("symbol"))
                signals = {"social_links": token.get("social_links", {}), "github_activity": {"status": "unavailable"}, "web_news_mentions_approx": None, "news_status": "unavailable"}
            candidates.append({
                "address": token["address"],
                "name": token["name"],
                "symbol": token["symbol"],
                "market": {
                    "price_usd": token["price"],
                    "change_percent": token.get("period_change", token["change"]),
                    "volume_usd": token.get("period_volume", token["volume"]),
                    "trades": token.get("period_trades", token["trades"]),
                    "buys": token.get("period_buys", token["buys"]),
                    "sells": token.get("period_sells", token["sells"]),
                    "liquidity_usd": token["liquidity"],
                },
                "public_signals": signals,
            })
        prompt = f"""
You are a cautious meme-coin project analyst. Score EVERY candidate separately using only INPUT DATA.
Language for short explanation: {LANGUAGE_NAMES.get(lang, 'English')}.
Return ONLY valid JSON in this exact shape:
{{"results":[{{"address":"exact input address","market_activity":0,"community":0,"x_mentions":0,"team_activity":0,"reputation_risk":0,"overall":0,"summary":"one concise evidence-based sentence","confidence":"low|medium|high"}}]}}
All scores are integers from 0 to 100. Higher reputation_risk score means stronger available evidence of transparency/reputation and fewer observed red flags, NOT a guarantee of safety.
Use market metrics for market_activity. Use linked social platform presence and available evidence cautiously for community. For x_mentions use only explicitly available mention data; if no direct X mention data exists, score conservatively and state that it is not verified. For team_activity use public GitHub data only; no linked repo or unavailable lookup means unknown/low confidence, not proof of inactivity. For reputation_risk, reward evidence completeness and clearly verified public information, but do not claim contract safety, holder concentration, mint/freeze authority, or liquidity locks were checked.
Calculate overall as weighted score: market_activity 35%, community 25%, x_mentions 15%, team_activity 15%, reputation_risk 10%. If evidence is missing, lower confidence and do not fabricate facts. Keep summary concise.
INPUT DATA:
{json.dumps(candidates, ensure_ascii=False)}
"""
        try:
            response_text = gemini_generate_with_fallback(prompt, "scanner social intelligence scoring")
            raw = re.sub(r"^\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60$", "", response_text.strip(), flags=re.I)
            result = json.loads(raw)
            results = result.get("results") if isinstance(result, dict) else None
            by_address = {str(item.get("address", "")).strip(): item for item in results or [] if isinstance(item, dict)}
            for token in uncached:
                item = by_address.get(token["address"])
                if not item:
                    continue
                def valid_score(key, fallback):
                    try:
                        return max(0, min(100, int(item.get(key, fallback))))
                    except (TypeError, ValueError):
                        return fallback
                scores = {
                    "market_activity_score": valid_score("market_activity", token.get("market_score_100", 50)),
                    "community_score": valid_score("community", token.get("social_score_100", 0)),
                    "x_mentions_score": valid_score("x_mentions", 0),
                    "team_activity_score": valid_score("team_activity", 0),
                    "reputation_risk_score": valid_score("reputation_risk", 50),
                    "overall_score_100": valid_score("overall", 0),
                    "ai_summary": str(item.get("summary") or "Not enough verified public data."),
                    "ai_confidence": str(item.get("confidence") or "low").lower(),
                    "ai_scored": True,
                }
                # Recalculate weighted total locally so inconsistent model arithmetic cannot affect ranking.
                scores["overall_score_100"] = round(
                    scores["market_activity_score"] * 0.35
                    + scores["community_score"] * 0.25
                    + scores["x_mentions_score"] * 0.15
                    + scores["team_activity_score"] * 0.15
                    + scores["reputation_risk_score"] * 0.10
                )
                token.update(scores)
                _SCANNER_AI_SCORE_CACHE[(token["address"], period)] = {"saved_at": now, "scores": dict(scores)}
        except Exception:
            logger.exception("Scanner AI social scoring failed; retaining deterministic fallback ranking")

    for token in tokens:
        if not token.get("ai_scored"):
            token.setdefault("market_activity_score", token.get("market_score_100", 50))
            token.setdefault("community_score", token.get("social_score_100", 0))
            token.setdefault("x_mentions_score", 0)
            token.setdefault("team_activity_score", 0)
            token.setdefault("reputation_risk_score", 50)
            token.setdefault("overall_score_100", round(
                token["market_activity_score"] * 0.70 + token["community_score"] * 0.30
            ))
            token.setdefault("ai_summary", "AI score unavailable; showing a basic market/social-link estimate.")
            token.setdefault("ai_confidence", "low")
    tokens.sort(key=lambda item: (
        item.get("overall_score_100", 0),
        item.get("market_activity_score", 0),
        item.get("period_volume", 0),
        item.get("period_trades", 0),
    ), reverse=True)
    return tokens


# ------------------------ Community coins and ratings ------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INITIAL_DB_PATH = os.path.join(BASE_DIR, "community_coins.sqlite3")
DB_PATH = "/data/community_coins.sqlite3"

if not os.path.exists(DB_PATH) and os.path.exists(INITIAL_DB_PATH):
    import shutil
    shutil.copy2(INITIAL_DB_PATH, DB_PATH)

def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS community_coins (
            address TEXT PRIMARY KEY, name TEXT NOT NULL, symbol TEXT NOT NULL,
            url TEXT NOT NULL, submitted_by INTEGER NOT NULL, submitted_at INTEGER NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS community_votes (
            address TEXT NOT NULL, user_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5), voted_at INTEGER NOT NULL,
            PRIMARY KEY(address, user_id),
            FOREIGN KEY(address) REFERENCES community_coins(address))""")

def fetch_token_for_submission(address):
    pairs = get_token_pairs(address)
    valid = [p for p in pairs if p.get("chainId") == "solana"
             and (p.get("baseToken") or {}).get("address") == address]
    if not valid:
        return None
    valid.sort(key=lambda p: float(((p.get("liquidity") or {}).get("usd")) or 0), reverse=True)
    pair = valid[0]
    base = pair.get("baseToken") or {}
    return {"address": address, "name": base.get("name") or "Unknown",
            "symbol": base.get("symbol") or "?",
            "url": pair.get("url") or f"https://dexscreener.com/solana/{address}"}

def save_community_coin(token, user_id):
    with db_connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO community_coins(address,name,symbol,url,submitted_by,submitted_at) VALUES(?,?,?,?,?,?)",
            (token["address"], token["name"], token["symbol"], token["url"], user_id, int(time.time())))
        return cur.rowcount > 0

def get_community_ratings(limit=10):
    with db_connect() as conn:
        rows = conn.execute("""
            SELECT c.address,c.name,c.symbol,c.url,COUNT(v.user_id) AS votes,
                   AVG(v.rating) AS average_rating
            FROM community_coins c LEFT JOIN community_votes v ON v.address=c.address
            GROUP BY c.address
            ORDER BY COUNT(v.user_id) DESC,
                     COALESCE(AVG(v.rating), 0) DESC, c.submitted_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]

def get_community_market_candidates(limit=20):
    """Fetch current DexScreener metrics for globally submitted community coins."""
    rows = get_community_ratings(limit)
    candidates = []
    for row in rows:
        best = None
        for pair in get_token_pairs(row["address"]):
            token = parse_pair(pair, apply_filters=False)
            if token and token["address"] == row["address"]:
                if best is None or token["liquidity"] > best["liquidity"]:
                    best = token
        if best:
            best["community_votes"] = int(row.get("votes") or 0)
            best["community_average"] = float(row.get("average_rating") or 0)
            candidates.append(best)
    return candidates


def gemini_community_recommendation(lang):
    """Choose one coin from the shared community list and explain the choice and risks."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is missing")
    tokens = get_community_market_candidates(limit=20)
    if not tokens:
        return "🌐 No community coins with current DexScreener market data were found. Submit a coin or try again later."

    candidates = []
    for token in tokens:
        score_token(token, "h24")
        candidates.append({
            "name": token["name"], "symbol": token["symbol"], "address": token["address"],
            "price_usd": token["price"], "change_5m_percent": token["change_5m"],
            "change_1h_percent": token["change_1h"], "change_24h_percent": token["change"],
            "volume_24h_usd": token["volume"], "trades_24h": token["trades"],
            "buys_24h": token["buys"], "sells_24h": token["sells"],
            "liquidity_usd": token["liquidity"], "community_votes": token["community_votes"],
            "community_average_rating_1_to_5": round(token["community_average"], 2),
            "activity_score": round(token["score"], 2),
        })
    prompt = f"""
You are a cautious Solana meme-coin analyst. Choose exactly ONE best-supported candidate from this global community list.
Language: {LANGUAGE_NAMES.get(lang, 'English')}.
Use momentum across 5m/1h/24h, liquidity, volume, trades, buy/sell balance and community votes.
Votes indicate interest, not quality. Do not automatically choose the most-voted coin.
You MUST select exactly one candidate address from INPUT DATA whenever at least one candidate is supplied, even when confidence is low. Never return an empty address just because evidence is imperfect. Prefer candidates with meaningful liquidity and trading activity, sensible buy/sell balance, and consistent momentum; use community votes only as a weak signal. If evidence is weak, select the least-weak candidate and explicitly state low confidence and why.
Return ONLY valid JSON:
{{"address":"exact address copied from one input candidate; never empty when candidates exist","reason":"2-4 concise sentences grounded in metrics; state confidence level","risk":"concrete risks/data limitations","verdict":"WATCH or LOW CONFIDENCE translated into requested language"}}
Never promise profit or claim to have checked contract safety, holder distribution, mint/freeze authority or liquidity locks.
INPUT DATA:
{json.dumps(candidates, ensure_ascii=False)}
"""
    response_text = gemini_generate_with_fallback(prompt, "community recommendation")
    fence = chr(96) * 3
    raw = response_text.strip().replace(fence + "json", "").replace(fence, "").strip()
    result = json.loads(raw)
    chosen_address = str(result.get("address") or "").strip()
    token = next((item for item in tokens if item["address"] == chosen_address), None)

    # Do not leave the user with a dead end if Gemini abstains or returns an invalid address.
    # Fall back to the existing market score and clearly mark it as low confidence.
    fallback_used = token is None
    if fallback_used:
        token = max(
            tokens,
            key=lambda item: (
                item.get("score", 0),
                item.get("liquidity", 0),
                item.get("period_volume", item.get("volume", 0)),
                item.get("period_trades", item.get("trades", 0)),
            ),
        )
        logger.warning(
            "Gemini community recommendation returned no valid candidate; deterministic fallback selected %s",
            token.get("address", "unknown"),
        )

    change = token["change"]
    sign = "+" if change > 0 else ""
    if fallback_used:
        reason_text = (
            "Gemini did not return a valid winner, so this candidate was selected by the radar's "
            "market-activity score from the available community coins. Treat this as a low-confidence "
            "fallback, not a confirmed AI pick."
        )
        risk_text = (
            "The AI selection could not be validated. The score reflects available market metrics only; "
            "it does not verify contract safety, holder concentration, mint/freeze authority, or liquidity locks."
        )
        verdict_text = "LOW CONFIDENCE — CHECK MANUALLY"
    else:
        reason_text = str(result.get("reason") or "No detailed explanation was returned.")
        risk_text = str(result.get("risk") or "Market data is incomplete; verify the contract independently.")
        verdict_text = str(result.get("verdict") or "WATCH")
    reason = html.escape(reason_text)
    risk = html.escape(risk_text)
    verdict = html.escape(verdict_text)
    text = (
        "🤖 <b>AI COMMUNITY PICK</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{html.escape(token['name'])} ({html.escape(token['symbol'])})</b>\n"
        f"💵 Price: {price_text(token['price'])}\n"
        f"📈 5m: {token['change_5m']:+.2f}% | 1h: {token['change_1h']:+.2f}% | 24h: {sign}{change:.2f}%\n"
        f"💰 24h volume: {money(token['volume'])}\n"
        f"🔄 24h trades: {token['trades']:,} (🟢 {token['buys']:,} buys / 🔴 {token['sells']:,} sells)\n"
        f"💧 Liquidity: {money(token['liquidity'])}\n"
        f"🗳 Community votes: {token['community_votes']} | ⭐ Average rating: {token['community_average']:.2f}/5\n\n"
        f"🧠 <b>Why AI selected it</b>\n{reason}\n\n"
        f"⚠️ <b>Risks</b>\n{risk}\n\n"
        f"🚦 <b>Assessment:</b> {verdict}\n"
        f"📋 Contract: <code>{html.escape(token['address'])}</code>\n"
        f"📈 <a href=\"{html.escape(token['url'], quote=True)}\">Open DexScreener</a>\n\n"
        "⚠️ Community interest and AI analysis do not guarantee profit. Verify contract safety independently."
    )
    return text


def get_community_addresses():
    with db_connect() as conn:
        rows = conn.execute("SELECT address FROM community_coins").fetchall()
        return {row["address"] for row in rows}


def delete_community_coin(address):
    """Delete one community coin and its votes in a single transaction."""
    with db_connect() as conn:
        exists = conn.execute("SELECT 1 FROM community_coins WHERE address=?", (address,)).fetchone()
        if not exists:
            return False
        conn.execute("DELETE FROM community_votes WHERE address=?", (address,))
        conn.execute("DELETE FROM community_coins WHERE address=?", (address,))
        return True


def is_configured_admin(user_id):
    return user_id in ADMIN_USER_IDS


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user and update.effective_message:
        await update.effective_message.reply_text(
            f"Your Telegram user ID: {update.effective_user.id}\n"
            "To grant admin delete permissions, add this number to ZOMBCASH_ADMIN_IDS in your Windows environment, then restart the bot."
        )


def cast_community_vote(address, user_id, rating):
    if not 1 <= rating <= 5:
        return False, "invalid"
    with db_connect() as conn:
        if not conn.execute("SELECT 1 FROM community_coins WHERE address=?", (address,)).fetchone():
            return False, "missing"
        previous = conn.execute(
            "SELECT rating FROM community_votes WHERE address=? AND user_id=?", (address, user_id)).fetchone()
        conn.execute("""
            INSERT INTO community_votes(address,user_id,rating,voted_at) VALUES(?,?,?,?)
            ON CONFLICT(address,user_id) DO UPDATE SET rating=excluded.rating,voted_at=excluded.voted_at
        """, (address, user_id, rating, int(time.time())))
        return True, ("updated" if previous else "added")


# ------------------------ Telegram UI ------------------------

def get_lang(context):
    return context.user_data.get("lang", "en")


def main_keyboard(lang):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(T[lang]["hot"], callback_data="hot")],
        [InlineKeyboardButton(T[lang]["zombcash"], callback_data="zombcash")],
        [InlineKeyboardButton(T[lang]["ai"], callback_data="ai")],
        [InlineKeyboardButton(T[lang]["addcoin"], callback_data="addcoin")],
        [InlineKeyboardButton("⭐ Rate this coin", callback_data="rating")],
        [InlineKeyboardButton(T[lang]["community_ai"], callback_data="community_ai")],
        [InlineKeyboardButton("🌐 Language / Мова / Язык / 言語", callback_data="languages")],
    ])


def language_keyboard(lang="en"):
    rows, row = [], []
    for code, label in LANGUAGES.items():
        row.append(InlineKeyboardButton(label, callback_data=f"lang:{code}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(hot_ui(lang if lang in LANGUAGES else "en", "home"), callback_data="home")])
    return InlineKeyboardMarkup(rows)


def money(value):
    return f"${value:,.0f}"


def price_text(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(number) or number <= 0:
        return "—"
    return f"${number:,.4f}" if number >= 1 else f"${number:.10f}"


def fetch_zombcash_market():
    """Read ZombieCash market metrics from the shared FadeHost API."""
    response = requests.get(
        "https://zombcash-bot.fadehost.app/api/zombcash",
        headers=HEADERS,
        timeout=12,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not data.get("priceUsd"):
        raise ValueError("Shared API returned no ZombieCash price")
    return data


def market_money(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(number) or number < 0:
        return "—"
    if number >= 1_000_000_000:
        return f"${number / 1_000_000_000:.2f}B"
    if number >= 1_000_000:
        return f"${number / 1_000_000:.2f}M"
    if number >= 1_000:
        return f"${number / 1_000:.1f}K"
    return f"${number:.2f}"


async def post_radar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Publish one clean Meme Radar promo post to the ZombieCash channel."""
    if update.effective_chat and update.effective_chat.type != "private":
        await update.effective_message.reply_text(
            "Для публикации открой личный чат с ботом и отправь /post_radar."
        )
        return

    text = (
        "🧟 <b>ZOMBIECASH MEME RADAR</b>\n"
        "<i>The market never sleeps. Neither do we.</i>\n\n"
        "🔥 Discover hot Solana meme coins\n"
        "🧠 Get AI-powered market analysis\n"
        "📊 Check 24h volume, price changes and trading activity\n"
        "⭐ Submit coins and vote for community favorites\n\n"
        "📡 Market data: DexScreener\n"
        "🤖 AI insights: Gemini\n"
        "⚠️ Market information only — not financial advice."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧪 OPEN ZOMBILAB", url="https://t.me/MemeCoinAIAgent_bot")],
        [InlineKeyboardButton("📊 OPEN MEME RADAR", url=RADAR_BOT_URL)],
    ])

    # Avoid creating another post every time the command is repeated.
    # The bot stores the message ID after the first successful publication.
    known_id = context.bot_data.get("radar_promo_message_id")
    if known_id:
        try:
            await context.bot.edit_message_text(
                chat_id=RADAR_CHANNEL,
                message_id=known_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            await update.effective_message.reply_text(
                "✅ Существующая публикация обновлена — новый пост не создан."
            )
            return
        except Exception:
            logger.exception("Could not update previous Meme Radar promo post; will publish a new one")

    try:
        sent = await context.bot.send_message(
            chat_id=RADAR_CHANNEL,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        context.bot_data["radar_promo_message_id"] = sent.message_id
        await update.effective_message.reply_text(
            "✅ Публикация с кнопкой отправлена в канал. "
            "Закрепи её в канале. Повторная команда будет пытаться обновить этот пост."
        )
        logger.info(
            "Meme Radar promo post published to %s (message_id=%s)",
            RADAR_CHANNEL,
            sent.message_id,
        )
    except Exception:
        logger.exception("Could not publish Meme Radar promo post")
        await update.effective_message.reply_text(
            "⚠️ Не удалось опубликовать пост. Проверь права бота в канале @ZOMBCASH "
            "и журнал CMD."
        )


async def post_radar_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Post the Meme Radar launch message to the ZombieCash CTO group."""
    if update.effective_chat and update.effective_chat.type == "private":
        await update.effective_message.reply_text(
            "Открой группу ZombieCash CTO | $ZOMBCASH и отправь там /radar_button."
        )
        return

    text = (
        "🧟 <b>ZOMBIECASH MEME RADAR</b>\n"
        "🔥 Hot Solana meme coins\n"
        "🧠 AI market analysis powered by Gemini\n"
        "📊 24h volume, price changes and trading activity\n"
        "⭐ Submit coins and vote for community favorites\n\n"
        "📡 Market data from DexScreener. Informational only — not financial advice."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧪 OPEN ZOMBILAB", url="https://t.me/MemeCoinAIAgent_bot")],
        [InlineKeyboardButton("📊 OPEN MEME RADAR", url=RADAR_BOT_URL)],
    ])
    try:
        await update.effective_message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception("Could not post Meme Radar button in group")
        await update.effective_message.reply_text(
            "Не удалось разместить кнопку. Проверь, что бот добавлен в группу и имеет право отправлять сообщения."
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(context)
    await update.effective_message.reply_text(
        T[lang]["welcome"], parse_mode="HTML", reply_markup=main_keyboard(lang)
    )


async def safe_answer_callback(query):
    """Answer fast; safely ignore callbacks that Telegram considers expired."""
    try:
        await query.answer()
        return True
    except BadRequest as exc:
        message = str(exc).lower()
        if any(s in message for s in (
            "query is too old", "response timeout expired", "query id is invalid"
        )):
            logger.warning("Ignored expired Telegram callback query")
            return False
        logger.warning("Callback answer failed: %s", exc)
        return False
    except TelegramError as exc:
        logger.warning("Callback answer Telegram error: %s", exc)
        return False


async def show_tokens(bot, chat_id, tokens, lang, period="h24"):
    period_labels = {"m5": "5 min", "h1": "1 hour", "h24": "24 hours"}
    period = period if period in period_labels else "h24"
    for index, token in enumerate(tokens, 1):
        change = token.get("period_change", token["change"])
        sign = "+" if change >= 0 else ""
        direction = "🟢" if change > 0 else ("🔴" if change < 0 else "⚪")
        name = html.escape(token["name"])
        symbol = html.escape(token["symbol"])
        address = html.escape(token["address"])
        message = (
            f"🪙 <b>{index}. {name} (${symbol})</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"💵 <b>Price:</b> {price_text(token['price'])}\n"
            f"{direction} <b>{T[lang]['change']} ({period_labels[period]}):</b> {sign}{change:.2f}%\n"
            f"💰 <b>{T[lang]['volume']} ({period_labels[period]}):</b> {money(token.get('period_volume', token['volume']))}\n"
            f"🔄 <b>{T[lang]['trades']} ({period_labels[period]}):</b> {token.get('period_trades', token['trades']):,}\n"
            f"🟢 <b>{T[lang]['buys']}:</b> {token.get('period_buys', token['buys']):,}\n"
            f"🔴 <b>{T[lang]['sells']}:</b> {token.get('period_sells', token['sells']):,}\n"
            f"💧 <b>{T[lang]['liquidity']}:</b> {money(token['liquidity'])}\n"
            f"🏆 <b>#{index} · OVERALL PROJECT SCORE: {token.get('overall_score_100', 0)}/100</b>\n"
            f"📊 Market activity  {token.get('market_activity_score', 0)}/100\n"
            f"<code>{_score_bar(token.get('market_activity_score', 0))}</code>\n"
            f"👥 Community  {token.get('community_score', 0)}/100\n"
            f"<code>{_score_bar(token.get('community_score', 0))}</code>\n"
            f"𝕏 X mentions  {token.get('x_mentions_score', 0)}/100\n"
            f"<code>{_score_bar(token.get('x_mentions_score', 0))}</code>\n"
            f"🛠 Team activity  {token.get('team_activity_score', 0)}/100\n"
            f"<code>{_score_bar(token.get('team_activity_score', 0))}</code>\n"
            f"🛡 Reputation & risks  {token.get('reputation_risk_score', 0)}/100\n"
            f"<code>{_score_bar(token.get('reputation_risk_score', 0))}</code>\n"
            f"🧠 <b>AI assessment:</b> {html.escape(token.get('ai_summary', 'Not enough verified data.'))}\n"
            f"🔎 Confidence: {html.escape(token.get('ai_confidence', 'low').upper())}\n"
            f"ℹ️ X activity is not verified through paid X API. Scores are not a contract-safety audit.\n\n"
            f"📋 <b>Contract address:</b>\n<code>{address}</code>\n\n📡 Source: DexScreener market data\n{T[lang]['disclaimer']}"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(T[lang]["chart"], url=token["url"])],
            [InlineKeyboardButton(hot_ui(lang, "home"), callback_data="home")],
        ])
        await bot.send_message(
            chat_id=chat_id, text=message, parse_mode="HTML",
            reply_markup=keyboard, disable_web_page_preview=True,
        )


async def send_long_text(bot, chat_id, text, limit=3200):
    chunk = ""
    for line in text.splitlines():
        candidate = chunk + line.rstrip() + "\n"
        if len(candidate) > limit and chunk.strip():
            await bot.send_message(chat_id=chat_id, text=chunk.rstrip(), parse_mode="HTML")
            chunk = line.rstrip() + "\n"
        else:
            chunk = candidate
    if chunk.strip():
        await bot.send_message(chat_id=chat_id, text=chunk.rstrip(), parse_mode="HTML")



async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_user:
        return
    if not context.user_data.get("awaiting_coin_address"):
        return
    context.user_data["awaiting_coin_address"] = False
    address = (update.effective_message.text or "").strip()
    lang = get_lang(context)
    if not (32 <= len(address) <= 44 and re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]+", address)):
        await update.effective_message.reply_text(
            "⚠️ Send a valid Solana contract address (base58, usually 32–44 characters).",
            reply_markup=main_keyboard(lang))
        return
    status = await update.effective_message.reply_text("🔎 Checking the address on DexScreener…")
    token = await asyncio.to_thread(fetch_token_for_submission, address)
    if not token:
        await status.edit_text(
            "❌ DexScreener did not return a Solana trading pair for this address. "
            "Check the contract address and try again.", reply_markup=main_keyboard(lang))
        return
    if not await asyncio.to_thread(save_community_coin, token, update.effective_user.id):
        await status.edit_text(
            f"ℹ️ <b>{html.escape(token['name'])} (${html.escape(token['symbol'])})</b> is already in the community list.\n"
            f"📋 <code>{html.escape(address)}</code>\n🔗 {html.escape(token['url'])}",
            parse_mode="HTML", reply_markup=main_keyboard(lang), disable_web_page_preview=True)
        return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(T[lang]["rating"], callback_data=f"rate:{address}")],
        [InlineKeyboardButton("📈 Open DexScreener", url=token["url"])],
        [InlineKeyboardButton(T[lang]["rating"], callback_data="rating")]])
    await status.edit_text(
        "✅ <b>Coin added to the community list</b>\n\n"
        f"🪙 <b>{html.escape(token['name'])} (${html.escape(token['symbol'])})</b>\n"
        f"📋 <b>Contract address:</b>\n<code>{html.escape(address)}</code>\n"
        "📡 Source: DexScreener pair data\n⭐ Community members can rate it from 1 to 5.",
        parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)

async def show_community_ratings(bot, chat_id, lang, user_id=None):
    rows = await asyncio.to_thread(get_community_ratings, 10)
    if not rows:
        await bot.send_message(chat_id=chat_id,
            text="⭐ No community coins yet. Use ➕ Submit a meme coin to add the first one.",
            reply_markup=main_keyboard(lang))
        return
    await bot.send_message(chat_id=chat_id,
        text="⭐ <b>COMMUNITY MEME COIN RATINGS</b>\n📡 Token names and links: DexScreener. Scores: community votes.",
        parse_mode="HTML", reply_markup=main_keyboard(lang))
    for index, row in enumerate(rows, 1):
        avg = row["average_rating"]
        rounded = int(round(avg or 0))
        stars = "⭐" * rounded + "☆" * (5 - rounded)
        avg_text = f"{avg:.2f}/5" if avg is not None else T[lang]["empty"]
        address = row["address"]
        buttons = [
            [InlineKeyboardButton("⭐ Rate 1–5", callback_data=f"rate:{address}")],
            [InlineKeyboardButton("📈 Open DexScreener", url=row["url"])],
        ]
        if user_id is not None and is_configured_admin(user_id):
            buttons.append([InlineKeyboardButton("🗑️ Admin: Remove coin", callback_data=f"deletecoin:{address}")])
        buttons.append([InlineKeyboardButton(hot_ui(lang, "home"), callback_data="home")])
        keyboard = InlineKeyboardMarkup(buttons)
        msg = (f"<b>{index}. {html.escape(row['name'])} (${html.escape(row['symbol'])})</b>\n"
               "━━━━━━━━━━━━━━━━━━\n"
               f"🏆 Rating: <b>{avg_text}</b> {stars}\n"
               f"🗳 Votes: <b>{row['votes']}</b>\n"
               f"📋 Contract address:\n<code>{html.escape(address)}</code>\n"
               "📡 Market source: DexScreener | Rating source: community votes")
        await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML",
                               reply_markup=keyboard, disable_web_page_preview=True)


async def callback_impl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return

    # Acknowledge the Telegram button quickly, but do not discard the action if
    # Telegram rejects the acknowledgement (e.g. a stale query or transient network error).
    await safe_answer_callback(query)
    data = query.data or ""

    if data.startswith("lang:"):
        lang = data.split(":", 1)[1]
        if lang not in LANGUAGES:
            lang = "en"
        context.user_data["lang"] = lang
        try:
            await query.edit_message_text(
                T[lang]["welcome"], parse_mode="HTML", reply_markup=main_keyboard(lang)
            )
        except BadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                logger.warning("Could not update language message: %s", exc)
        return

    lang = get_lang(context)

    if data == "home":
        context.user_data["awaiting_coin_address"] = False
        try:
            await query.edit_message_text(T[lang]["welcome"], parse_mode="HTML", reply_markup=main_keyboard(lang))
        except BadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await context.bot.send_message(chat_id=query.message.chat_id, text=T[lang]["welcome"],
                                               parse_mode="HTML", reply_markup=main_keyboard(lang))
        return

    if data == "zombcash":
        chat_id = query.message.chat_id
        try:
            await query.edit_message_text("🧟 Loading $ZOMBCASH market data…")
        except BadRequest:
            pass
        try:
            market = await asyncio.to_thread(fetch_zombcash_market)
            change = market.get("priceChange") or {}
            change24 = float(change.get("h24") or 0)
            direction = "🟢" if change24 > 0 else ("🔴" if change24 < 0 else "⚪")
            sign = "+" if change24 > 0 else ""
            volume = market.get("volume") or {}
            liquidity = market.get("liquidity") or {}
            liquidity_value = market.get("liquidityUsd")
            if liquidity_value is None:
                liquidity_value = liquidity.get("usd")
            liquidity_text = market_money(liquidity_value) if liquidity_value is not None else "Not reported by DexScreener"
            cap_value = market.get("marketCap") or market.get("fdv")
            updated = int(market.get("updatedAt") or 0)
            updated_text = time.strftime("%H:%M:%S UTC", time.gmtime(updated)) if updated else "—"
            freshness = "⚠️ Cached/stale snapshot" if market.get("stale") else "🟢 API snapshot fetched"
            dex_name = html.escape(str(market.get("dexId") or "Solana DEX"))
            message = (
                "🧟 <b>ZOMBCASH MARKET</b> · <b>$ZOMBCASH</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"💵 <b>Price:</b> {price_text(float(market['priceUsd']))}\n"
                f"{direction} <b>24h change:</b> {sign}{change24:.2f}%\n"
                f"💰 <b>24h volume:</b> {market_money(volume.get('h24'))}\n"
                f"💧 <b>Liquidity:</b> {liquidity_text}\n"
                f"📊 <b>Market cap / FDV:</b> {market_money(cap_value)}\n"
                f"🏦 <b>Trading pair:</b> {dex_name}\n"
                f"🕒 <b>API snapshot:</b> {updated_text}\n"
                f"{freshness}\n\n"
                f"📋 <b>Contract:</b>\n<code>{html.escape(market.get('address', ''))}</code>\n\n"
                "📡 Source: shared ZombieCash API · DexScreener\n"
                f"{T[lang]['disclaimer']}"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📈 Open DexScreener", url=market.get("url") or "https://dexscreener.com/solana")],
                [InlineKeyboardButton("🛒 Buy on Pump.fun", url="https://pump.fun/coin/6cCBF9gtyp3c6VmyxUWzETk3yFmxhjvPm15cxicfpump")],
                [InlineKeyboardButton(hot_ui(lang, "home"), callback_data="home")],
            ])
            await context.bot.send_message(
                chat_id=chat_id, text=message, parse_mode="HTML",
                reply_markup=keyboard, disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("Could not load ZombieCash market data from shared API")
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ $ZOMBCASH market data is temporarily unavailable. Other Meme Radar features are unaffected.",
                reply_markup=main_keyboard(lang),
            )
        return

    if data.startswith("deletecoin:"):
        address = data.split(":", 1)[1]
        if not is_configured_admin(query.from_user.id):
            await query.answer("Admin permission required.", show_alert=True)
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yes, delete", callback_data=f"confirmdelete:{address}"),
            InlineKeyboardButton("❌ Cancel", callback_data="rating"),
        ], [InlineKeyboardButton(hot_ui(lang, "home"), callback_data="home")]])
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"🗑️ <b>Remove this coin from Community Ratings?</b>\n\n<code>{html.escape(address)}</code>\n\nIts community votes will also be deleted. This cannot be undone.",
            parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)
        return

    if data.startswith("confirmdelete:"):
        address = data.split(":", 1)[1]
        if not is_configured_admin(query.from_user.id):
            await query.answer("Admin permission required.", show_alert=True)
            return
        removed = await asyncio.to_thread(delete_community_coin, address)
        msg = "✅ Coin and its votes were removed from Community Ratings." if removed else "ℹ️ This coin is already absent from Community Ratings."
        await context.bot.send_message(chat_id=query.message.chat_id, text=msg, reply_markup=main_keyboard(lang))
        return

    if data == "addcoin":
        context.user_data["awaiting_coin_address"] = True
        try:
            await query.edit_message_text(
                "➕ <b>Submit a Solana meme coin</b>\\n\\n"
                "Send the token contract address in your next message. "
                "The bot will check whether DexScreener has a Solana trading pair.",
                parse_mode="HTML", reply_markup=main_keyboard(lang))
        except BadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                logger.warning("Could not show add coin prompt: %s", exc)
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="➕ Send the Solana meme coin contract address in your next message."
                )
        except Exception:
            logger.exception("Add coin button failed")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="➕ Send the Solana meme coin contract address in your next message."
            )
        return

    if data == "community_ai":
        chat_id = query.message.chat_id
        status = await context.bot.send_message(
            chat_id=chat_id, text=T[lang]["ai_loading"], reply_markup=main_keyboard(lang)
        )
        try:
            result = await asyncio.to_thread(gemini_community_recommendation, lang)
            await status.edit_text(
                result, parse_mode="HTML", disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(T[lang]["rating"], callback_data="rating")],
                    [InlineKeyboardButton(T[lang]["addcoin"], callback_data="addcoin")],
                    [InlineKeyboardButton(hot_ui(lang, "home"), callback_data="home")],
                ])
            )
        except Exception:
            logger.exception("Community AI recommendation failed")
            try:
                await status.edit_text(T[lang]["ai_error"], reply_markup=main_keyboard(lang))
            except TelegramError:
                await context.bot.send_message(chat_id=chat_id, text=T[lang]["ai_error"],
                                               reply_markup=main_keyboard(lang))
        return

    if data == "rating":
        try:
            await show_community_ratings(context.bot, query.message.chat_id, lang, query.from_user.id)
        except Exception:
            logger.exception("Community ratings button failed")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="⚠️ Не удалось открыть рейтинг. Ошибка записана в журнал CMD. Попробуй ещё раз."
            )
        return

    if data.startswith("rate:"):
        address = data.split(":", 1)[1]
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("⭐ 1", callback_data=f"vote:{address}:1"),
            InlineKeyboardButton("⭐ 2", callback_data=f"vote:{address}:2"),
            InlineKeyboardButton("⭐ 3", callback_data=f"vote:{address}:3"),
            InlineKeyboardButton("⭐ 4", callback_data=f"vote:{address}:4"),
            InlineKeyboardButton("⭐ 5", callback_data=f"vote:{address}:5")],
            [InlineKeyboardButton(hot_ui(lang, "home"), callback_data="home")]])
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"⭐ Rate this coin from 1 to 5 stars. One current rating per Telegram user.\\n\\n<code>{html.escape(address)}</code>",
            parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)
        return

    if data.startswith("vote:"):
        try:
            _, address, rating_text = data.split(":", 2)
            rating = int(rating_text)
        except (ValueError, TypeError):
            return
        try:
            ok, result = await asyncio.to_thread(cast_community_vote, address, query.from_user.id, rating)
            if ok:
                message = "✅ Your rating was saved." if result == "added" else "✅ Your rating was updated."
            else:
                message = T[lang]["empty"]
            await context.bot.send_message(chat_id=query.message.chat_id, text=message,
                                           reply_markup=main_keyboard(lang))
        except Exception:
            logger.exception("Community vote button failed")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="⚠️ Не удалось сохранить голос. Проверь журнал CMD."
            )
        return

    if data == "languages":
        try:
            await query.edit_message_text(
                T[lang]["language"], reply_markup=language_keyboard(lang)
            )
        except BadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                logger.warning("Could not show language menu: %s", exc)
        return

    if data == "ai":
        rows = [
            [InlineKeyboardButton(hot_ui(lang, "p5"), callback_data="ai:m5")],
            [InlineKeyboardButton(hot_ui(lang, "p1"), callback_data="ai:h1")],
            [InlineKeyboardButton(hot_ui(lang, "p24"), callback_data="ai:h24")],
            [InlineKeyboardButton(hot_ui(lang, "home"), callback_data="home")],
        ]
        prompts = {
            "en": "🧠 <b>AI Recommendations</b>\\nChoose the period to find one top coin:",
            "ru": "🧠 <b>Рекомендации ИИ</b>\\nВыберите период для поиска одной лучшей монеты:",
            "uk": "🧠 <b>Рекомендації ШІ</b>\\nОберіть період для пошуку однієї найкращої монети:",
            "tr": "🧠 <b>AI önerileri</b>\\nEn iyi tek coin için dönemi seçin:",
            "zh": "🧠 <b>AI 推荐</b>\\n请选择时间段，AI 将选出一枚最佳代币：",
            "ja": "🧠 <b>AIおすすめ</b>\\n最有力コインを1つ選ぶ期間を指定してください：",
        }
        await query.edit_message_text(prompts.get(lang, prompts["en"]), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "hot":
        labels = {
            "en": (hot_ui(lang, "p5"), hot_ui(lang, "p1"), hot_ui(lang, "p24"), hot_ui(lang, "home")),
            "ru": (hot_ui(lang, "p5"), hot_ui(lang, "p1"), hot_ui(lang, "p24"), hot_ui(lang, "home")),
            "uk": (hot_ui(lang, "p5"), hot_ui(lang, "p1"), hot_ui(lang, "p24"), hot_ui(lang, "home")),
            "ja": (hot_ui(lang, "p5"), hot_ui(lang, "p1"), hot_ui(lang, "p24"), hot_ui(lang, "home")),
            "zh": (hot_ui(lang, "p5"), hot_ui(lang, "p1"), hot_ui(lang, "p24"), hot_ui(lang, "home")),
            "tr": (hot_ui(lang, "p5"), hot_ui(lang, "p1"), hot_ui(lang, "p24"), hot_ui(lang, "home")),
        }
        p = labels.get(lang, labels["en"])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(p[0], callback_data="hot:m5")],
            [InlineKeyboardButton(p[1], callback_data="hot:h1")],
            [InlineKeyboardButton(p[2], callback_data="hot:h24")],
            [InlineKeyboardButton(p[3], callback_data="home")],
        ])
        await query.edit_message_text(
            hot_ui(lang, "period"),
            parse_mode="HTML", reply_markup=keyboard)
        return

    is_hot_period = data.startswith("hot:")
    is_ai_period = data.startswith("ai:")
    if not is_hot_period and not is_ai_period:
        return
    period = data.split(":", 1)[1]
    if period not in ("m5", "h1", "h24"):
        return
    context.user_data["hot_period"] = period
    context.user_data["ai_period"] = period
    is_ai = is_ai_period
    status = T[lang]["ai_loading"] if is_ai else T[lang]["loading"]
    chat_id = query.message.chat_id

    try:
        await query.edit_message_text(status)
    except BadRequest as exc:
        logger.warning("Could not edit status message: %s", exc)
        try:
            await context.bot.send_message(chat_id=chat_id, text=status)
        except TelegramError:
            return

    try:
        # AI uses the same five candidates and time window selected in Hot Solana Coins.
        tokens = await asyncio.to_thread(get_hot_tokens_cached, period, 5)
        if not tokens:
            await context.bot.send_message(
                chat_id=chat_id, text=T[lang]["empty"], reply_markup=main_keyboard(lang)
            )
            return

        if not is_ai:
            # Primary scanner: AI score each candidate, then rank #1 by highest total score.
            tokens = await asyncio.to_thread(gemini_score_scanner_tokens, tokens, lang, period)

        if is_ai:
            # Keep the separate AI Recommendations menu and its single-winner explanation unchanged.
            report = await asyncio.to_thread(gemini_analyze, tokens, lang, period)
            await send_long_text(context.bot, chat_id, f"{T[lang]['report']}\n\n{report}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"{T[lang]['ai_note']}\n\n{T[lang]['disclaimer']}",
                reply_markup=main_keyboard(lang),
            )
            return

        await show_tokens(context.bot, chat_id, tokens, lang, period)
        labels = {
            "en": (hot_ui(lang, "choose"), hot_ui(lang, "s5"), hot_ui(lang, "s1"), hot_ui(lang, "s24"), hot_ui(lang, "home")),
            "ru": (hot_ui(lang, "choose"), hot_ui(lang, "s5"), hot_ui(lang, "s1"), hot_ui(lang, "s24"), hot_ui(lang, "home")),
            "uk": (hot_ui(lang, "choose"), hot_ui(lang, "s5"), hot_ui(lang, "s1"), hot_ui(lang, "s24"), hot_ui(lang, "home")),
            "ja": (hot_ui(lang, "choose"), hot_ui(lang, "s5"), hot_ui(lang, "s1"), hot_ui(lang, "s24"), hot_ui(lang, "home")),
            "zh": (hot_ui(lang, "choose"), hot_ui(lang, "s5"), hot_ui(lang, "s1"), hot_ui(lang, "s24"), hot_ui(lang, "home")),
            "tr": (hot_ui(lang, "choose"), hot_ui(lang, "s5"), hot_ui(lang, "s1"), hot_ui(lang, "s24"), hot_ui(lang, "home")),
        }
        p = labels.get(lang, labels["en"])
        await context.bot.send_message(
            chat_id=chat_id, text=p[0],
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(p[1], callback_data="hot:m5"),
                 InlineKeyboardButton(p[2], callback_data="hot:h1"),
                 InlineKeyboardButton(p[3], callback_data="hot:h24")],
                [InlineKeyboardButton(p[4], callback_data="home")],
            ])
        )

    except Exception as exc:
        logger.exception("Market or Gemini request failed")
        error_text = T[lang]["ai_error"] if is_ai else T[lang]["market_error"]
        # Keep technical diagnostics in server logs; show a clean message in Telegram.
        try:
            await context.bot.send_message(
                chat_id=chat_id, text=error_text, reply_markup=main_keyboard(lang)
            )
        except TelegramError:
            logger.exception("Could not send user-facing error message")


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await callback_impl(update, context)
    except Exception:
        logger.exception("Unhandled button callback failure")
        query = update.callback_query
        if query and query.message:
            try:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="⚠️ Ошибка обработки кнопки. Подробности записаны в журнал CMD."
                )
            except Exception:
                logger.exception("Could not send callback error notice")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    if error is None:
        return
    logger.error(
        "Unhandled Telegram update error",
        exc_info=(type(error), error, error.__traceback__),
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("ZOMBCASH_BOT_TOKEN is missing in this CMD window.")
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY is missing. AI analysis will not work.")

    app = Application.builder().token(BOT_TOKEN).build()
    init_db()
    logger.info("Community coins database initialized: %s", DB_PATH)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("post_radar", post_radar))
    app.add_handler(CommandHandler("radar_button", post_radar_group))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_error_handler(error_handler)

    logger.info("ZombieCash Meme Radar starting")
    app.run_polling()


if __name__ == "__main__":
    main()
