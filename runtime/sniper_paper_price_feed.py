from __future__ import annotations

import asyncio
import logging
import math
import re
import time

from aiohttp import ClientSession, ClientTimeout

log = logging.getLogger("zombcash.sniper.paper_prices")
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
SOL_MINT = "So11111111111111111111111111111111111111112"
DEXSCREENER_TOKEN_PAIRS_URL = "https://api.dexscreener.com/token-pairs/v1/solana/{}"
JUPITER_PRICE_URL = "https://api.jup.ag/price/v3"
GECKOTERMINAL_SIMPLE_URL = "https://api.geckoterminal.com/api/v2/simple/networks/solana/token_price/{}"
MIN_LIQUIDITY_USD = 500.0
MAX_MINTS_PER_CYCLE = 20
POLL_INTERVAL_SECONDS = 60.0
REQUEST_SPACING_SECONDS = 1.1


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


async def _fetch_token_pairs(session: ClientSession, mint: str):
    url = DEXSCREENER_TOKEN_PAIRS_URL.format(mint)
    async with session.get(url, headers={"Accept": "application/json", "User-Agent": "ZombieCash-PaperPrice/1.0"}) as response:
        if response.status != 200:
            raise RuntimeError(f"DexScreener HTTP {response.status}")
        payload = await response.json(content_type=None)
    return payload if isinstance(payload, list) else []


async def paper_price_mark_worker(supabase_request, state):
    """Refresh marks for open Paper positions only; never writes trade events or opens positions."""
    timeout = ClientTimeout(total=12, connect=5)
    while True:
        try:
            rows = await supabase_request(
                "POST",
                "rpc/sniper_open_paper_position_mints",
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
                state["paperPriceFeedStatus"] = "waiting_for_open_positions"
                state["paperPriceFeedMints"] = 0
                await asyncio.sleep(30)
                continue

            updated = 0
            no_pair = 0
            errors = 0
            dex_covered = set()
            async with ClientSession(timeout=timeout) as session:
                for index, mint in enumerate(mints):
                    try:
                        pairs = await _fetch_token_pairs(session, mint)
                        mark = normalize_dexscreener_paper_mark(pairs, mint)
                        if mark is not None:
                            saved = await supabase_request(
                                "POST",
                                "rpc/sniper_paper_price_mark_upsert",
                                payload={"p_mark": mark},
                            )
                            if not isinstance(saved, dict) or saved.get("ok") is not True:
                                errors += 1
                                log.warning("[Sniper] DexScreener Paper mark rejected by Supabase validation")
                            else:
                                updated += 1
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
                            saved = await supabase_request(
                                "POST",
                                "rpc/sniper_paper_price_mark_upsert",
                                payload={"p_mark": mark},
                            )
                            if not isinstance(saved, dict) or saved.get("ok") is not True:
                                errors += 1
                                log.warning("[Sniper] Jupiter Paper mark rejected by Supabase validation")
                            else:
                                updated += 1
                                jupiter_covered.add(mint)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        errors += 1
                        log.warning("[Sniper] Jupiter Paper price fallback failed (%s)", type(exc).__name__)

                gecko_mints = [mint for mint in missing_mints if mint not in jupiter_covered]
                if gecko_mints:
                    try:
                        prices = await _fetch_geckoterminal_prices(session, gecko_mints)
                        observed_at_ms = int(time.time() * 1000)
                        for mint in gecko_mints:
                            mark = normalize_geckoterminal_paper_mark(prices, mint, observed_at_ms)
                            if mark is None:
                                no_pair += 1
                                continue
                            saved = await supabase_request(
                                "POST",
                                "rpc/sniper_paper_price_mark_upsert",
                                payload={"p_mark": mark},
                            )
                            if not isinstance(saved, dict) or saved.get("ok") is not True:
                                errors += 1
                                log.warning("[Sniper] GeckoTerminal Paper mark rejected by Supabase validation")
                            else:
                                updated += 1
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        errors += 1
                        no_pair = len(gecko_mints)
                        log.warning("[Sniper] GeckoTerminal Paper price fallback failed (%s)", type(exc).__name__)

            state["paperPriceFeedStatus"] = "active"
            state["paperPriceFeedMints"] = len(mints)
            state["paperPriceFeedUpdated"] = updated
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
