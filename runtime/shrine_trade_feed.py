from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time

from aiohttp import ClientConnectionError, ClientSession, ClientTimeout, WSMsgType

log = logging.getLogger("zombcash.sniper.shrine")
SOL_MINT = "So11111111111111111111111111111111111111112"
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
SHRINE_MAX_ACTIVE_SUBSCRIPTIONS = 20
SHRINE_SUBSCRIPTION_TTL_SECONDS = 30
PAPER_SUBSCRIPTION_REFRESH_SECONDS = 30


def normalize_shrine_trade(payload, quote_mint=None):
    """Map Shrine per-token events to a SOL-denominated shared trade schema."""
    if not isinstance(payload, dict):
        return None
    mint = str(payload.get("mint") or "").strip()
    signature = str(payload.get("signature") or "").strip()
    quote = str(payload.get("quote") or payload.get("quoteMint") or quote_mint or "").strip()
    if not MINT_RE.fullmatch(mint) or not signature:
        return None
    is_buy = payload.get("is_buy")
    if not isinstance(is_buy, bool):
        return None
    try:
        raw_sol_amount = payload.get("sol_amount")
        # Some trade packets omit quote/sol_amount/price. Resolve the mint's
        # quote asset from Shrine metadata before treating quote_amount as SOL.
        if raw_sol_amount in (None, "") and quote in {SOL_MINT, "SOL", "WSOL"}:
            raw_sol_amount = payload.get("quote_amount")
        sol_amount = float(raw_sol_amount)
        token_amount = float(payload.get("token_amount"))
        timestamp = float(payload.get("timestamp"))
        raw_price = payload.get("price")
        quote_price = float(raw_price) if raw_price not in (None, "") else None
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) and v > 0 for v in (sol_amount, token_amount, timestamp)):
        return None
    if quote_price is not None and (not math.isfinite(quote_price) or quote_price <= 0):
        return None
    # quote_price is denominated in the pool's quote asset; the persisted price
    # must always be SOL/token, derived from the SOL-denominated trade amount.
    price_sol = sol_amount / token_amount
    if not math.isfinite(price_sol) or price_sol <= 0:
        return None
    return {
        "mint": mint,
        "signature": signature[:128],
        "side": "buy" if is_buy else "sell",
        "solAmount": sol_amount,
        "tokenAmount": token_amount,
        "priceSol": price_sol,
        "marketCapSol": None,
        "vSolInBondingCurve": None,
        "vTokensInBondingCurve": None,
        "eventAt": int(timestamp * 1000 if timestamp < 1_000_000_000_000 else timestamp),
        "trader": str(payload.get("wallet") or "")[:64] or None,
        "source": "shrine",
    }




async def _safe_ws_send(ws, payload, send_lock):
    """Send only while the transport is open; disconnect races are benign."""
    try:
        async with send_lock:
            if ws.closed:
                return False
            await ws.send_str(payload)
        return True
    except (ClientConnectionError, asyncio.TimeoutError, RuntimeError):
        return False


def select_paper_subscription_mints(rows, offset=0, limit=SHRINE_MAX_ACTIVE_SUBSCRIPTIONS):
    """Select a fair rotating batch of unique open Paper mints from the DB RPC rows."""
    mints = []
    seen = set()
    for row in rows if isinstance(rows, list) else []:
        mint = str(row.get("mint") or "").strip() if isinstance(row, dict) else str(row or "").strip()
        if MINT_RE.fullmatch(mint) and mint not in seen:
            seen.add(mint)
            mints.append(mint)
    if not mints:
        return [], 0, 0
    start = int(offset or 0) % len(mints)
    selected = [mints[(start + index) % len(mints)] for index in range(min(max(1, int(limit)), len(mints)))]
    return selected, (start + len(selected)) % len(mints), len(mints)


async def paper_position_subscription_worker(launch_enabled, paper_mint_queue, supabase_request, feed_state):
    """Continuously rotate Shrine's free subscription slots across open Paper positions."""
    offset = 0
    while True:
        await launch_enabled.wait()
        try:
            rows = await supabase_request("POST", "rpc/sniper_open_paper_position_mints", payload={})
            selected, offset, total = select_paper_subscription_mints(rows, offset)
            # Replace stale queued batches instead of accumulating duplicate mints.
            while True:
                try:
                    paper_mint_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            queued_at = int(time.time() * 1000)
            for mint in selected:
                try:
                    paper_mint_queue.put_nowait((mint, queued_at))
                except asyncio.QueueFull:
                    feed_state["shrinePaperMintQueueDrops"] = int(feed_state.get("shrinePaperMintQueueDrops", 0)) + 1
                    break
            feed_state["shrinePaperMintTotal"] = total
            feed_state["shrinePaperMintsQueued"] = len(selected)
            feed_state["shrinePaperMintOffset"] = offset
            if selected:
                log.info(
                    "[Sniper] Shrine prioritizing open Paper positions: queued=%s total=%s next_offset=%s",
                    len(selected), total, offset,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            feed_state["shrinePaperMintLastError"] = type(exc).__name__
            log.warning("[Sniper] Could not refresh Shrine Paper-position subscriptions (%s)", type(exc).__name__)
        await asyncio.sleep(PAPER_SUBSCRIPTION_REFRESH_SECONDS)


async def shrine_trade_worker(launch_enabled, mint_queue, supabase_request, feed_state, paper_mint_queue=None):
    """Consume Shrine's free Socket.IO per-token trade channel over Engine.IO WebSocket."""
    backoff = 15
    paper_mint_queue = paper_mint_queue or asyncio.Queue(maxsize=SHRINE_MAX_ACTIVE_SUBSCRIPTIONS * 2)
    while True:
        await launch_enabled.wait()
        active = {}
        expiry_tasks = set()
        metadata_tasks = set()
        quote_mints = {}
        pending_trades = {}
        metadata_lock = asyncio.Lock()
        last_metadata_request = [0.0]
        try:
            url = "wss://sol.shrine.trade/socket.io/?EIO=4&transport=websocket"
            async with ClientSession(timeout=ClientTimeout(total=None)) as session:
                async with session.ws_connect(url, heartbeat=None, receive_timeout=90, max_msg_size=1_000_000) as ws:
                    first = await ws.receive(timeout=15)
                    if first.type != WSMsgType.TEXT or not first.data.startswith("0"):
                        raise RuntimeError("shrine_engineio_handshake_invalid")
                    send_lock = asyncio.Lock()
                    if not await _safe_ws_send(ws, "40", send_lock):
                        raise RuntimeError("shrine_websocket_handshake_send_failed")
                    connected = await ws.receive(timeout=15)
                    if connected.type != WSMsgType.TEXT or not connected.data.startswith("40"):
                        raise RuntimeError("shrine_socketio_namespace_not_connected")
                    feed_state.update({
                        "shrineTradeConnected": True,
                        "shrineTradeStatus": "connected_free",
                        "shrineTradeLastError": None,
                    })
                    log.info("[Sniper] Shrine free trade stream connected")

                    async def persist_trade(payload, resolved_quote=None):
                        normalized = normalize_shrine_trade(payload, quote_mint=resolved_quote)
                        if not normalized:
                            return None
                        try:
                            await supabase_request("POST", "rpc/sniper_trade_upsert", payload={"p_event": normalized})
                            feed_state["tradesReceived"] = int(feed_state.get("tradesReceived", 0)) + 1
                            feed_state["lastTradeAt"] = int(time.time() * 1000)
                            feed_state["shrineTradeLastError"] = None
                            feed_state["shrineTradeLastInvalidReason"] = None
                            return True
                        except Exception as exc:
                            feed_state["shrineTradePersistErrors"] = int(feed_state.get("shrineTradePersistErrors", 0)) + 1
                            feed_state["shrineTradeLastError"] = type(exc).__name__
                            log.warning("[Sniper] Shrine trade persist failed (%s)", type(exc).__name__)
                            return False

                    async def resolve_quote_mint(mint):
                        try:
                            async with metadata_lock:
                                wait = 1.05 - (time.monotonic() - last_metadata_request[0])
                                if wait > 0:
                                    await asyncio.sleep(wait)
                                last_metadata_request[0] = time.monotonic()
                                async with session.get(
                                    "https://sol.shrine.trade/metadata",
                                    params={"mint": mint},
                                    timeout=ClientTimeout(total=10),
                                ) as response:
                                    if response.status != 200:
                                        raise RuntimeError(f"shrine_metadata_http_{response.status}")
                                    metadata = await response.json()
                            quote = str(metadata.get("quote") or (metadata.get("pumpfun") or {}).get("quote_mint") or "").strip()
                            if not quote:
                                raise RuntimeError("shrine_metadata_quote_missing")
                            quote_mints[mint] = quote
                            queued = pending_trades.pop(mint, [])
                            for pending in queued:
                                result = await persist_trade(pending, quote)
                                if result is None:
                                    feed_state["shrineTradeInvalidEvents"] = int(feed_state.get("shrineTradeInvalidEvents", 0)) + 1
                                    feed_state["shrineTradeLastInvalidReason"] = "metadata_quote_not_supported"
                            log.info("[Sniper] Shrine quote resolved mint=%s quote=%s pending=%s", mint, quote, len(queued))
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            feed_state["shrineTradeLastError"] = type(exc).__name__
                            feed_state["shrineTradeLastInvalidReason"] = "metadata_quote_lookup_failed"
                            pending_trades.pop(mint, None)
                            log.warning("[Sniper] Shrine quote lookup failed mint=%s error=%s", mint, type(exc).__name__)

                    async def subscribe_dispatcher():
                        while launch_enabled.is_set() and not ws.closed:
                            queue_used = paper_mint_queue
                            try:
                                mint, queued_at = paper_mint_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                queue_used = mint_queue
                                try:
                                    mint, queued_at = await asyncio.wait_for(mint_queue.get(), timeout=2)
                                except asyncio.TimeoutError:
                                    continue
                            now_ms = int(time.time() * 1000)
                            if now_ms - queued_at >= 60_000:
                                feed_state["shrineTradeQueueExpired"] = int(feed_state.get("shrineTradeQueueExpired", 0)) + 1
                                continue
                            if not MINT_RE.fullmatch(str(mint or "")):
                                feed_state["shrineTradeLastInvalidReason"] = "invalid_queued_mint"
                                continue
                            if mint in active:
                                continue
                            if len(active) >= SHRINE_MAX_ACTIVE_SUBSCRIPTIONS:
                                feed_state["shrineTradeQueueDeferred"] = int(feed_state.get("shrineTradeQueueDeferred", 0)) + 1
                                try:
                                    queue_used.put_nowait((mint, queued_at))
                                except asyncio.QueueFull:
                                    feed_state["shrineTradeQueueDrops"] = int(feed_state.get("shrineTradeQueueDrops", 0)) + 1
                                await asyncio.sleep(0.5)
                                continue
                            packet = "42" + json.dumps(["subscribe_trades", {"mint": mint}], separators=(",", ":"))
                            if not await _safe_ws_send(ws, packet, send_lock):
                                feed_state["shrineTradeLastError"] = "subscription_send_disconnected"
                                log.info("[Sniper] Shrine subscription send lost its WebSocket; reconnecting")
                                try:
                                    await ws.close()
                                except Exception:
                                    pass
                                return
                            active[mint] = time.monotonic()
                            feed_state["shrineTradeSubscriptions"] = len(active)
                            feed_state["shrineTradeSubscriptionsSent"] = int(feed_state.get("shrineTradeSubscriptionsSent", 0)) + 1
                            feed_state["shrineTradeLastSubscriptionAt"] = int(time.time() * 1000)
                            feed_state["shrineTradeLastSubscriptionMint"] = mint
                            log.info("[Sniper] Shrine trade subscription sent mint=%s active=%s", mint, len(active))
                            if mint not in quote_mints:
                                task = asyncio.create_task(resolve_quote_mint(mint))
                                metadata_tasks.add(task)
                                task.add_done_callback(metadata_tasks.discard)
                            async def expire(target):
                                try:
                                    await asyncio.sleep(SHRINE_SUBSCRIPTION_TTL_SECONDS)
                                    if target not in active:
                                        return
                                    active.pop(target, None)
                                    feed_state["shrineTradeSubscriptions"] = len(active)
                                    await _safe_ws_send(
                                        ws,
                                        "42" + json.dumps(["unsubscribe_trades", {"mint": target}], separators=(",", ":")),
                                        send_lock,
                                    )
                                except asyncio.CancelledError:
                                    raise
                                except Exception as exc:
                                    log.debug("[Sniper] Shrine subscription expiry ended (%s)", type(exc).__name__)
                            task = asyncio.create_task(expire(mint))
                            expiry_tasks.add(task)
                            task.add_done_callback(expiry_tasks.discard)

                    dispatcher = asyncio.create_task(subscribe_dispatcher())
                    backoff = 15
                    while launch_enabled.is_set() and not ws.closed:
                        message = await ws.receive(timeout=90)
                        if message.type == WSMsgType.TEXT:
                            raw = message.data
                            if raw == "2":
                                if not await _safe_ws_send(ws, "3", send_lock):
                                    raise RuntimeError("shrine_websocket_pong_failed")
                                continue
                            if raw.startswith("2"):
                                if not await _safe_ws_send(ws, "3" + raw[1:], send_lock):
                                    raise RuntimeError("shrine_websocket_pong_failed")
                                continue
                            if not raw.startswith("42"):
                                continue
                            try:
                                packet = json.loads(raw[2:])
                            except (TypeError, ValueError):
                                continue
                            if not isinstance(packet, list) or len(packet) < 2:
                                continue
                            event_name, payload = packet[0], packet[1]
                            if event_name in {"subscription_error", "error"}:
                                feed_state["shrineTradeLastError"] = "subscription_error"
                                continue
                            if event_name != "trade":
                                continue
                            feed_state["shrineTradeEventsSeen"] = int(feed_state.get("shrineTradeEventsSeen", 0)) + 1
                            feed_state["shrineTradeLastEventAt"] = int(time.time() * 1000)
                            feed_state["shrineTradeLastEventMint"] = str(payload.get("mint") or "")[:64] if isinstance(payload, dict) else None
                            mint_value = str(payload.get("mint") or "").strip() if isinstance(payload, dict) else ""
                            quote_value = (payload.get("quote") or payload.get("quoteMint") or quote_mints.get(mint_value)) if isinstance(payload, dict) else None
                            if isinstance(payload, dict) and payload.get("sol_amount") in (None, "") and not quote_value:
                                pending = pending_trades.setdefault(mint_value, [])
                                if len(pending) < 20:
                                    pending.append(payload)
                                continue
                            persist_result = await persist_trade(payload, quote_value)
                            if persist_result is None:
                                feed_state["shrineTradeInvalidEvents"] = int(feed_state.get("shrineTradeInvalidEvents", 0)) + 1
                                context = {"payload_type": type(payload).__name__}
                                if not isinstance(payload, dict):
                                    reason = "payload_not_object"
                                elif not payload.get("mint"):
                                    reason = "missing_mint"
                                elif not payload.get("signature"):
                                    reason = "missing_signature"
                                else:
                                    quote_value = str(payload.get("quote") or payload.get("quoteMint") or "")[:64]
                                    sol_raw = payload.get("sol_amount")
                                    quote_raw = payload.get("quote_amount")
                                    if not isinstance(payload.get("is_buy"), bool):
                                        reason = "invalid_is_buy"
                                    elif sol_raw in (None, "") and quote_value not in {SOL_MINT, "SOL", "WSOL"}:
                                        reason = "no_sol_value_for_quote"
                                    elif not payload.get("token_amount") or not payload.get("price") or not payload.get("timestamp"):
                                        reason = "missing_amount_price_or_timestamp"
                                    else:
                                        reason = "invalid_numeric_value"
                                    context = {
                                        "quote": quote_value or None,
                                        "has_sol_amount": sol_raw not in (None, ""),
                                        "has_quote_amount": quote_raw not in (None, ""),
                                        "has_token_amount": payload.get("token_amount") not in (None, ""),
                                        "has_price": payload.get("price") not in (None, ""),
                                        "has_timestamp": payload.get("timestamp") not in (None, ""),
                                        "has_signature": bool(payload.get("signature")),
                                    }
                                feed_state["shrineTradeLastInvalidReason"] = reason
                                feed_state["shrineTradeLastInvalidContext"] = context if isinstance(payload, dict) else {"payload_type": type(payload).__name__}
                                log.info("[Sniper] Shrine trade event rejected reason=%s context=%s", reason, feed_state["shrineTradeLastInvalidContext"])
                                continue
                            if persist_result is False:
                                continue

                        elif message.type in (WSMsgType.CLOSED, WSMsgType.CLOSE, WSMsgType.ERROR):
                            raise RuntimeError("shrine_websocket_closed")
                    dispatcher.cancel()
                    try:
                        await dispatcher
                    except asyncio.CancelledError:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            feed_state["shrineTradeConnected"] = False
            feed_state["shrineTradeStatus"] = "reconnecting" if launch_enabled.is_set() else "stopped"
            feed_state["shrineTradeLastError"] = type(exc).__name__
            if launch_enabled.is_set():
                log.warning("[Sniper] Shrine free trade stream disconnected (%s); retry in %ss", type(exc).__name__, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 180)
        finally:
            for task in tuple(expiry_tasks) + tuple(metadata_tasks):
                task.cancel()
            feed_state["shrineTradeConnected"] = False
            feed_state["shrineTradeSubscriptions"] = 0
            if not launch_enabled.is_set():
                feed_state["shrineTradeStatus"] = "stopped"
