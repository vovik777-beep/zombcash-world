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
MAX_GECKO_POOL_LOOKUPS_PER_CYCLE = 4
MIN_LIQUIDITY_USD = 500.0
MAX_MINTS_PER_CYCLE = 20
POLL_INTERVAL_SECONDS = 60.0
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


async def _fetch_token_pairs(session: ClientSession, mint: str):
    url = DEXSCREENER_TOKEN_PAIRS_URL.format(mint)
    async with session.get(url, headers={"Accept": "application/json", "User-Agent": "ZombieCash-PaperPrice/1.0"}) as response:
        if response.status != 200:
            raise RuntimeError(f"DexScreener HTTP {response.status}")
        payload = await response.json(content_type=None)
    return payload if isinstance(payload, list) else []


async def _save_price_mark(supabase_request, mark):
    saved = await supabase_request(
        "POST",
        "rpc/sniper_paper_price_mark_upsert",
        payload={"p_mark": mark},
    )
    if not isinstance(saved, dict) or saved.get("ok") is not True:
        return False, False
    try:
        observed_at = datetime.fromtimestamp(int(mark["observedAt"]) / 1000, tz=timezone.utc).isoformat()
        result = await supabase_request(
            "POST",
            "rpc/sniper_live_position_mark",
            payload={"p_mint": mark["mint"], "p_price_sol": mark["priceSol"], "p_observed_at": observed_at},
        )
        return True, bool(result)
    except Exception as exc:
        log.warning("[Sniper] Live position mark update failed (%s)", type(exc).__name__)
        return True, False


async def paper_price_mark_worker(supabase_request, state):
    """Refresh shared price marks for open Paper and Live positions; never trades."""
    timeout = ClientTimeout(total=12, connect=5)
    while True:
        try:
            rows = await supabase_request(
                "POST",
                "rpc/sniper_open_paper_position_mints",
                payload={},
            )
            live_rows = await supabase_request(
                "POST",
                "rpc/sniper_open_live_position_mints",
                payload={},
            )
            mints = []
            seen = set()
            for source_rows in (rows, live_rows):
                if isinstance(source_rows, list):
                    for row in source_rows:
                        mint = str(row.get("mint") or "").strip() if isinstance(row, dict) else ""
                        if MINT_RE.fullmatch(mint) and mint not in seen:
                            seen.add(mint)
                            mints.append(mint)
            if not mints:
                state["paperPriceFeedStatus"] = "waiting_for_open_positions"
                state["paperPriceFeedMints"] = 0
                await asyncio.sleep(30)
                continue

            updated = 0
            live_marked = 0
            no_pair = 0
            errors = 0
            dex_covered = set()
            async with ClientSession(timeout=timeout) as session:
                for index, mint in enumerate(mints):
                    try:
                        pairs = await _fetch_token_pairs(session, mint)
                        mark = normalize_dexscreener_paper_mark(pairs, mint)
                        if mark is not None:
                            saved_ok, live_updated = await _save_price_mark(supabase_request, mark)
                            if not saved_ok:
                                errors += 1
                                log.warning("[Sniper] DexScreener price mark rejected by Supabase validation")
                            else:
                                updated += 1
                                live_marked += int(live_updated)
                                dex_covered.add(mint)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        errors += 1
                        log.warning("[Sniper] DexScreener Paper price refresh failed (%s)", type(exc).__name__)
                    if index < len(mints) - 1:
                        await asyncio.sleep(REQUEST_SPACING_SECONDS)

                # Batch fallback for tokens without an eligible DexScreener pair.
                # Jupiter is preferred; GeckoTerminal is a final keyless batch source.
                missing_mints = [mint for mint in mints if mint not in dex_covered]
                jupiter_covered = set()
                if missing_mints:
                    try:
                        prices = await _fetch_jupiter_prices(session, missing_mints)
                        observed_at_ms = int(time.time() * 1000)
                        for mint in missing_mints:
                            mark = normalize_jupiter_paper_mark(prices, mint, observed_at_ms)
                            if mark is None:
                                continue
                            saved_ok, live_updated = await _save_price_mark(supabase_request, mark)
                            if not saved_ok:
                                errors += 1
                                log.warning("[Sniper] Jupiter price mark rejected by Supabase validation")
                            else:
                                updated += 1
                                live_marked += int(live_updated)
                                jupiter_covered.add(mint)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        errors += 1
                        log.warning("[Sniper] Jupiter Paper price fallback failed (%s)", type(exc).__name__)

                gecko_mints = [mint for mint in missing_mints if mint not in jupiter_covered]
                if gecko_mints:
                    # The simple batch index can omit long-tail Solana tokens. Query token pools
                    # directly, but cap this to four per minute to stay comfortably below the
                    # public GeckoTerminal rate limit even when the open-position set grows.
                    pool_mints, next_offset = select_gecko_pool_mints(
                        gecko_mints,
                        state.get("paperPriceFeedGeckoOffset", 0),
                        MAX_GECKO_POOL_LOOKUPS_PER_CYCLE,
                    )
                    state["paperPriceFeedGeckoOffset"] = next_offset
                    state["paperPriceFeedGeckoDeferred"] = len(gecko_mints) - len(pool_mints)
                    for index, mint in enumerate(pool_mints):
                        try:
                            pools = await _fetch_geckoterminal_token_pools(session, mint)
                            mark = normalize_geckoterminal_pool_mark(pools, mint)
                            if mark is None:
                                no_pair += 1
                                continue
                            saved_ok, live_updated = await _save_price_mark(supabase_request, mark)
                            if not saved_ok:
                                errors += 1
                                log.warning("[Sniper] GeckoTerminal pool mark rejected by Supabase validation")
                            else:
                                updated += 1
                                live_marked += int(live_updated)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            errors += 1
                            log.warning("[Sniper] GeckoTerminal token-pools fallback failed (%s: %s)", type(exc).__name__, str(exc)[:100])
                        if index < len(pool_mints) - 1:
                            await asyncio.sleep(REQUEST_SPACING_SECONDS)

            state["paperPriceFeedStatus"] = "active"
            state["paperPriceFeedMints"] = len(mints)
            state["paperPriceFeedUpdated"] = updated
            state["livePositionMarksUpdated"] = live_marked
            state["paperPriceFeedNoPair"] = no_pair
            state["paperPriceFeedErrors"] = errors
            state["paperPriceFeedLastAt"] = int(time.time() * 1000)
            log.info(
                "[Sniper] Paper price refresh complete: checked=%s updated=%s no_pair=%s errors=%s",
                len(mints), updated, no_pair, errors,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state["paperPriceFeedStatus"] = "error"
            state["paperPriceFeedError"] = type(exc).__name__
            log.warning("[Sniper] Paper price feed cycle failed (%s)", type(exc).__name__)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
