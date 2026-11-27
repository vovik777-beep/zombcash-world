from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time

from aiohttp import ClientSession, ClientTimeout, WSMsgType

log = logging.getLogger("zombcash.sniper.shrine")
SOL_MINT = "So11111111111111111111111111111111111111112"
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def normalize_shrine_trade(payload):
    """Map Shrine's documented per-token trade event to the shared trade schema."""
    if not isinstance(payload, dict):
        return None
    mint = str(payload.get("mint") or "").strip()
    signature = str(payload.get("signature") or "").strip()
    quote = str(payload.get("quote") or payload.get("quoteMint") or "").strip()
    if not MINT_RE.fullmatch(mint) or not signature:
        return None
    is_buy = payload.get("is_buy")
    if not isinstance(is_buy, bool):
        return None
    try:
        raw_sol_amount = payload.get("sol_amount")
        # Shrine converts USDC/USDT/USD1 quote amounts to SOL in sol_amount.
        # quote_amount is only a valid SOL fallback when the quote is explicitly WSOL.
        if raw_sol_amount is None and quote in {SOL_MINT, "SOL", "WSOL"}:
            raw_sol_amount = payload.get("quote_amount")
        sol_amount = float(raw_sol_amount)
        token_amount = float(payload.get("token_amount"))
        quote_price = float(payload.get("price"))
        timestamp = float(payload.get("timestamp"))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) and v > 0 for v in (sol_amount, token_amount, quote_price, timestamp)):
        return None
    # price is denominated in the pool's quote asset. Use Shrine's SOL-normalized
    # trade value to persist a consistent SOL/token price for every supported quote.
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


async def shrine_trade_worker(launch_enabled, mint_queue, supabase_request, feed_state):
    """Consume Shrine's free Socket.IO per-token trade channel over Engine.IO WebSocket."""
    backoff = 15
    while True:
        await launch_enabled.wait()
        active = {}
        expiry_tasks = set()
        try:
            url = "wss://sol.shrine.trade/socket.io/?EIO=4&transport=websocket"
            async with ClientSession(timeout=ClientTimeout(total=None)) as session:
                async with session.ws_connect(url, heartbeat=None, receive_timeout=90, max_msg_size=1_000_000) as ws:
                    first = await ws.receive(timeout=15)
                    if first.type != WSMsgType.TEXT or not first.data.startswith("0"):
                        raise RuntimeError("shrine_engineio_handshake_invalid")
                    await ws.send_str("40")
                    connected = await ws.receive(timeout=15)
                    if connected.type != WSMsgType.TEXT or not connected.data.startswith("40"):
                        raise RuntimeError("shrine_socketio_namespace_not_connected")
                    feed_state.update({
                        "shrineTradeConnected": True,
                        "shrineTradeStatus": "connected_free",
                        "shrineTradeLastError": None,
                    })
                    log.info("[Sniper] Shrine free trade stream connected")

                    async def subscribe_dispatcher():
                        while launch_enabled.is_set() and not ws.closed:
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
                            if len(active) >= 20:
                                feed_state["shrineTradeQueueDeferred"] = int(feed_state.get("shrineTradeQueueDeferred", 0)) + 1
                                try:
                                    mint_queue.put_nowait((mint, queued_at))
                                except asyncio.QueueFull:
                                    feed_state["shrineTradeQueueDrops"] = int(feed_state.get("shrineTradeQueueDrops", 0)) + 1
                                await asyncio.sleep(0.5)
                                continue
                            packet = "42" + json.dumps(["subscribe_trades", {"mint": mint}], separators=(",", ":"))
                            await ws.send_str(packet)
                            active[mint] = time.monotonic()
                            feed_state["shrineTradeSubscriptions"] = len(active)
                            feed_state["shrineTradeSubscriptionsSent"] = int(feed_state.get("shrineTradeSubscriptionsSent", 0)) + 1
                            feed_state["shrineTradeLastSubscriptionAt"] = int(time.time() * 1000)
                            feed_state["shrineTradeLastSubscriptionMint"] = mint
                            log.info("[Sniper] Shrine trade subscription sent mint=%s active=%s", mint, len(active))
                            async def expire(target):
                                await asyncio.sleep(60)
                                if target in active and not ws.closed:
                                    await ws.send_str("42" + json.dumps(["unsubscribe_trades", {"mint": target}], separators=(",", ":")))
                                    active.pop(target, None)
                                    feed_state["shrineTradeSubscriptions"] = len(active)
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
                                await ws.send_str("3")
                                continue
                            if raw.startswith("2"):
                                await ws.send_str("3" + raw[1:])
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
                            normalized = normalize_shrine_trade(payload)
                            if not normalized:
                                feed_state["shrineTradeInvalidEvents"] = int(feed_state.get("shrineTradeInvalidEvents", 0)) + 1
                                if not isinstance(payload, dict):
                                    reason = "payload_not_object"
                                elif not payload.get("mint"):
                                    reason = "missing_mint"
                                elif not payload.get("signature"):
                                    reason = "missing_signature"
                                elif str(payload.get("quote") or payload.get("quoteMint") or "") != SOL_MINT:
                                    reason = "quote_not_sol"
                                elif not isinstance(payload.get("is_buy"), bool):
                                    reason = "invalid_is_buy"
                                else:
                                    reason = "invalid_amount_or_price_or_timestamp"
                                feed_state["shrineTradeLastInvalidReason"] = reason
                                log.info("[Sniper] Shrine trade event rejected reason=%s", reason)
                                continue
                            try:
                                await supabase_request("POST", "rpc/sniper_trade_upsert", payload={"p_event": normalized})
                                feed_state["tradesReceived"] = int(feed_state.get("tradesReceived", 0)) + 1
                                feed_state["lastTradeAt"] = int(time.time() * 1000)
                                feed_state["shrineTradeLastError"] = None
                                feed_state["shrineTradeLastInvalidReason"] = None
                            except Exception as exc:
                                feed_state["shrineTradePersistErrors"] = int(feed_state.get("shrineTradePersistErrors", 0)) + 1
                                feed_state["shrineTradeLastError"] = type(exc).__name__
                                log.warning("[Sniper] Shrine trade persist failed (%s)", type(exc).__name__)
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
            for task in tuple(expiry_tasks):
                task.cancel()
            feed_state["shrineTradeConnected"] = False
            feed_state["shrineTradeSubscriptions"] = 0
            if not launch_enabled.is_set():
                feed_state["shrineTradeStatus"] = "stopped"
