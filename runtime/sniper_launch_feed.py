from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
from urllib.parse import urlencode

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from helius_standard_ws import helius_trade_worker
from sniper_paper_price_feed import live_price_mark_worker, paper_price_mark_worker
from sniper_live_lifecycle import live_fill_reconciliation_worker
from sniper_live_engine import live_entry_worker, live_exit_worker
from shrine_trade_feed import shrine_trade_worker

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
    "heliusLookupBudgetSkips": 0,
    "paperEngineEnabled": False,
    "paperEngineStatus": "waiting_for_trade_data_source",
    "paperPriceFeedStatus": "starting",
    "paperPriceFeedMints": 0,
    "paperPriceFeedUpdated": 0,
    "paperPriceFeedNoPair": 0,
    "paperPriceFeedErrors": 0,
    "paperPriceFeedLastAt": None,
    "paperPriceFeedError": None,
    "livePriceFeedStatus": "starting",
    "livePriceFeedMints": 0,
    "livePriceFeedTotalMints": 0,
    "livePriceFeedUpdated": 0,
    "livePriceFeedNoPair": 0,
    "livePriceFeedErrors": 0,
    "livePriceFeedLastAt": None,
    "livePriceFeedError": None,
    "liveFillReconcilerStatus": "starting",
    "liveFillReconcilerLastAt": None,
    "liveFillReconcilerLastSweep": None,
    "liveFillReconcilerLastError": None,
    "liveEntryStatus": "starting",
    "liveEntryLastAt": None,
    "liveEntryLastSweep": None,
    "liveEntryLastError": None,
    "liveExitStatus": "starting",
    "liveExitLastAt": None,
    "liveExitLastSweep": None,
    "liveExitLastError": None,
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
    "pumpdevTxTypeCounts": {},
    "pumpdevLastMarketEvent": None,
    "pumpdevTradeRejectReasons": {},
    "pumpdevTradePersistErrors": 0,
    "pumpPortalTradeEnabled": False,
    "pumpPortalTradeConnected": False,
    "pumpportalTradeStatus": "disabled",
    "pumpportalTradeSubscriptions": 0,
    "pumpportalTradeEventsSeen": 0,
    "pumpportalTradePersistErrors": 0,
    "pumpportalTradeLastError": None,
    "shrineTradeEnabled": True,
    "shrineTradeConnected": False,
    "shrineTradeStatus": "waiting",
    "shrineTradeSubscriptions": 0,
    "shrineTradeEventsSeen": 0,
    "shrineTradePersistErrors": 0,
    "shrineTradeLastError": None,
    "shrineTradeQueued": 0,
    "shrineTradeQueueDrops": 0,
    "shrineTradeQueueExpired": 0,
    "shrineTradeQueueDeferred": 0,
    "shrineTradeSubscriptionsSent": 0,
    "shrineTradeLastSubscriptionAt": None,
    "shrineTradeLastSubscriptionMint": None,
    "shrineTradeLastEventAt": None,
    "shrineTradeLastEventMint": None,
    "shrineTradeInvalidEvents": 0,
    "shrineTradeLastInvalidReason": None,
    "shrineTradeLastInvalidContext": None,
}


async def _read_paper_monitoring_opt_in(supabase_request):
    """Read the aggregate Paper-mode opt-in without exposing individual users."""
    try:
        enabled = await supabase_request(
            "POST", "rpc/sniper_any_paper_monitoring_enabled", payload={}
        )
    except Exception as exc:
        log.warning(
            "[Sniper] Could not read persisted monitoring preference (%s)",
            type(exc).__name__,
        )
        return None
    return enabled is True


async def _restore_paper_monitoring_opt_in(supabase_request, launch_enabled: asyncio.Event) -> bool:
    """Restore the persisted Paper-mode opt-in after an API process restart."""
    enabled = await _read_paper_monitoring_opt_in(supabase_request)
    if enabled:
        launch_enabled.set()
        FEED_STATE["paperEngineEnabled"] = True
        return True
    return False


# PumpDev account dashboard confirmed the free-key monthly trade quota is exhausted.
# Use the documented anonymous tier (4 of 5 live subscriptions) until after the
# account's displayed reset date; then automatically resume the configured free key.
PUMPDEV_ANONYMOUS_FALLBACK_UNTIL_UTC = 1797724800  # 2026-12-20 00:00 UTC


def _pumpdev_effective_api_key(configured_key: str, now=None) -> str:
    key = str(configured_key or "").strip()
    if not key:
        return ""
    current_time = time.time() if now is None else float(now)
    if current_time < PUMPDEV_ANONYMOUS_FALLBACK_UNTIL_UTC:
        return ""
    return key


def _pumpdev_safe_subscription_limit(api_key_present: bool, status=None, tier=None) -> int:
    """Stay below the effective PumpDev tier even when a configured key is rejected."""
    if not api_key_present:
        return 4
    effective_tier = str(tier or "").strip().lower()
    if effective_tier in {"free", "starter", "pro", "whale", "custom"}:
        return 20
    if str(status or "").strip().lower() == "ok" and not effective_tier:
        # PumpDev confirms that the supplied key was applied; remain 5 below its free cap.
        return 20
    return 4


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


def _normalize_launch_event(payload, source="pumpportal"):
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
        "source": str(source or "pumpportal")[:32],
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
            "eventAt": event_at, "trader": str(payload.get("traderPublicKey") or "")[:64] or None,
            "source": "pumpdev"}


def _normalize_pumpportal_trade_event(payload):
    """Normalize PumpPortal trade payloads to the shared on-chain trade schema."""
    if not isinstance(payload, dict) or str(payload.get("txType") or "").lower() not in {"buy", "sell"}:
        return None
    mint = str(payload.get("mint") or "").strip()
    signature = str(payload.get("signature") or "").strip()
    if not MINT_RE.fullmatch(mint) or not signature:
        return None
    if payload.get("quoteMint") and payload.get("quoteMint") != PUMPDEV_SOL_MINT:
        return None
    sol_amount = _safe_number(payload.get("solAmount"))
    token_raw = _safe_number(payload.get("tokenAmount"))
    # PumpPortal payloads expose tokenAmount and curve reserves in UI units.
    token_amount = token_raw
    v_sol = _safe_number(payload.get("vSolInBondingCurve"))
    v_tokens = _safe_number(payload.get("vTokensInBondingCurve"))
    if str(payload.get("pool") or payload.get("source") or "").lower() in {"pumpswap", "pump-amm"}:
        quote = _safe_number(payload.get("poolEffectiveQuoteReservesUi"))
        if quote is None:
            quote = _safe_number(payload.get("poolEffectiveQuoteReserves"))
            quote = quote / 1_000_000_000 if quote is not None else None
        base = _safe_number(payload.get("poolBaseReservesUi"))
        if base is None:
            base = _safe_number(payload.get("poolBaseReserves"))
            base = base / 1_000_000 if base is not None else None
        v_sol, v_tokens = quote, base
    if sol_amount is None or sol_amount <= 0 or token_amount is None or token_amount <= 0:
        return None
    if v_sol is None or v_sol <= 0 or v_tokens is None or v_tokens <= 0:
        # Derive price from trade amounts when reserve fields are absent.
        price = sol_amount / token_amount
        v_sol, v_tokens = price, 1.0
    else:
        price = v_sol / v_tokens
    if not math.isfinite(price) or price <= 0:
        return None
    stamp = _safe_number(payload.get("timestamp"))
    event_at = int(time.time() * 1000) if stamp is None or stamp <= 0 else int(stamp * 1000 if stamp < 1_000_000_000_000 else stamp)
    return {
        "mint": mint, "signature": signature[:128], "side": str(payload["txType"]).lower(),
        "solAmount": sol_amount, "tokenAmount": token_amount, "priceSol": price,
        "marketCapSol": _safe_number(payload.get("marketCapSol")),
        "vSolInBondingCurve": v_sol, "vTokensInBondingCurve": v_tokens,
        "eventAt": event_at, "trader": str(payload.get("traderPublicKey") or "")[:64] or None,
        "source": "pumpportal",
    }


def register_sniper_launch_feed(app: web.Application, supabase_request, allowed_origin: str) -> None:
    launch_enabled = asyncio.Event()
    pumpportal_mint_queue = asyncio.Queue(maxsize=1000)
    shrine_mint_queue = asyncio.Queue(maxsize=1000)
    active_launch_ws = None
    active_pumpdev_ws = None
    helius_active_mints = {}
    helius_mints_changed = asyncio.Event()

    async def set_enabled(enabled: bool) -> None:
        nonlocal active_launch_ws, active_pumpdev_ws
        persisted = await _read_paper_monitoring_opt_in(supabase_request)
        if persisted is None:
            # If the database cannot confirm a new opt-in, do not start new feeds.
            # An explicit opt-out still stops this process immediately.
            if enabled:
                return
            effective_enabled = False
        else:
            # Feeds are shared by all registered users: one user's opt-out must not
            # stop monitoring while another Paper-mode user remains opted in.
            effective_enabled = persisted
        if effective_enabled:
            launch_enabled.set()
            FEED_STATE["paperEngineEnabled"] = True
            log.info("[Sniper] Launch monitoring active from persisted Paper-mode user settings")
            return
        launch_enabled.clear()
        helius_active_mints.clear()
        helius_mints_changed.set()
        FEED_STATE["paperEngineEnabled"] = False
        for ws in (active_launch_ws, active_pumpdev_ws):
            if ws is not None and not ws.closed:
                await ws.close()
        FEED_STATE.update({"connected": False, "lastError": None})
        log.info("[Sniper] Launch monitoring stopped; no Paper-mode users remain opted in")

    async def persist_launch(event: dict) -> None:
        await supabase_request("POST", "rpc/sniper_launch_upsert", payload={"p_event": event})


    async def pumpdev_trade_worker() -> None:
        """Discover new Pump.fun tokens only while Auto Sniper is enabled; then capture their trades."""
        nonlocal active_pumpdev_ws
        backoff = 30
        while True:
            await launch_enabled.wait()
            configured_api_key = os.getenv("PUMPDEV_API_KEY", "").strip()
            api_key = _pumpdev_effective_api_key(configured_api_key)
            if configured_api_key and not api_key:
                log.info(
                    "[Sniper] PumpDev free-key quota is exhausted; using anonymous free tier "
                    "until 2026-12-20 UTC (4 live trade subscriptions)",
                )
            try:
                # Hold each trade subscription for the full first-minute entry window.
                # A FIFO queue prevents a burst of launches from immediately evicting
                # the oldest mint before its first trades can arrive.
                active = {}
                pending = []
                subscribed_new_tokens = False
                last_diagnostic_at = 0.0
                authenticated = not bool(api_key)
                subscription_limit = 4
                async with ClientSession(timeout=ClientTimeout(total=None)) as session:
                    async with session.ws_connect("wss://pumpdev.io/ws", heartbeat=30, receive_timeout=600, max_msg_size=1_000_000) as ws:
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
                                diagnostic_now = time.monotonic()
                                if diagnostic_now - last_diagnostic_at >= 60:
                                    last_diagnostic_at = diagnostic_now
                                    log.info(
                                        "[Sniper] PumpDev stream diagnostics active=%s pending=%s acks=%s "
                                        "trade_events_seen=%s rejected=%s persist_errors=%s tx_types=%s "
                                        "reject_reasons=%s notices=%s",
                                        len(active), len(pending),
                                        int(FEED_STATE.get("pumpdevAckCount", 0)),
                                        int(FEED_STATE.get("pumpdevTradeEventsSeen", 0)),
                                        int(FEED_STATE.get("pumpdevTradeEventsRejected", 0)),
                                        int(FEED_STATE.get("pumpdevTradePersistErrors", 0)),
                                        str(FEED_STATE.get("pumpdevTxTypeCounts", {}))[:240],
                                        str(FEED_STATE.get("pumpdevTradeRejectReasons", {}))[:240],
                                        int(FEED_STATE.get("pumpdevNoticeCount", 0)),
                                    )
                                if event.get("type") == "auth":
                                    status = str(event.get("status") or "").lower()
                                    if status == "error":
                                        FEED_STATE["pumpdevStatus"] = "auth_error"
                                        raise RuntimeError("PumpDev auth error")
                                    authenticated = status in {"ok", "free"}
                                    tier = str(event.get("tier") or "").strip().lower()
                                    subscription_limit = _pumpdev_safe_subscription_limit(bool(api_key), status, tier)
                                    FEED_STATE.update({"pumpdevEnabled": True, "pumpdevConnected": authenticated,
                                        "pumpdevStatus": "connected" if status == "ok" else "connected_free"})
                                    log.info(
                                        "[Sniper] PumpDev auth resolved status=%s tier=%s safe_subscription_limit=%s",
                                        status or "missing", tier or "unspecified", subscription_limit,
                                    )
                                    if not subscribed_new_tokens:
                                        await ws.send_json({"method": "subscribeNewToken"})
                                        subscribed_new_tokens = True
                                    continue
                                event_type = str(event.get("type") or "").lower()
                                if event_type == "subscribed":
                                    FEED_STATE["pumpdevAckCount"] = int(FEED_STATE.get("pumpdevAckCount", 0)) + 1
                                    ack_keys = event.get("keys") if isinstance(event.get("keys"), list) else None
                                    accepted = event.get("accepted") if isinstance(event.get("accepted"), (int, list, dict, bool)) else (len(ack_keys) if ack_keys is not None else None)
                                    ack = {
                                        "method": str(event.get("method") or "")[:64],
                                        "accepted": accepted,
                                        "keys": [str(key)[:64] for key in ack_keys[:25]] if ack_keys is not None else None,
                                        "dropped": event.get("dropped") if isinstance(event.get("dropped"), (int, list, dict, bool)) else None,
                                        "at": int(time.time() * 1000),
                                    }
                                    FEED_STATE["pumpdevLastAck"] = ack
                                    log.info("[Sniper] PumpDev subscription ack method=%s accepted=%s keys=%s dropped=%s",
                                             ack["method"], str(ack["accepted"])[:120], str(ack["keys"])[:240], str(ack["dropped"])[:120])
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
                                # PumpDev documents every frame with a top-level "type" as
                                # a control frame; market events carry txType and no type.
                                # Do not count control frames as rejected trades.
                                if event.get("type"):
                                    log.debug("[Sniper] PumpDev control frame ignored type=%s",
                                              str(event.get("type"))[:64])
                                    continue
                                if not authenticated:
                                    continue
                                if event.get("txType") == "create":
                                    launch = _normalize_launch_event(event, source="pumpdev")
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
                                    try:
                                        shrine_mint_queue.put_nowait((mint, int(time.time() * 1000)))
                                        FEED_STATE["shrineTradeQueued"] = int(FEED_STATE.get("shrineTradeQueued", 0)) + 1
                                    except asyncio.QueueFull:
                                        FEED_STATE["shrineTradeQueueDrops"] = int(FEED_STATE.get("shrineTradeQueueDrops", 0)) + 1
                                        log.warning("[Sniper] Shrine mint queue full; free secondary subscription skipped")
                                    if os.getenv("PUMPPORTAL_API_KEY", "").strip():
                                        try:
                                            pumpportal_mint_queue.put_nowait((mint, int(time.time() * 1000)))
                                        except asyncio.QueueFull:
                                            log.warning("[Sniper] PumpPortal mint queue full; paid secondary subscription skipped")
                                    now_ms = int(time.time() * 1000)
                                    pending_mints = {item[0] for item in pending}
                                    if MINT_RE.fullmatch(mint) and mint not in active and mint not in pending_mints:
                                        pending.append((mint, now_ms))
                                    # A queued launch is useful only inside the same first-minute
                                    # window as the Paper entry rule; never subscribe to stale backlog.
                                    pending = [(queued_mint, queued_at) for queued_mint, queued_at in pending
                                               if now_ms - queued_at < 60_000]
                                    expired = [key for key, started_ms in active.items()
                                               if now_ms - started_ms >= 60_000]
                                    for old_mint in expired:
                                        await ws.send_json({"method": "unsubscribeTokenTrade", "keys": [old_mint]})
                                        active.pop(old_mint, None)
                                        helius_active_mints.pop(old_mint, None)
                                        helius_mints_changed.set()
                                    max_subs = subscription_limit
                                    while pending and len(active) < max_subs:
                                        next_mint, queued_at = pending.pop(0)
                                        if now_ms - queued_at >= 60_000:
                                            continue
                                        active[next_mint] = now_ms
                                        helius_active_mints[next_mint] = now_ms
                                        helius_mints_changed.set()
                                        await ws.send_json({"method": "subscribeTokenTrade", "keys": [next_mint]})
                                    FEED_STATE["pumpdevSubscribedMints"] = len(active)
                                    FEED_STATE["pumpdevPendingMints"] = len(pending)
                                    continue
                                tx_type = str(event.get("txType") or "").lower()
                                tx_counts = FEED_STATE.setdefault("pumpdevTxTypeCounts", {})
                                tx_counts[tx_type or "missing"] = int(tx_counts.get(tx_type or "missing", 0)) + 1
                                FEED_STATE["pumpdevLastMarketEvent"] = {
                                    "txType": tx_type or None,
                                    "source": str(event.get("source") or "")[:24] or None,
                                    "mint": str(event.get("mint") or "")[:64] or None,
                                    "quoteMint": str(event.get("quoteMint") or "")[:48] or None,
                                    "hasSignature": bool(event.get("signature")),
                                    "hasQuoteAmount": event.get("quoteAmount") is not None,
                                    "hasQuoteAmountRaw": event.get("quoteAmountRaw") is not None,
                                    "hasSolAmount": event.get("solAmount") is not None,
                                    "hasTokenAmount": event.get("tokenAmount") is not None,
                                    "hasBondingReserves": event.get("vSolInBondingCurve") is not None and event.get("vTokensInBondingCurve") is not None,
                                    "quoteContextResolved": event.get("quoteContextResolved"),
                                    "at": int(time.time() * 1000),
                                }
                                if tx_type in {"complete", "create_pool", "migration", "migrate"}:
                                    log.info("[Sniper] PumpDev lifecycle event ignored for trade storage txType=%s mint=%s",
                                             tx_type, str(event.get("mint") or "")[:64])
                                    continue
                                FEED_STATE["pumpdevTradeEventsSeen"] = int(FEED_STATE.get("pumpdevTradeEventsSeen", 0)) + 1
                                normalized = _normalize_pumpdev_trade_event(event)
                                if not normalized:
                                    mint_value = str(event.get("mint") or "").strip()
                                    signature_value = str(event.get("signature") or "").strip()
                                    tx_value = str(event.get("txType") or "").lower()
                                    if tx_value not in {"buy", "sell"}:
                                        reason = "unsupported_tx_type"
                                    elif not MINT_RE.fullmatch(mint_value):
                                        reason = "invalid_mint"
                                    elif not signature_value:
                                        reason = "missing_signature"
                                    elif event.get("quoteMint") != PUMPDEV_SOL_MINT:
                                        reason = "quote_mint_not_sol"
                                    elif event.get("quoteContextResolved") is False:
                                        reason = "quote_context_unresolved"
                                    else:
                                        sol_value = _safe_number(event.get("solAmount"))
                                        if sol_value is None:
                                            sol_value = _safe_number(event.get("quoteAmount"))
                                        token_value = _safe_number(event.get("tokenAmount"))
                                        v_sol_value = _safe_number(event.get("vSolInBondingCurve"))
                                        v_tokens_value = _safe_number(event.get("vTokensInBondingCurve"))
                                        if str(event.get("source") or "").lower() == "pumpswap":
                                            v_sol_value = _safe_number(event.get("poolEffectiveQuoteReservesUi"))
                                            if v_sol_value is None:
                                                raw_quote = _safe_number(event.get("poolEffectiveQuoteReserves"))
                                                v_sol_value = raw_quote / 1_000_000_000 if raw_quote is not None else None
                                            v_tokens_value = _safe_number(event.get("poolBaseReservesUi"))
                                            if v_tokens_value is None:
                                                raw_base = _safe_number(event.get("poolBaseReserves"))
                                                v_tokens_value = raw_base / 1_000_000 if raw_base is not None else None
                                        if sol_value is None or sol_value <= 0:
                                            reason = "missing_or_invalid_sol_amount"
                                        elif token_value is None or token_value <= 0:
                                            reason = "missing_or_invalid_token_amount"
                                        elif v_sol_value is None or v_sol_value <= 0:
                                            reason = "missing_or_invalid_sol_reserves"
                                        elif v_tokens_value is None or v_tokens_value <= 0:
                                            reason = "missing_or_invalid_token_reserves"
                                        else:
                                            reason = "normalization_failed_other"
                                    reasons = FEED_STATE.setdefault("pumpdevTradeRejectReasons", {})
                                    reasons[reason] = int(reasons.get(reason, 0)) + 1
                                    FEED_STATE["pumpdevTradeEventsRejected"] = int(FEED_STATE.get("pumpdevTradeEventsRejected", 0)) + 1
                                    log.info("[Sniper] PumpDev trade rejected reason=%s txType=%s source=%s quoteMint=%s quoteResolved=%s",
                                             reason, tx_value[:16], str(event.get("source") or "")[:24],
                                             str(event.get("quoteMint") or "")[:48],
                                             str(event.get("quoteContextResolved"))[:8])
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
                    backoff = min(backoff * 2, 300)
            finally:
                active_pumpdev_ws = None
                helius_active_mints.clear()
                helius_mints_changed.set()
                FEED_STATE.update({"pumpdevConnected": False, "pumpdevSubscribedMints": 0,
                    "pumpdevStatus": "stopped" if not launch_enabled.is_set() else FEED_STATE.get("pumpdevStatus", "reconnecting")})
                if not launch_enabled.is_set():
                    FEED_STATE["pumpdevEnabled"] = False

    async def worker() -> None:
        """Free PumpPortal launch discovery only; connect only while the website switch is enabled."""
        nonlocal active_launch_ws
        backoff = 120
        while True:
            await launch_enabled.wait()
            try:
                pumpportal_api_key = os.getenv("PUMPPORTAL_API_KEY", "").strip()
                uri = "wss://pumpportal.fun/api/data" + ("?" + urlencode({"api-key": pumpportal_api_key}) if pumpportal_api_key else "")
                timeout = ClientTimeout(total=None)
                async with ClientSession(timeout=timeout) as session:
                    async with session.ws_connect(
                        uri,
                        heartbeat=30,
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
                        })
                        subscribed_mints = {}
                        expiry_tasks = set()
                        FEED_STATE.update({"pumpPortalTradeEnabled": bool(pumpportal_api_key), "pumpPortalTradeConnected": bool(pumpportal_api_key), "pumpportalTradeStatus": "connected" if pumpportal_api_key else "launch_only_no_key", "pumpportalTradeLastError": None, "pumpportalTradeSubscriptions": 0})
                        log.info("[Sniper] PumpPortal websocket connected; trade fallback=%s", "enabled" if pumpportal_api_key else "launch-only (API key not configured)")
                        await ws.send_json({"method": "subscribeNewToken"})
                        await ws.send_json({"method": "subscribeMigration"})
                        async def pumpportal_subscription_dispatcher():
                            while launch_enabled.is_set() and not ws.closed:
                                try:
                                    mint, queued_at_ms = await asyncio.wait_for(pumpportal_mint_queue.get(), timeout=2)
                                except asyncio.TimeoutError:
                                    continue
                                if int(time.time() * 1000) - queued_at_ms >= 60_000:
                                    continue
                                if mint in subscribed_mints or len(subscribed_mints) >= 20:
                                    continue
                                await ws.send_json({"method": "subscribeTokenTrade", "keys": [mint]})
                                subscribed_mints[mint] = time.monotonic()
                                FEED_STATE["pumpportalTradeSubscriptions"] = len(subscribed_mints)
                                async def expire_trade_subscription(target_mint, target_ws):
                                    await asyncio.sleep(60)
                                    if target_mint in subscribed_mints and not target_ws.closed:
                                        try:
                                            await target_ws.send_json({"method": "unsubscribeTokenTrade", "keys": [target_mint]})
                                        except Exception:
                                            pass
                                        subscribed_mints.pop(target_mint, None)
                                        FEED_STATE["pumpportalTradeSubscriptions"] = len(subscribed_mints)
                                expiry = asyncio.create_task(expire_trade_subscription(mint, ws))
                                expiry_tasks.add(expiry)
                                expiry.add_done_callback(expiry_tasks.discard)
                        dispatcher_task = asyncio.create_task(pumpportal_subscription_dispatcher())
                        backoff = 120
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
                                tx_type = str(payload.get("txType") or "").lower()
                                if tx_type in {"buy", "sell"}:
                                    FEED_STATE["pumpportalTradeEventsSeen"] = int(FEED_STATE.get("pumpportalTradeEventsSeen", 0)) + 1
                                    normalized_trade = _normalize_pumpportal_trade_event(payload)
                                    if normalized_trade:
                                        try:
                                            await supabase_request("POST", "rpc/sniper_trade_upsert", payload={"p_event": normalized_trade})
                                            FEED_STATE["tradesReceived"] = int(FEED_STATE.get("tradesReceived", 0)) + 1
                                            FEED_STATE["lastTradeAt"] = int(time.time() * 1000)
                                            FEED_STATE["pumpportalTradeLastError"] = None
                                        except Exception as exc:
                                            FEED_STATE["pumpportalTradePersistErrors"] = int(FEED_STATE.get("pumpportalTradePersistErrors", 0)) + 1
                                            FEED_STATE["pumpportalTradeLastError"] = type(exc).__name__
                                            log.warning("[Sniper] PumpPortal trade persist failed (%s)", type(exc).__name__)
                                    else:
                                        log.debug("[Sniper] PumpPortal trade rejected by normalizer")
                                    continue
                                launch = _normalize_launch_event(payload, source="pumpportal")
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
                                mint = launch["mint"]
                                try:
                                    shrine_mint_queue.put_nowait((mint, int(time.time() * 1000)))
                                    FEED_STATE["shrineTradeQueued"] = int(FEED_STATE.get("shrineTradeQueued", 0)) + 1
                                except asyncio.QueueFull:
                                    FEED_STATE["shrineTradeQueueDrops"] = int(FEED_STATE.get("shrineTradeQueueDrops", 0)) + 1
                                    log.warning("[Sniper] Shrine mint queue full; free secondary subscription skipped")
                                if pumpportal_api_key:
                                    try:
                                        pumpportal_mint_queue.put_nowait((mint, int(time.time() * 1000)))
                                    except asyncio.QueueFull:
                                        log.warning("[Sniper] PumpPortal mint queue full; paid secondary subscription skipped")
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
                    backoff = min(backoff * 2, 600)
            finally:
                if "dispatcher_task" in locals() and not dispatcher_task.done():
                    dispatcher_task.cancel()
                    try:
                        await dispatcher_task
                    except asyncio.CancelledError:
                        pass
                active_launch_ws = None
                FEED_STATE["connected"] = False
                FEED_STATE["pumpPortalTradeConnected"] = False
                FEED_STATE["pumpportalTradeSubscriptions"] = 0
                FEED_STATE["pumpportalTradeStatus"] = "stopped" if not launch_enabled.is_set() else "reconnecting"
                FEED_STATE["tradeStreamingEnabled"] = False
                FEED_STATE["pumpPortalTradeEnabled"] = False
                FEED_STATE["tradeStreamSource"] = None
                FEED_STATE["subscribedMints"] = 0

    async def paper_position_watchdog() -> None:
        """Check open Paper positions independently of incoming trade events."""
        while True:
            try:
                result = await supabase_request("POST", "rpc/sniper_paper_engine_sweep", payload={})
                if isinstance(result, dict) and result.get("ok"):
                    FEED_STATE["paperEngineLastSweepAt"] = int(time.time() * 1000)
                    FEED_STATE["paperEngineLastSweep"] = {
                        "checked": int(result.get("checked") or 0),
                        "updated": int(result.get("updated") or 0),
                        "closed": int(result.get("closed") or 0),
                    }
                    FEED_STATE["paperEngineStatus"] = "watchdog_active"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                FEED_STATE["paperEngineStatus"] = "watchdog_error"
                log.warning("[Sniper] Paper position watchdog failed (%s)", type(exc).__name__)
            await asyncio.sleep(5)

    async def startup(application: web.Application) -> None:
        application["sniper_launch_feed_set_enabled"] = set_enabled
        restored = await _restore_paper_monitoring_opt_in(supabase_request, launch_enabled)
        application["sniper_launch_feed_task"] = asyncio.create_task(worker())
        application["pumpdev_trade_feed_task"] = asyncio.create_task(pumpdev_trade_worker())
        application["shrine_trade_feed_task"] = asyncio.create_task(
            shrine_trade_worker(launch_enabled, shrine_mint_queue, supabase_request, FEED_STATE)
        )
        application["helius_trade_feed_task"] = asyncio.create_task(
            helius_trade_worker(launch_enabled, helius_active_mints, helius_mints_changed, supabase_request, FEED_STATE)
        )
        application["sniper_paper_watchdog_task"] = asyncio.create_task(paper_position_watchdog())
        application["sniper_paper_price_feed_task"] = asyncio.create_task(
            paper_price_mark_worker(supabase_request, FEED_STATE)
        )
        application["sniper_live_price_feed_task"] = asyncio.create_task(
            live_price_mark_worker(supabase_request, FEED_STATE)
        )
        application["sniper_live_fill_reconciler_task"] = asyncio.create_task(
            live_fill_reconciliation_worker(supabase_request, FEED_STATE)
        )
        application["sniper_live_entry_task"] = asyncio.create_task(
            live_entry_worker(supabase_request, FEED_STATE)
        )
        application["sniper_live_exit_task"] = asyncio.create_task(
            live_exit_worker(supabase_request, FEED_STATE)
        )
        if restored:
            log.info("[Sniper] Restored launch monitoring from persisted Paper-mode user settings")
        else:
            log.info("[Sniper] Launch monitoring remains OFF (no enabled Paper-mode user); Paper watchdog runs independently")

    async def cleanup(application: web.Application) -> None:
        task = application.get("sniper_launch_feed_task")
        trade_task = application.get("pumpdev_trade_feed_task")
        shrine_task = application.get("shrine_trade_feed_task")
        watchdog_task = application.get("sniper_paper_watchdog_task")
        helius_task = application.get("helius_trade_feed_task")
        price_task = application.get("sniper_paper_price_feed_task")
        live_price_task = application.get("sniper_live_price_feed_task")
        fill_reconciler_task = application.get("sniper_live_fill_reconciler_task")
        live_entry_task = application.get("sniper_live_entry_task")
        live_exit_task = application.get("sniper_live_exit_task")
        for running_task in (task, trade_task, shrine_task, helius_task, watchdog_task, price_task, live_price_task, fill_reconciler_task, live_entry_task, live_exit_task):
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
                    "heliusError": FEED_STATE.get("heliusError"),
                    "heliusLastSignature": FEED_STATE["heliusLastSignature"],
                    "heliusPendingWrites": int(FEED_STATE.get("heliusPendingWrites", 0)),
                    "heliusPersistErrors": int(FEED_STATE.get("heliusPersistErrors", 0)),
                    "heliusMessagesReceived": int(FEED_STATE.get("heliusMessagesReceived", 0)),
                    "heliusTransactionsLookedUp": int(FEED_STATE.get("heliusTransactionsLookedUp", 0)),
                    "heliusLookupBudgetSkips": int(FEED_STATE.get("heliusLookupBudgetSkips", 0)),
                    "heliusTradesPersisted": int(FEED_STATE.get("heliusTradesPersisted", 0)),
                    "heliusTradesSkipped": int(FEED_STATE.get("heliusTradesSkipped", 0)),
                    "heliusBudget": FEED_STATE.get("heliusBudget"),
                    "paperEngineEnabled": bool(FEED_STATE["paperEngineEnabled"]),
                    "paperEngineStatus": FEED_STATE["paperEngineStatus"],
                    "paperEngineLastSweepAt": FEED_STATE.get("paperEngineLastSweepAt"),
                    "paperEngineLastSweep": FEED_STATE.get("paperEngineLastSweep"),
                    "paperPriceFeedStatus": FEED_STATE.get("paperPriceFeedStatus"),
                    "paperPriceFeedMints": int(FEED_STATE.get("paperPriceFeedMints", 0)),
                    "paperPriceFeedUpdated": int(FEED_STATE.get("paperPriceFeedUpdated", 0)),
                    "paperPriceFeedNoPair": int(FEED_STATE.get("paperPriceFeedNoPair", 0)),
                    "paperPriceFeedErrors": int(FEED_STATE.get("paperPriceFeedErrors", 0)),
                    "paperPriceFeedLastAt": FEED_STATE.get("paperPriceFeedLastAt"),
                    "paperPriceFeedError": FEED_STATE.get("paperPriceFeedError"),
                    "livePriceFeedStatus": FEED_STATE.get("livePriceFeedStatus"),
                    "livePriceFeedMints": int(FEED_STATE.get("livePriceFeedMints", 0)),
                    "livePriceFeedTotalMints": int(FEED_STATE.get("livePriceFeedTotalMints", 0)),
                    "livePriceFeedUpdated": int(FEED_STATE.get("livePriceFeedUpdated", 0)),
                    "livePriceFeedNoPair": int(FEED_STATE.get("livePriceFeedNoPair", 0)),
                    "livePriceFeedErrors": int(FEED_STATE.get("livePriceFeedErrors", 0)),
                    "livePriceFeedLastAt": FEED_STATE.get("livePriceFeedLastAt"),
                    "livePriceFeedError": FEED_STATE.get("livePriceFeedError"),
                    "liveFillReconcilerStatus": FEED_STATE.get("liveFillReconcilerStatus"),
                    "liveFillReconcilerLastAt": FEED_STATE.get("liveFillReconcilerLastAt"),
                    "liveFillReconcilerLastSweep": FEED_STATE.get("liveFillReconcilerLastSweep"),
                    "liveFillReconcilerLastError": FEED_STATE.get("liveFillReconcilerLastError"),
                    "liveEntryStatus": FEED_STATE.get("liveEntryStatus"),
                    "liveEntryLastAt": FEED_STATE.get("liveEntryLastAt"),
                    "liveEntryLastSweep": FEED_STATE.get("liveEntryLastSweep"),
                    "liveEntryLastError": FEED_STATE.get("liveEntryLastError"),
                    "liveExitStatus": FEED_STATE.get("liveExitStatus"),
                    "liveExitLastAt": FEED_STATE.get("liveExitLastAt"),
                    "liveExitLastSweep": FEED_STATE.get("liveExitLastSweep"),
                    "liveExitLastError": FEED_STATE.get("liveExitLastError"),
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
                    "pumpdevTxTypeCounts": FEED_STATE.get("pumpdevTxTypeCounts", {}),
                    "pumpdevLastMarketEvent": FEED_STATE.get("pumpdevLastMarketEvent"),
                    "pumpdevTradeRejectReasons": FEED_STATE.get("pumpdevTradeRejectReasons", {}),
                    "pumpdevTradePersistErrors": int(FEED_STATE.get("pumpdevTradePersistErrors", 0)),
                    "pumpportalTradeEnabled": bool(FEED_STATE.get("pumpPortalTradeEnabled", False)),
                    "pumpportalTradeConnected": bool(FEED_STATE.get("pumpPortalTradeConnected", False)),
                    "pumpportalTradeStatus": FEED_STATE.get("pumpportalTradeStatus", "disabled"),
                    "pumpportalTradeSubscriptions": int(FEED_STATE.get("pumpportalTradeSubscriptions", 0)),
                    "pumpportalTradeEventsSeen": int(FEED_STATE.get("pumpportalTradeEventsSeen", 0)),
                    "pumpportalTradePersistErrors": int(FEED_STATE.get("pumpportalTradePersistErrors", 0)),
                    "pumpportalTradeLastError": FEED_STATE.get("pumpportalTradeLastError"),
                    "shrineTradeEnabled": bool(FEED_STATE.get("shrineTradeEnabled", True)),
                    "shrineTradeConnected": bool(FEED_STATE.get("shrineTradeConnected", False)),
                    "shrineTradeStatus": FEED_STATE.get("shrineTradeStatus", "waiting"),
                    "shrineTradeSubscriptions": int(FEED_STATE.get("shrineTradeSubscriptions", 0)),
                    "shrineTradeEventsSeen": int(FEED_STATE.get("shrineTradeEventsSeen", 0)),
                    "shrineTradePersistErrors": int(FEED_STATE.get("shrineTradePersistErrors", 0)),
                    "shrineTradeLastError": FEED_STATE.get("shrineTradeLastError"),
                    "shrineTradeQueued": int(FEED_STATE.get("shrineTradeQueued", 0)),
                    "shrineTradeQueueDrops": int(FEED_STATE.get("shrineTradeQueueDrops", 0)),
                    "shrineTradeQueueExpired": int(FEED_STATE.get("shrineTradeQueueExpired", 0)),
                    "shrineTradeQueueDeferred": int(FEED_STATE.get("shrineTradeQueueDeferred", 0)),
                    "shrineTradeSubscriptionsSent": int(FEED_STATE.get("shrineTradeSubscriptionsSent", 0)),
                    "shrineTradeLastSubscriptionAt": FEED_STATE.get("shrineTradeLastSubscriptionAt"),
                    "shrineTradeLastSubscriptionMint": FEED_STATE.get("shrineTradeLastSubscriptionMint"),
                    "shrineTradeLastEventAt": FEED_STATE.get("shrineTradeLastEventAt"),
                    "shrineTradeLastEventMint": FEED_STATE.get("shrineTradeLastEventMint"),
                    "shrineTradeInvalidEvents": int(FEED_STATE.get("shrineTradeInvalidEvents", 0)),
                    "shrineTradeLastInvalidReason": FEED_STATE.get("shrineTradeLastInvalidReason"),
                    "shrineTradeLastInvalidContext": FEED_STATE.get("shrineTradeLastInvalidContext"),
                },
            })
        except Exception:
            log.exception("[Sniper] Launch feed lookup failed")
            return web.json_response({"error": "Launch feed is temporarily unavailable"}, status=502)

    app.router.add_get("/api/sniper/launches", list_launches)
    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
