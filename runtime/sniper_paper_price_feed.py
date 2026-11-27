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
                "GET",
                "sniper.paper_positions",
                params={"select": "mint", "status": "eq.open", "order": "opened_at.asc", "limit": str(MAX_MINTS_PER_CYCLE)},
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
            async with ClientSession(timeout=timeout) as session:
                for index, mint in enumerate(mints):
                    try:
                        pairs = await _fetch_token_pairs(session, mint)
                        mark = normalize_dexscreener_paper_mark(pairs, mint)
                        if mark is None:
                            no_pair += 1
                        else:
                            await supabase_request(
                                "POST",
                                "rpc/sniper_paper_price_mark_upsert",
                                payload={"p_mark": mark},
                            )
                            updated += 1
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        errors += 1
                        log.warning("[Sniper] Paper price refresh failed (%s)", type(exc).__name__)
                    if index < len(mints) - 1:
                        await asyncio.sleep(REQUEST_SPACING_SECONDS)
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
