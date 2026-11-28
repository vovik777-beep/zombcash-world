from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from datetime import datetime, timezone

from aiohttp import ClientSession, ClientTimeout

log = logging.getLogger("zombcash.sniper.paper_prices")
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
SOL_MINT = "So11111111111111111111111111111111111111112"
DEXSCREENER_TOKEN_PAIRS_URL = "https://api.dexscreener.com/token-pairs/v1/solana/{}"
JUPITER_PRICE_URL = "https://api.jup.ag/price/v3"
GECKOTERMINAL_SIMPLE_URL = "https://api.geckoterminal.com/api/v2/simple/networks/solana/token_price/{}"
GECKOTERMINAL_TOKEN_POOLS_URL = "https://api.geckoterminal.com/api/v2/networks/solana/tokens/{}/pools"
GECKOTERMINAL_MULTI_TOKEN_POOLS_URL = "https://api.geckoterminal.com/api/v2/networks/solana/tokens/multi/{}"
MAX_GECKO_POOL_LOOKUPS_PER_CYCLE = 4
PAPER_GECKO_POOL_BATCH_SIZE = 30  # GeckoTerminal multi-token endpoint accepts up to 30 token addresses.
MAX_LIVE_GECKO_POOL_LOOKUPS_PER_CYCLE = 4
MAX_LIVE_MINTS_PER_CYCLE = 60
DEXSCREENER_TOKEN_BATCH_URL = "https://api.dexscreener.com/tokens/v1/solana/{}"
MIN_LIQUIDITY_USD = 500.0
MAX_MINTS_PER_CYCLE = 60  # Covers three Paper profiles with 20 open positions each.
PAPER_POLL_INTERVAL_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 60.0  # Keep the Live price worker on its existing cadence.
REQUEST_SPACING_SECONDS = 1.1


def select_gecko_pool_mints(mints, offset=0, limit=MAX_GECKO_POOL_LOOKUPS_PER_CYCLE):
    """Select a bounded, duplicate-free round-robin slice of unpriced mints."""
    if not mints:
        return [], 0
    count = min(len(mints), max(0, int(limit)))
    start = int(offset) % len(mints)
    selected = [mints[(start + index) % len(mints)] for index in range(count)]
    return selected, (start + count) % len(mints)


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def select_price_feed_mints(mints, offset=0, limit=MAX_MINTS_PER_CYCLE):
    """Select a bounded round-robin slice so large open-position sets are not starved."""
    if not mints:
        return [], 0
    count = min(len(mints), max(0, int(limit)))
    start = int(offset) % len(mints)
    selected = [mints[(start + index) % len(mints)] for index in range(count)]
    return selected, (start + count) % len(mints)


def normalize_dexscreener_paper_mark(pairs, mint: str, observed_at_ms: int | None = None):
    """Return the most liquid SOL-quoted pair for mint; reject weak or ambiguous markets."""
    if not MINT_RE.fullmatch(str(mint or "")) or not isinstance(pairs, list):
        return None
    candidates = []
    for pair in pairs:
        if not isinstance(pair, dict) or pair.get("chainId") != "solana":
            continue
        base = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
        quote = pair.get("quoteToken") if isinstance(pair.get("quoteToken"), dict) else {}
        base_address = str(base.get("address") or "")
        quote_address = str(quote.get("address") or "")
        raw_price = _number(pair.get("priceNative"))
        liquidity = _number((pair.get("liquidity") or {}).get("usd"))
        if raw_price is None or raw_price <= 0 or liquidity is None or liquidity < MIN_LIQUIDITY_USD:
            continue
        if base_address == mint and quote_address == SOL_MINT:
            price_sol = raw_price
        elif quote_address == mint and base_address == SOL_MINT:
            price_sol = 1.0 / raw_price
        else:
            continue
        if not math.isfinite(price_sol) or price_sol <= 0:
            continue
        pair_address = str(pair.get("pairAddress") or "").strip()
        if not pair_address:
            continue
        candidates.append((liquidity, pair_address, price_sol))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], row[1]), reverse=True)
    liquidity, pair_address, price_sol = candidates[0]
    return {
        "mint": mint,
        "source": "dexscreener",
        "priceSol": price_sol,
        "liquidityUsd": liquidity,
        "pairAddress": pair_address,
        "observedAt": int(observed_at_ms or time.time() * 1000),
    }


def normalize_jupiter_paper_mark(prices, mint: str, observed_at_ms: int | None = None):
    """Convert Jupiter's USD prices to SOL using the same response's SOL/USD anchor."""
    if not isinstance(prices, dict):
        return None
    token = prices.get(mint)
    sol = prices.get(SOL_MINT)
    if not isinstance(token, dict) or not isinstance(sol, dict):
        return None
    usd_price = _number(token.get("usdPrice"))
    sol_usd = _number(sol.get("usdPrice"))
    liquidity = _number(token.get("liquidity"))
    if usd_price is None or usd_price <= 0 or sol_usd is None or sol_usd <= 0:
        return None
    if liquidity is None or liquidity < MIN_LIQUIDITY_USD:
        return None
    price_sol = usd_price / sol_usd
    if not math.isfinite(price_sol) or price_sol <= 0:
        return None
    return {
        "mint": mint,
        "source": "jupiter",
        "priceSol": price_sol,
        "liquidityUsd": liquidity,
        "pairAddress": "jupiter-price-v3",
        "observedAt": int(observed_at_ms or time.time() * 1000),
    }


async def _fetch_jupiter_prices(session: ClientSession, mints: list[str]):
    """One keyless batch call; Jupiter documents 0.5 requests/sec without an API key."""
    ids = list(dict.fromkeys([*mints, SOL_MINT]))[:50]
    async with session.get(
        JUPITER_PRICE_URL,
        params={"ids": ",".join(ids)},
        headers={"Accept": "application/json", "User-Agent": "ZombieCash-PaperPrice/1.0"},
    ) as response:
        if response.status != 200:
            raise RuntimeError(f"Jupiter Price API HTTP {response.status}")
        payload = await response.json(content_type=None)
    return payload if isinstance(payload, dict) else {}


def normalize_geckoterminal_paper_mark(payload, mint: str, observed_at_ms: int | None = None):
    """Convert GeckoTerminal's batched USD price and reserve into a validated SOL mark."""
    if not MINT_RE.fullmatch(str(mint or "")) or not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    attrs = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
    prices = attrs.get("token_prices") if isinstance(attrs.get("token_prices"), dict) else {}
    reserves = attrs.get("total_reserve_in_usd") if isinstance(attrs.get("total_reserve_in_usd"), dict) else {}
    token_usd = _number(prices.get(mint))
    sol_usd = _number(prices.get(SOL_MINT))
    liquidity = _number(reserves.get(mint))
    if token_usd is None or token_usd <= 0 or sol_usd is None or sol_usd <= 0:
        return None
    if liquidity is None or liquidity < MIN_LIQUIDITY_USD:
        return None
    price_sol = token_usd / sol_usd
    if not math.isfinite(price_sol) or price_sol <= 0:
        return None
    return {
        "mint": mint,
        "source": "geckoterminal",
        "priceSol": price_sol,
        "liquidityUsd": liquidity,
        "pairAddress": "geckoterminal-simple-price",
        "observedAt": int(observed_at_ms or time.time() * 1000),
    }


async def _fetch_geckoterminal_prices(session: ClientSession, mints: list[str]):
    """Fetch up to 30 token prices plus SOL in one free public API request."""
    ids = list(dict.fromkeys([*mints, SOL_MINT]))[:30]
    url = GECKOTERMINAL_SIMPLE_URL.format(",".join(ids))
    async with session.get(
        url,
        params={"include_total_reserve_in_usd": "true", "include_inactive_source": "false"},
        headers={"Accept": "application/json;version=20230203", "User-Agent": "ZombieCash-PaperPrice/1.0"},
    ) as response:
        if response.status != 200:
            raise RuntimeError(f"GeckoTerminal HTTP {response.status}")
        payload = await response.json(content_type=None)
    return payload if isinstance(payload, dict) else {}


def _geckoterminal_token_id_matches(token_id, mint: str):
    value = str(token_id or "")
    return value == mint or value.endswith("_" + mint)


def normalize_geckoterminal_pool_mark(payload, mint: str, observed_at_ms: int | None = None):
    """Choose a liquid GeckoTerminal pool and read the token price in Solana native currency."""
    if not MINT_RE.fullmatch(str(mint or "")) or not isinstance(payload, dict):
        return None
    pools = payload.get("data") if isinstance(payload.get("data"), list) else []
    candidates = []
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        attrs = pool.get("attributes") if isinstance(pool.get("attributes"), dict) else {}
        relationships = pool.get("relationships") if isinstance(pool.get("relationships"), dict) else {}
        base = relationships.get("base_token") if isinstance(relationships.get("base_token"), dict) else {}
        quote = relationships.get("quote_token") if isinstance(relationships.get("quote_token"), dict) else {}
        base_data = base.get("data") if isinstance(base.get("data"), dict) else {}
        quote_data = quote.get("data") if isinstance(quote.get("data"), dict) else {}
        base_id = base_data.get("id")
        quote_id = quote_data.get("id")
        if _geckoterminal_token_id_matches(base_id, mint):
            price_sol = _number(attrs.get("base_token_price_native_currency"))
        elif _geckoterminal_token_id_matches(quote_id, mint):
            price_sol = _number(attrs.get("quote_token_price_native_currency"))
        else:
            continue
        liquidity = _number(attrs.get("reserve_in_usd"))
        pool_address = str(attrs.get("address") or "").strip()
        if price_sol is None or price_sol <= 0 or liquidity is None or liquidity < MIN_LIQUIDITY_USD:
            continue
        if not pool_address or not MINT_RE.fullmatch(pool_address):
            continue
        candidates.append((liquidity, pool_address, price_sol))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], row[1]), reverse=True)
    liquidity, pool_address, price_sol = candidates[0]
    return {
        "mint": mint,
        "source": "geckoterminal",
        "priceSol": price_sol,
        "liquidityUsd": liquidity,
        "pairAddress": pool_address,
        "observedAt": int(observed_at_ms or time.time() * 1000),
    }


def geckoterminal_pool_diagnostics(payload, mint: str):
    """Summarize GeckoTerminal pool coverage without changing mark eligibility."""
    pools = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), list) else []
    matching = 0
    liquidity_values = []
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        attrs = pool.get("attributes") if isinstance(pool.get("attributes"), dict) else {}
        relationships = pool.get("relationships") if isinstance(pool.get("relationships"), dict) else {}
        base = relationships.get("base_token") if isinstance(relationships.get("base_token"), dict) else {}
        quote = relationships.get("quote_token") if isinstance(relationships.get("quote_token"), dict) else {}
        base_data = base.get("data") if isinstance(base.get("data"), dict) else {}
        quote_data = quote.get("data") if isinstance(quote.get("data"), dict) else {}
        if not (
            _geckoterminal_token_id_matches(base_data.get("id"), mint)
            or _geckoterminal_token_id_matches(quote_data.get("id"), mint)
        ):
            continue
        matching += 1
        liquidity = _number(attrs.get("reserve_in_usd"))
        if liquidity is not None:
            liquidity_values.append(liquidity)
    return {
        "pool_count": len(pools),
        "matching_pool_count": matching,
        "max_liquidity_usd": max(liquidity_values) if liquidity_values else None,
    }


def classify_gecko_pool_result(diagnostics):
    """Classify why a GeckoTerminal pool response did not produce an eligible mark."""
    matching = int((diagnostics or {}).get("matching_pool_count") or 0)
    if matching <= 0:
        return "no_pool"
    max_liquidity = _number((diagnostics or {}).get("max_liquidity_usd"))
    if max_liquidity is not None and max_liquidity < MIN_LIQUIDITY_USD:
        return "below_liquidity_threshold"
    return "pool_mark_rejected"


def geckoterminal_pools_by_mint(payload, mints):
    """Split a GeckoTerminal multi-token response into per-token top-pool payloads."""
    if not isinstance(payload, dict):
        return {mint: {"data": []} for mint in mints}
    tokens = payload.get("data") if isinstance(payload.get("data"), list) else []
    included = payload.get("included") if isinstance(payload.get("included"), list) else []
    pools_by_id = {
        str(pool.get("id")): pool
        for pool in included
        if isinstance(pool, dict) and pool.get("id")
    }
    requested = set(mints)
    result = {mint: {"data": []} for mint in mints}
    for token in tokens:
        if not isinstance(token, dict):
            continue
        attrs = token.get("attributes") if isinstance(token.get("attributes"), dict) else {}
        mint = str(attrs.get("address") or "").strip()
        if mint not in requested:
            continue
        relationships = token.get("relationships") if isinstance(token.get("relationships"), dict) else {}
        top_pools = relationships.get("top_pools") if isinstance(relationships.get("top_pools"), dict) else {}
        top_pool_rows = top_pools.get("data") if isinstance(top_pools.get("data"), list) else []
        pool_ids = [str(row.get("id")) for row in top_pool_rows if isinstance(row, dict) and row.get("id")]
        result[mint] = {"data": [pools_by_id[pool_id] for pool_id in pool_ids if pool_id in pools_by_id]}
    return result


GECKO_RATE_LIMIT_COOLDOWN_SECONDS = 60


def is_gecko_rate_limit_error(exc):
    """Identify GeckoTerminal HTTP 429 errors without matching unrelated failures."""
    return "HTTP 429" in str(exc)


MAX_TRADE_FEED_MARK_AGE_MS = 120_000


def normalize_trade_feed_paper_mark(payload, mint: str, now_ms: int | None = None):
    """Build a price mark only from a recent, positive SOL-denominated ingested trade."""
    if not MINT_RE.fullmatch(str(mint or "")) or not isinstance(payload, dict):
        return None
    if str(payload.get("mint") or "") != mint:
        return None
    source = str(payload.get("source") or "").lower()
    if source not in {"shrine", "pumpdev", "pumpportal", "helius_standard_ws"}:
        return None
    price_sol = _number(payload.get("priceSol"))
    observed_at = _number(payload.get("eventAt"))
    side = str(payload.get("side") or "").lower()
    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    if price_sol is None or price_sol <= 0 or observed_at is None or observed_at <= 0:
        return None
    if side not in {"buy", "sell"}:
        return None
    if observed_at < current_ms - MAX_TRADE_FEED_MARK_AGE_MS or observed_at > current_ms + 5_000:
        return None
    return {
        "mint": mint,
        "source": "trade_feed",
        "priceSol": price_sol,
        "liquidityUsd": 0,
        "pairAddress": f"trade-feed:{source}:{side}",
        "observedAt": int(observed_at),
    }


async def _fetch_geckoterminal_token_pools(session: ClientSession, mint: str):
    url = GECKOTERMINAL_TOKEN_POOLS_URL.format(mint)
    async with session.get(
        url,
        params={"include": "base_token,quote_token", "sort": "h24_volume_usd_desc"},
        headers={"Accept": "application/json;version=20230203", "User-Agent": "ZombieCash-PaperPrice/1.0"},
    ) as response:
        # No indexed pools is a normal no-quote result for long-tail or inactive tokens.
        if response.status == 404:
            return {"data": []}
        if response.status != 200:
            raise RuntimeError(f"GeckoTerminal token pools HTTP {response.status}")
        payload = await response.json(content_type=None)
    return payload if isinstance(payload, dict) else {}


async def _fetch_geckoterminal_multi_token_pools(session: ClientSession, mints: list[str]):
    """Fetch top pools for up to 30 Solana tokens in one public API request."""
    unique = list(dict.fromkeys(mint for mint in mints if MINT_RE.fullmatch(str(mint or ""))))
    if not unique:
        return {"data": [], "included": []}
    if len(unique) > 30:
        raise ValueError("GeckoTerminal multi-token request exceeds 30 addresses")
    url = GECKOTERMINAL_MULTI_TOKEN_POOLS_URL.format(",".join(unique))
    async with session.get(
        url,
        params={"include": "top_pools"},
        headers={"Accept": "application/json;version=20230203", "User-Agent": "ZombieCash-PaperPrice/1.0"},
    ) as response:
        if response.status == 404:
            return {"data": [], "included": []}
        if response.status != 200:
            raise RuntimeError(f"GeckoTerminal multi-token pools HTTP {response.status}")
        payload = await response.json(content_type=None)
    return payload if isinstance(payload, dict) else {"data": [], "included": []}


async def _fetch_token_pairs(session: ClientSession, mint: str):
    url = DEXSCREENER_TOKEN_PAIRS_URL.format(mint)
    async with session.get(url, headers={"Accept": "application/json", "User-Agent": "ZombieCash-PaperPrice/1.0"}) as response:
        if response.status != 200:
            raise RuntimeError(f"DexScreener HTTP {response.status}")
        payload = await response.json(content_type=None)
    return payload if isinstance(payload, list) else []


async def _fetch_token_pairs_batch(session: ClientSession, mints: list[str]):
    """Fetch DexScreener pairs in bounded batches of up to 30 mints."""
    result = {mint: [] for mint in mints}
    unique = list(dict.fromkeys(mints))
    for offset in range(0, len(unique), 30):
        batch = unique[offset:offset + 30]
        url = DEXSCREENER_TOKEN_BATCH_URL.format(",".join(batch))
        async with session.get(
            url,
            headers={"Accept": "application/json", "User-Agent": "ZombieCash-LivePrice/1.0"},
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"DexScreener batch HTTP {response.status}")
            payload = await response.json(content_type=None)
        batch_set = set(batch)
        for pair in payload if isinstance(payload, list) else []:
            if not isinstance(pair, dict) or pair.get("chainId") != "solana":
                continue
            base = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
            quote = pair.get("quoteToken") if isinstance(pair.get("quoteToken"), dict) else {}
            for address in (str(base.get("address") or ""), str(quote.get("address") or "")):
                if address in batch_set:
                    result[address].append(pair)
        if offset + 30 < len(unique):
            await asyncio.sleep(REQUEST_SPACING_SECONDS)
    return result


async def _fetch_jupiter_prices_batched(session: ClientSession, mints: list[str]):
    """Fetch all live-position prices without truncating Jupiter's 50-id request limit."""
    combined = {}
    unique = list(dict.fromkeys(mints))
    for offset in range(0, len(unique), 49):
        batch = unique[offset:offset + 49]
        combined.update(await _fetch_jupiter_prices(session, batch))
        if offset + 49 < len(unique):
            await asyncio.sleep(REQUEST_SPACING_SECONDS)
    return combined


async def _save_price_mark(supabase_request, mark):
    """Persist a validated mark for Paper positions only."""
    saved = await supabase_request(
        "POST",
        "rpc/sniper_paper_price_mark_upsert",
        payload={"p_mark": mark},
    )
    if not isinstance(saved, dict) or saved.get("ok") is not True:
        return False, False
    return True, False


async def _save_live_price_mark(supabase_request, mark):
    """Update open Live-position valuation from a validated SOL price mark."""
    if not isinstance(mark, dict):
        return 0
    mint = str(mark.get("mint") or "")
    price_sol = _number(mark.get("priceSol"))
    observed_at_ms = _number(mark.get("observedAt"))
    if not MINT_RE.fullmatch(mint) or price_sol is None or price_sol <= 0:
        return 0
    if observed_at_ms is None or observed_at_ms <= 0:
        return 0
    observed_at = datetime.fromtimestamp(int(observed_at_ms) / 1000, tz=timezone.utc).isoformat()
    result = await supabase_request(
        "POST",
        "rpc/sniper_live_position_mark",
        payload={"p_mint": mint, "p_price_sol": price_sol, "p_observed_at": observed_at},
    )
    try:
        return max(0, int(result or 0))
    except (TypeError, ValueError):
        return 0


async def live_price_mark_worker(supabase_request, state):
    """Refresh open Live-position marks; it never signs, submits, or broadcasts trades."""
    timeout = ClientTimeout(total=12, connect=5)
    while True:
        try:
            rows = await supabase_request(
                "POST",
                "rpc/sniper_open_live_position_mints",
                payload={},
            )
            mints = []
            seen = set()
            if isinstance(rows, list):
                for row in rows:
                    mint = str(row.get("mint") or "").strip() if isinstance(row, dict) else ""
                    if MINT_RE.fullmatch(mint) and mint not in seen:
                        seen.add(mint)
                        mints.append(mint)
            if not mints:
                state["livePriceFeedStatus"] = "waiting_for_open_positions"
                state["livePriceFeedMints"] = 0
                state["livePriceFeedTotalMints"] = 0
                state["livePriceFeedMintOffset"] = 0
                await asyncio.sleep(30)
                continue

            total_mints = len(mints)
            mints, next_offset = select_price_feed_mints(
                mints,
                state.get("livePriceFeedMintOffset", 0),
                MAX_LIVE_MINTS_PER_CYCLE,
            )
            state["livePriceFeedMintOffset"] = next_offset
            state["livePriceFeedTotalMints"] = total_mints

            updated = 0
            no_pair = 0
            errors = 0
            dex_covered = set()
            async with ClientSession(timeout=timeout) as session:
                pair_map = {}
                try:
                    pair_map = await _fetch_token_pairs_batch(session, mints)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    errors += 1
                    log.warning("[Sniper] Live DexScreener batch lookup failed (%s)", type(exc).__name__)

                for mint in mints:
                    mark = normalize_dexscreener_paper_mark(pair_map.get(mint, []), mint)
                    if mark is None:
                        continue
                    dex_covered.add(mint)
                    try:
                        updated += await _save_live_price_mark(supabase_request, mark)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        errors += 1
                        log.warning("[Sniper] Live DexScreener mark update failed (%s)", type(exc).__name__)

                missing_mints = [mint for mint in mints if mint not in dex_covered]
                jupiter_covered = set()
                if missing_mints:
                    try:
                        prices = await _fetch_jupiter_prices_batched(session, missing_mints)
                        observed_at_ms = int(time.time() * 1000)
                        for mint in missing_mints:
                            mark = normalize_jupiter_paper_mark(prices, mint, observed_at_ms)
                            if mark is None:
                                continue
                            jupiter_covered.add(mint)
                            updated += await _save_live_price_mark(supabase_request, mark)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        errors += 1
                        log.warning("[Sniper] Live Jupiter price fallback failed (%s)", type(exc).__name__)

                trade_covered = set()
                trade_mints = [mint for mint in missing_mints if mint not in jupiter_covered]
                for mint in trade_mints:
                    try:
                        latest_trade = await supabase_request(
                            "POST",
                            "rpc/sniper_trade_latest",
                            payload={"p_mint": mint},
                        )
                        mark = normalize_trade_feed_paper_mark(latest_trade, mint)
                        if mark is None:
                            continue
                        trade_covered.add(mint)
                        updated += await _save_live_price_mark(supabase_request, mark)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        errors += 1
                        log.warning("[Sniper] Live trade-feed price fallback failed (%s)", type(exc).__name__)

                gecko_mints = [mint for mint in trade_mints if mint not in trade_covered]
                if gecko_mints:
                    retry_after = float(state.get("paperPriceFeedGeckoRetryAfter", 0) or 0)
                    if time.time() < retry_after:
                        state["paperPriceFeedGeckoDeferred"] = len(gecko_mints)
                        log.info(
                            "[Sniper] GeckoTerminal Paper pool fallback paused after HTTP 429 retry_in=%ss deferred=%s",
                            max(0, int(retry_after - time.time())),
                            len(gecko_mints),
                        )
                    else:
                        # One request returns top pools for up to 30 tokens, leaving shared API headroom.
                        pool_mints, next_offset = select_gecko_pool_mints(
                            gecko_mints,
                            state.get("paperPriceFeedGeckoOffset", 0),
                            PAPER_GECKO_POOL_BATCH_SIZE,
                        )
                        state["paperPriceFeedGeckoOffset"] = next_offset
                        state["paperPriceFeedGeckoDeferred"] = len(gecko_mints) - len(pool_mints)
                        if pool_mints:
                            try:
                                batch_payload = await _fetch_geckoterminal_multi_token_pools(session, pool_mints)
                                pools_by_mint = geckoterminal_pools_by_mint(batch_payload, pool_mints)
                                for mint in pool_mints:
                                    pools = pools_by_mint.get(mint, {"data": []})
                                    diagnostics = geckoterminal_pool_diagnostics(pools, mint)
                                    mark = normalize_geckoterminal_pool_mark(pools, mint)
                                    if mark is None:
                                        reason = classify_gecko_pool_result(diagnostics)
                                        if reason == "no_pool":
                                            no_pair += 1
                                        elif reason == "below_liquidity_threshold":
                                            below_liquidity += 1
                                        else:
                                            rejected_pool += 1
                                        log.info(
                                            "[Sniper] GeckoTerminal Paper pool lookup rejected mint=%s reason=%s pools=%s matching=%s max_liquidity_usd=%s",
                                            mint,
                                            reason,
                                            diagnostics["pool_count"],
                                            diagnostics["matching_pool_count"],
                                            diagnostics["max_liquidity_usd"],
                                        )
                                        continue
                                    saved_ok, live_updated = await _save_price_mark(supabase_request, mark)
                                    if not saved_ok:
                                        errors += 1
                                        log.warning("[Sniper] GeckoTerminal pool mark rejected by Supabase validation")
                                    else:
                                        updated += 1
                                        log.info(
                                            "[Sniper] GeckoTerminal Paper pool mark saved mint=%s pools=%s matching=%s liquidity_usd=%.2f",
                                            mint,
                                            diagnostics["pool_count"],
                                            diagnostics["matching_pool_count"],
                                            mark["liquidityUsd"],
                                        )
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                errors += 1
                                if is_gecko_rate_limit_error(exc):
                                    state["paperPriceFeedGeckoRetryAfter"] = time.time() + GECKO_RATE_LIMIT_COOLDOWN_SECONDS
                                    log.warning(
                                        "[Sniper] GeckoTerminal HTTP 429; pausing Paper pool fallback for %ss",
                                        GECKO_RATE_LIMIT_COOLDOWN_SECONDS,
                                    )
                                else:
                                    log.warning("[Sniper] GeckoTerminal multi-token pool fallback failed (%s: %s)", type(exc).__name__, str(exc)[:100])

            state["paperPriceFeedStatus"] = "active"
            state["paperPriceFeedMints"] = len(mints)
            state["paperPriceFeedUpdated"] = updated
            state["paperPriceFeedNoPair"] = no_pair
            state["paperPriceFeedBelowLiquidity"] = below_liquidity
            state["paperPriceFeedRejectedPool"] = rejected_pool
            state["paperPriceFeedErrors"] = errors
            state["paperPriceFeedLastAt"] = int(time.time() * 1000)
            log.info(
                "[Sniper] Paper price refresh complete: checked=%s updated=%s no_pool=%s below_liquidity=%s rejected_pool=%s errors=%s",
                len(mints), updated, no_pair, below_liquidity, rejected_pool, errors,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state["paperPriceFeedStatus"] = "error"
            state["paperPriceFeedError"] = type(exc).__name__
            log.warning("[Sniper] Paper price feed cycle failed (%s)", type(exc).__name__)
        await asyncio.sleep(PAPER_POLL_INTERVAL_SECONDS)
