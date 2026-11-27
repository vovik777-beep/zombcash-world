from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

log = logging.getLogger("zombcash.sniper.launches")
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
FEED_STATE = {
    "connected": False,
    "connectedAt": None,
    "lastEventAt": None,
    "lastTradeAt": None,
    "lastError": None,
    "received": 0,
    "tradesReceived": 0,
    "tradeStreamingEnabled": False,
    "pumpPortalTradeEnabled": False,
    "tradeStreamSource": None,
    "subscribedMints": 0,
    "heliusEnabled": False,
    "heliusConnected": False,
    "heliusConnectedAt": None,
    "heliusError": None,
    "heliusLastSignature": None,
    "heliusMissingKeyLogged": False,
    "paperEngineEnabled": False,
    "paperEngineStatus": "waiting_for_trade_data_source",
    "pumpdevEnabled": False,
    "pumpdevConnected": False,
    "pumpdevStatus": "disabled",
    "pumpdevSubscribedMints": 0,
    "pumpdevLastError": None,
    "pumpdevAckCount": 0,
    "pumpdevLastAck": None,
    "pumpdevNoticeCount": 0,
    "pumpdevLastNotice": None,
    "pumpdevTradeEventsSeen": 0,
    "pumpdevTradeEventsRejected": 0,
    "pumpdevTradeRejectReasons": {},
    "pumpdevTradePersistErrors": 0,
}


def _safe_number(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _event_time_ms(payload):
    """Return a trustworthy event timestamp; stale provider clocks must not kill launch windows."""
    raw = payload.get("timestamp") or payload.get("blockTime") or payload.get("createdAt")
    value = _safe_number(raw)
    received_ms = int(time.time() * 1000)
    if value is None or value <= 0:
        return received_ms
    if value < 1000000000000:
        value *= 1000
    event_ms = int(value)
    # PumpPortal payloads can omit timestamps or carry seconds from a delayed
    # cache. Keep real near-live chain timestamps, but anchor clearly stale or
    # future-dated events to observation time so the first-minute window stays usable.
    if abs(event_ms - received_ms) > 120_000:
        return received_ms
    return event_ms


def _normalize_launch_event(payload):
    if not isinstance(payload, dict):
        return None
    event_type = str(payload.get("txType") or payload.get("type") or "").strip().lower()
    if event_type not in {"create", "new_token", "complete", "migration", "migrate"}:
        return None
    mint = str(payload.get("mint") or "").strip()
    if not MINT_RE.fullmatch(mint):
        return None
    return {
        "mint": mint,
        "signature": str(payload.get("signature") or "")[:128] or None,
        "name": str(payload.get("name") or "")[:96],
        "symbol": str(payload.get("symbol") or "TOKEN")[:24],
        "creator": str(payload.get("traderPublicKey") or payload.get("creator") or "")[:64] or None,
        "uri": str(payload.get("uri") or "")[:512] or None,
        "pool": str(payload.get("pool") or payload.get("poolKey") or "")[:64] or None,
        "initialBuy": _safe_number(payload.get("initialBuy") or payload.get("solAmount")),
        "marketCapSol": _safe_number(payload.get("marketCapSol")),
        "vSolInBondingCurve": _safe_number(payload.get("vSolInBondingCurve")),
        "vTokensInBondingCurve": _safe_number(payload.get("vTokensInBondingCurve")),
        "eventType": event_type,
        "txType": event_type,
        # Keep launch age aligned with the receive clock if provider time is stale.
        "eventAt": (
            int(time.time() * 1000)
            if abs(_event_time_ms(payload) - int(time.time() * 1000)) > 120_000
            else _event_time_ms(payload)
        ),
    }



PUMPDEV_SOL_MINT = "So11111111111111111111111111111111111111112"

def _normalize_pumpdev_trade_event(payload):
    if not isinstance(payload, dict) or str(payload.get("txType") or "").lower() not in {"buy", "sell"}:
        return None
    mint = str(payload.get("mint") or "").strip()
    signature = str(payload.get("signature") or "").strip()
    if not MINT_RE.fullmatch(mint) or not signature or payload.get("quoteMint") != PUMPDEV_SOL_MINT:
        return None
    if payload.get("quoteContextResolved") is False:
        return None
    sol_amount = _safe_number(payload.get("solAmount"))
    if sol_amount is None:
        sol_amount = _safe_number(payload.get("quoteAmount"))
    token_amount_raw = _safe_number(payload.get("tokenAmount"))
    # PumpDev tokenAmount and bonding-curve virtual token reserves are raw SPL units (6 decimals).
    token_amount = token_amount_raw / 1_000_000 if token_amount_raw is not None else None
    v_sol = _safe_number(payload.get("vSolInBondingCurve"))
    v_tokens_raw = _safe_number(payload.get("vTokensInBondingCurve"))
    v_tokens = v_tokens_raw / 1_000_000 if v_tokens_raw is not None else None
    if str(payload.get("source") or "").lower() == "pumpswap":
        effective = _safe_number(payload.get("poolEffectiveQuoteReservesUi"))
        if effective is None:
            raw = _safe_number(payload.get("poolEffectiveQuoteReserves"))
            effective = raw / 1_000_000_000 if raw is not None else None
        base = _safe_number(payload.get("poolBaseReservesUi"))
        if base is None:
            raw = _safe_number(payload.get("poolBaseReserves"))
            base = raw / 1_000_000 if raw is not None else None
        v_sol, v_tokens = effective, base
    if any(v is None or v <= 0 for v in (sol_amount, token_amount, v_sol, v_tokens)):
        return None
    price = sol_amount / token_amount
    if not math.isfinite(price) or price <= 0:
        return None
    stamp = _safe_number(payload.get("timestamp"))
    event_at = int(time.time() * 1000) if stamp is None or stamp <= 0 else int(stamp * 1000 if stamp < 1_000_000_000_000 else stamp)
    return {"mint": mint, "signature": signature[:128], "side": str(payload["txType"]).lower(),
            "solAmount": sol_amount, "tokenAmount": token_amount, "priceSol": price,
            "marketCapSol": _safe_number(payload.get("marketCapSol")),
            "vSolInBondingCurve": v_sol, "vTokensInBondingCurve": v_tokens,
            "eventAt": event_at, "trader": str(payload.get("traderPublicKey") or "")[:64] or None}


def register_sniper_launch_feed(app: web.Application, supabase_request, allowed_origin: str) -> None:
    launch_enabled = asyncio.Event()
    active_launch_ws = None
    active_pumpdev_ws = None

    async def set_enabled(enabled: bool) -> None:
        nonlocal active_launch_ws, active_pumpdev_ws
        if enabled:
            launch_enabled.set()
            log.info("[Sniper] Launch monitoring enabled from website")
            return
        launch_enabled.clear()
        for ws in (active_launch_ws, active_pumpdev_ws):
            if ws is not None and not ws.closed:
                await ws.close()
        FEED_STATE.update({"connected": False, "lastError": None})
        log.info("[Sniper] Launch monitoring stopped from website")

    async def persist_launch(event: dict) -> None:
        await supabase_request("POST", "rpc/sniper_launch_upsert", payload={"p_event": event})


    async def pumpdev_trade_worker() -> None:
        """Discover new Pump.fun tokens only while Auto Sniper is enabled; then capture their trades."""
        nonlocal active_pumpdev_ws
        backoff = 5
        while True:
            await launch_enabled.wait()
            api_key = os.getenv("PUMPDEV_API_KEY", "").strip()
            try:
                active = []
                subscribed_new_tokens = False
                authenticated = not bool(api_key)
                async with ClientSession(timeout=ClientTimeout(total=None)) as session:
                    async with session.ws_connect("wss://pumpdev.io/ws", heartbeat=20, receive_timeout=600, max_msg_size=1_000_000) as ws:
                        active_pumpdev_ws = ws
                        FEED_STATE.update({"pumpdevEnabled": True, "pumpdevConnected": True,
                            "pumpdevStatus": "connected_anonymous" if not api_key else "authenticating",
                            "pumpdevLastError": None, "pumpdevSubscribedMints": 0})
                        if api_key:
                            await ws.send_json({"method": "auth", "key": api_key})
                        else:
                            await ws.send_json({"method": "subscribeNewToken"})
                            subscribed_new_tokens = True
                        async for message in ws:
                            if not launch_enabled.is_set():
                                await ws.close()
                                break
                            if message.type == WSMsgType.TEXT:
                                try:
                                    event = json.loads(message.data)
                                except (TypeError, ValueError):
                                    continue
                                if not isinstance(event, dict):
                                    continue
                                if event.get("type") == "auth":
                                    status = str(event.get("status") or "").lower()
                                    if status == "error":
                                        FEED_STATE["pumpdevStatus"] = "auth_error"
                                        raise RuntimeError("PumpDev auth error")
                                    authenticated = status in {"ok", "free"}
                                    FEED_STATE.update({"pumpdevEnabled": True, "pumpdevConnected": authenticated,
                                        "pumpdevStatus": "connected" if status == "ok" else "connected_free"})
                                    if not subscribed_new_tokens:
                                        await ws.send_json({"method": "subscribeNewToken"})
                                        subscribed_new_tokens = True
                                    continue
                                event_type = str(event.get("type") or "").lower()
                                if event_type == "subscribed":
                                    FEED_STATE["pumpdevAckCount"] = int(FEED_STATE.get("pumpdevAckCount", 0)) + 1
                                    ack = {
                                        "method": str(event.get("method") or "")[:64],
                                        "accepted": event.get("accepted") if isinstance(event.get("accepted"), (int, list, dict, bool)) else None,
                                        "dropped": event.get("dropped") if isinstance(event.get("dropped"), (int, list, dict, bool)) else None,
                                        "at": int(time.time() * 1000),
                                    }
                                    FEED_STATE["pumpdevLastAck"] = ack
                                    log.info("[Sniper] PumpDev subscription ack method=%s accepted=%s dropped=%s",
                                             ack["method"], str(ack["accepted"])[:120], str(ack["dropped"])[:120])
                                    continue
                                if event_type in {"notice", "error"}:
                                    FEED_STATE["pumpdevNoticeCount"] = int(FEED_STATE.get("pumpdevNoticeCount", 0)) + 1
                                    notice = {
                                        "type": event_type,
                                        "code": str(event.get("code") or "")[:80],
                                        "message": str(event.get("message") or event.get("error") or "")[:180],
                                        "accepted": event.get("accepted") if isinstance(event.get("accepted"), (int, list, dict, bool)) else None,
                                        "dropped": event.get("dropped") if isinstance(event.get("dropped"), (int, list, dict, bool)) else None,
                                        "at": int(time.time() * 1000),
                                    }
                                    FEED_STATE["pumpdevLastNotice"] = notice
                                    log.warning("[Sniper] PumpDev %s code=%s message=%s accepted=%s dropped=%s",
                                                event_type, notice["code"], notice["message"],
                                                str(notice["accepted"])[:120], str(notice["dropped"])[:120])
                                    continue
                                if event_type in {"connected", "connectionstatus"}:
                                    continue
                                if not authenticated:
                                    continue
                                if event.get("txType") == "create":
                                    launch = _normalize_launch_event(event)
                                    mint = str(event.get("mint") or "").strip()
                                    if launch:
                                        try:
                                            await persist_launch(launch)
                                            FEED_STATE["lastEventAt"] = int(time.time() * 1000)
                                            FEED_STATE["received"] = int(FEED_STATE.get("received", 0)) + 1
                                            FEED_STATE["lastError"] = None
                                            log.info("[Sniper] PumpDev launch stored: %s", mint)
                                        except Exception as exc:
                                            FEED_STATE["pumpdevLastError"] = type(exc).__name__
                                            log.warning("[Sniper] PumpDev launch persist failed (%s)", type(exc).__name__)
                                    if MINT_RE.fullmatch(mint) and mint not in active:
                                        max_subs = 25 if api_key else 5
                                        if len(active) >= max_subs:
                                            old = active.pop(0)
                                            await ws.send_json({"method": "unsubscribeTokenTrade", "keys": [old]})
                                        active.append(mint)
                                        await ws.send_json({"method": "subscribeTokenTrade", "keys": [mint]})
                                        FEED_STATE["pumpdevSubscribedMints"] = len(active)
                                    continue
                                FEED_STATE["pumpdevTradeEventsSeen"] = int(FEED_STATE.get("pumpdevTradeEventsSeen", 0)) + 1
                                normalized = _normalize_pumpdev_trade_event(event)
                                if not normalized:
                                    reason = "invalid_or_unsupported_trade"
                                    reasons = FEED_STATE.setdefault("pumpdevTradeRejectReasons", {})
                                    reasons[reason] = int(reasons.get(reason, 0)) + 1
                                    FEED_STATE["pumpdevTradeEventsRejected"] = int(FEED_STATE.get("pumpdevTradeEventsRejected", 0)) + 1
                                    continue
                                try:
                                    await supabase_request("POST", "rpc/sniper_trade_upsert", payload={"p_event": normalized})
                                    FEED_STATE["tradesReceived"] = int(FEED_STATE.get("tradesReceived", 0)) + 1
                                    FEED_STATE["lastTradeAt"] = int(time.time() * 1000)
                                    FEED_STATE["pumpdevLastError"] = None
                                except Exception as exc:
                                    FEED_STATE["pumpdevTradePersistErrors"] = int(FEED_STATE.get("pumpdevTradePersistErrors", 0)) + 1
                                    FEED_STATE["pumpdevLastError"] = type(exc).__name__
                                    log.warning("[Sniper] PumpDev persist failed (%s)", type(exc).__name__)
                            elif message.type == WSMsgType.ERROR:
                                raise ws.exception() or RuntimeError("PumpDev websocket error")
                            elif message.type in (WSMsgType.CLOSED, WSMsgType.CLOSE):
                                raise RuntimeError("PumpDev websocket closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                FEED_STATE["pumpdevConnected"] = False
                FEED_STATE["pumpdevStatus"] = "reconnecting"
                FEED_STATE["pumpdevLastError"] = type(exc).__name__
                if launch_enabled.is_set():
                    log.warning("[Sniper] PumpDev disconnected (%s); retry in %ss", type(exc).__name__, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 120)
            finally:
                active_pumpdev_ws = None
                FEED_STATE.update({"pumpdevConnected": False, "pumpdevSubscribedMints": 0,
                    "pumpdevStatus": "stopped" if not launch_enabled.is_set() else FEED_STATE.get("pumpdevStatus", "reconnecting")})
                if not launch_enabled.is_set():
                    FEED_STATE["pumpdevEnabled"] = False

    async def worker() -> None:
        """Free PumpPortal launch discovery only; connect only while the website switch is enabled."""
        nonlocal active_launch_ws
        backoff = 60
        while True:
            await launch_enabled.wait()
            try:
                uri = "wss://pumpportal.fun/api/data"
                timeout = ClientTimeout(total=None)
                async with ClientSession(timeout=timeout) as session:
                    async with session.ws_connect(
                        uri,
                        heartbeat=20,
                        receive_timeout=600,
                        max_msg_size=1_000_000,
                    ) as ws:
                        active_launch_ws = ws
                        FEED_STATE.update({
                            "connected": True,
                            "connectedAt": int(time.time() * 1000),
                            "lastError": None,
                            "tradeStreamingEnabled": False,
                            "pumpPortalTradeEnabled": False,
                            "tradeStreamSource": None,
                            "subscribedMints": 0,
                            "heliusEnabled": False,
                            "heliusConnected": False,
                            "heliusConnectedAt": None,
                            "heliusError": "LIVE_EXECUTION_ONLY",
                        })
                        log.info("[Sniper] PumpPortal launch-only websocket connected (newToken + migration)")
                        await ws.send_json({"method": "subscribeNewToken"})
                        await ws.send_json({"method": "subscribeMigration"})
                        backoff = 5
                        async for message in ws:
                            if message.type == WSMsgType.TEXT:
                                try:
                                    payload = json.loads(message.data)
                                except (TypeError, ValueError):
                                    continue
                                if isinstance(payload, dict) and (
                                    payload.get("error") or payload.get("success") is False
                                ):
                                    FEED_STATE["lastError"] = "PUMPPORTAL_SUBSCRIPTION_ERROR"
                                    log.warning("[Sniper] PumpPortal launch subscription error")
                                    continue
                                launch = _normalize_launch_event(payload)
                                if not launch:
                                    continue
                                try:
                                    await persist_launch(launch)
                                    FEED_STATE["lastEventAt"] = int(time.time() * 1000)
                                    FEED_STATE["received"] = int(FEED_STATE.get("received", 0)) + 1
                                    FEED_STATE["lastError"] = None
                                    log.info("[Sniper] Launch event stored: %s (%s)", launch["mint"], launch["eventType"])
                                except Exception as exc:
                                    FEED_STATE["lastError"] = type(exc).__name__
                                    log.warning("[Sniper] Could not store launch event (%s)", type(exc).__name__)
                            elif message.type == WSMsgType.ERROR:
                                raise ws.exception() or RuntimeError("PumpPortal websocket error")
                            elif message.type in (WSMsgType.CLOSED, WSMsgType.CLOSE):
                                raise RuntimeError("PumpPortal websocket closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                FEED_STATE["connected"] = False
                if launch_enabled.is_set():
                    FEED_STATE["lastError"] = type(exc).__name__
                    log.warning("[Sniper] PumpPortal launch stream disconnected (%s: %s); retrying in %ss", type(exc).__name__, str(exc)[:160], backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 120)
            finally:
                active_launch_ws = None
                FEED_STATE["connected"] = False
                FEED_STATE["tradeStreamingEnabled"] = False
                FEED_STATE["pumpPortalTradeEnabled"] = False
                FEED_STATE["tradeStreamSource"] = None
                FEED_STATE["subscribedMints"] = 0

    async def startup(application: web.Application) -> None:
        application["sniper_launch_feed_set_enabled"] = set_enabled
        application["sniper_launch_feed_task"] = asyncio.create_task(worker())
        application["pumpdev_trade_feed_task"] = asyncio.create_task(pumpdev_trade_worker())
        log.info("[Sniper] Launch monitoring is OFF until enabled by the website button; PumpDev feed is separately opt-in")

    async def cleanup(application: web.Application) -> None:
        task = application.get("sniper_launch_feed_task")
        trade_task = application.get("pumpdev_trade_feed_task")
        for running_task in (task, trade_task):
            if running_task:
                running_task.cancel()
                try:
                    await running_task
                except asyncio.CancelledError:
                    pass

    async def list_launches(request: web.Request) -> web.Response:
        if request.headers.get("Origin", "") != allowed_origin:
            return web.json_response({"error": "Invalid website origin"}, status=403)
        try:
            limit = max(1, min(int(request.query.get("limit", "50")), 100))
        except (TypeError, ValueError):
            limit = 50
        try:
            launches = await supabase_request(
                "POST", "rpc/sniper_launch_list", payload={"p_limit": limit}
            )
            return web.json_response({
                "ok": True,
                "launches": launches if isinstance(launches, list) else [],
                "feed": {
                    "connected": bool(FEED_STATE["connected"]),
                    "connectedAt": FEED_STATE["connectedAt"],
                    "lastEventAt": FEED_STATE["lastEventAt"],
                    "lastTradeAt": FEED_STATE["lastTradeAt"],
                    "lastError": FEED_STATE["lastError"],
                    "received": int(FEED_STATE["received"]),
                    "tradesReceived": int(FEED_STATE["tradesReceived"]),
                    "tradeStreamingEnabled": bool(FEED_STATE["tradeStreamingEnabled"]),
                    "tradeStreamSource": FEED_STATE["tradeStreamSource"],
                    "subscribedMints": int(FEED_STATE["subscribedMints"]),
                    "heliusEnabled": bool(FEED_STATE["heliusEnabled"]),
                    "heliusConnected": bool(FEED_STATE["heliusConnected"]),
                    "heliusConnectedAt": FEED_STATE["heliusConnectedAt"],
                    "heliusError": "LIVE_EXECUTION_ONLY",
                    "heliusLastSignature": FEED_STATE["heliusLastSignature"],
                    "heliusPendingWrites": int(FEED_STATE.get("heliusPendingWrites", 0)),
                    "heliusPersistErrors": int(FEED_STATE.get("heliusPersistErrors", 0)),
                    "paperEngineEnabled": bool(FEED_STATE["paperEngineEnabled"]),
                    "paperEngineStatus": FEED_STATE["paperEngineStatus"],
                    "pumpdevEnabled": bool(FEED_STATE.get("pumpdevEnabled", False)),
                    "pumpdevConnected": bool(FEED_STATE.get("pumpdevConnected", False)),
                    "pumpdevStatus": FEED_STATE.get("pumpdevStatus", "disabled"),
                    "pumpdevSubscribedMints": int(FEED_STATE.get("pumpdevSubscribedMints", 0)),
                    "pumpdevLastError": FEED_STATE.get("pumpdevLastError"),
                    "pumpdevAckCount": int(FEED_STATE.get("pumpdevAckCount", 0)),
                    "pumpdevLastAck": FEED_STATE.get("pumpdevLastAck"),
                    "pumpdevNoticeCount": int(FEED_STATE.get("pumpdevNoticeCount", 0)),
                    "pumpdevLastNotice": FEED_STATE.get("pumpdevLastNotice"),
                    "pumpdevTradeEventsSeen": int(FEED_STATE.get("pumpdevTradeEventsSeen", 0)),
                    "pumpdevTradeEventsRejected": int(FEED_STATE.get("pumpdevTradeEventsRejected", 0)),
                    "pumpdevTradeRejectReasons": FEED_STATE.get("pumpdevTradeRejectReasons", {}),
                    "pumpdevTradePersistErrors": int(FEED_STATE.get("pumpdevTradePersistErrors", 0)),
                },
            })
        except Exception:
            log.exception("[Sniper] Launch feed lookup failed")
            return web.json_response({"error": "Launch feed is temporarily unavailable"}, status=502)

    app.router.add_get("/api/sniper/launches", list_launches)
    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
