from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientSession, ClientTimeout, WSMsgType

log = logging.getLogger("zombcash.sniper.helius")
MINT_RE = __import__("re").compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _helius_ws_url_from_rpc(rpc_url: str):
    """Derive the Standard WSS endpoint without exposing the credential URL."""
    try:
        parts = urlsplit(str(rpc_url or "").strip())
    except Exception:
        return None
    if parts.scheme != "https" or not parts.hostname or "helius" not in parts.hostname.lower():
        return None
    return urlunsplit(("wss", parts.netloc, parts.path or "/", parts.query, ""))


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _token_ui_amount(row):
    token = row.get("uiTokenAmount") if isinstance(row, dict) else None
    if not isinstance(token, dict):
        return None
    value = _number(token.get("uiAmountString"))
    if value is not None:
        return value
    raw = _number(token.get("amount"))
    try:
        decimals = int(token.get("decimals"))
    except (TypeError, ValueError):
        return None
    if raw is None or decimals < 0 or decimals > 18:
        return None
    return raw / (10 ** decimals)


def _normalize_helius_trade_event(transaction_result, mint: str, signature: str | None = None):
    """Conservatively derive a SOL-quoted trade from a parsed standard-RPC transaction.

    Transactions that create/delete the trader's token account, have ambiguous token
    owners, or lack a matching signer/native-SOL balance delta are deliberately skipped.
    """
    if not isinstance(transaction_result, dict) or not MINT_RE.fullmatch(str(mint or "")):
        return None
    meta = transaction_result.get("meta")
    tx = transaction_result.get("transaction")
    if not isinstance(meta, dict) or meta.get("err") is not None or not isinstance(tx, dict):
        return None
    logs = meta.get("logMessages")
    if not isinstance(logs, list) or not any(
        marker in str(line).lower()
        for line in logs
        for marker in ("instruction: buy", "instruction: sell", "instruction: swap")
    ):
        return None
    message = tx.get("message")
    if not isinstance(message, dict):
        return None
    account_keys = message.get("accountKeys")
    pre_lamports = meta.get("preBalances")
    post_lamports = meta.get("postBalances")
    if not isinstance(account_keys, list) or not isinstance(pre_lamports, list) or not isinstance(post_lamports, list):
        return None
    key_rows = []
    for item in account_keys:
        if isinstance(item, dict):
            key_rows.append((str(item.get("pubkey") or ""), bool(item.get("signer"))))
        else:
            key_rows.append((str(item or ""), False))
    pre_by_owner = {}
    post_by_owner = {}
    for rows, target in ((meta.get("preTokenBalances"), pre_by_owner), (meta.get("postTokenBalances"), post_by_owner)):
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get("mint") != mint:
                continue
            owner = str(row.get("owner") or "")
            amount = _token_ui_amount(row)
            if not owner or amount is None:
                continue
            target[owner] = target.get(owner, 0.0) + amount
    signer_owners = {pubkey for pubkey, signer in key_rows if pubkey and signer}
    deltas = []
    for owner in (set(pre_by_owner) & set(post_by_owner)) & signer_owners:
        delta = post_by_owner[owner] - pre_by_owner[owner]
        if abs(delta) > 0:
            deltas.append((owner, delta))
    if len(deltas) != 1:
        return None
    owner, token_delta = deltas[0]
    owner_indices = [i for i, (pubkey, signer) in enumerate(key_rows) if pubkey == owner and signer]
    if len(owner_indices) != 1:
        return None
    owner_index = owner_indices[0]
    if owner_index >= len(pre_lamports) or owner_index >= len(post_lamports):
        return None
    try:
        pre_sol_lamports = int(pre_lamports[owner_index])
        post_sol_lamports = int(post_lamports[owner_index])
        fee_lamports = int(meta.get("fee") or 0)
    except (TypeError, ValueError):
        return None
    fee_adjustment = fee_lamports if owner_index == 0 else 0
    if token_delta > 0:
        side = "buy"
        sol_lamports = pre_sol_lamports - post_sol_lamports - fee_adjustment
    else:
        side = "sell"
        sol_lamports = post_sol_lamports - pre_sol_lamports + fee_adjustment
    token_amount = abs(token_delta)
    sol_amount = sol_lamports / 1_000_000_000
    if not math.isfinite(sol_amount) or not math.isfinite(token_amount) or sol_amount <= 0 or token_amount <= 0:
        return None
    price = sol_amount / token_amount
    if not math.isfinite(price) or price <= 0:
        return None
    tx_signatures = tx.get("signatures")
    tx_signature = str(signature or (tx_signatures[0] if isinstance(tx_signatures, list) and tx_signatures else "")).strip()
    if not tx_signature:
        return None
    try:
        block_time = float(transaction_result.get("blockTime"))
    except (TypeError, ValueError):
        block_time = 0
    event_at = int(block_time * 1000) if block_time > 0 else int(time.time() * 1000)
    return {
        "mint": mint,
        "signature": tx_signature[:128],
        "side": side,
        "solAmount": sol_amount,
        "tokenAmount": token_amount,
        "priceSol": price,
        "eventAt": event_at,
        "trader": owner[:64],
    }


async def helius_trade_worker(launch_enabled, active_mints, mints_changed, supabase_request, state):
    """Use only Standard logsSubscribe, four mint filters, and persistent hard budgets."""
    backoff = 15
    seen_signatures = set()
    while True:
        await launch_enabled.wait()
        rpc_url = os.getenv("HELIUS_RPC_URL", "").strip()
        ws_url = _helius_ws_url_from_rpc(rpc_url)
        if not ws_url:
            state.update({"heliusEnabled": False, "heliusConnected": False,
                          "heliusError": "missing_or_invalid_helius_rpc_url"})
            if not state.get("heliusMissingKeyLogged"):
                log.warning("[Sniper] Helius Standard WSS disabled: HELIUS_RPC_URL is not configured")
                state["heliusMissingKeyLogged"] = True
            await asyncio.sleep(60)
            continue

        while launch_enabled.is_set():
            now_ms = int(time.time() * 1000)
            for mint, started_ms in list(active_mints.items()):
                if now_ms - int(started_ms) >= 60_000:
                    active_mints.pop(mint, None)
                    mints_changed.set()
            if active_mints:
                break
            mints_changed.clear()
            if active_mints:
                break
            await mints_changed.wait()
        if not launch_enabled.is_set():
            continue

        budget_blocked = False
        budget_rpc_failed = False
        try:
            timeout = ClientTimeout(total=None, connect=8)
            async with ClientSession(timeout=timeout) as session:
                async with session.ws_connect(
                    ws_url, heartbeat=30, receive_timeout=600, max_msg_size=262144
                ) as ws:
                    state.update({"heliusEnabled": True, "heliusConnected": True,
                                  "heliusConnectedAt": int(time.time() * 1000), "heliusError": None})
                    log.info("[Sniper] Helius Standard WSS connected (mint-specific logsSubscribe; max 4)")
                    request_id = 1
                    pending_requests = {}
                    subscribed = {}
                    subscription_mints = {}
                    while launch_enabled.is_set():
                        now_ms = int(time.time() * 1000)
                        for mint, started_ms in list(active_mints.items()):
                            if now_ms - int(started_ms) >= 60_000:
                                active_mints.pop(mint, None)
                                mints_changed.set()
                        desired = {
                            mint for mint, started_ms in list(active_mints.items())
                            if MINT_RE.fullmatch(mint) and now_ms - int(started_ms) < 60_000
                        }
                        for pending_id, pending_mint in list(pending_requests.items()):
                            if pending_mint not in desired:
                                pending_requests.pop(pending_id, None)
                        if not desired:
                            await ws.close()
                            break
                        for mint, sub_id in list(subscribed.items()):
                            if mint not in desired:
                                await ws.send_json({
                                    "jsonrpc": "2.0", "id": request_id,
                                    "method": "logsUnsubscribe", "params": [sub_id],
                                })
                                request_id += 1
                                subscribed.pop(mint, None)
                                subscription_mints.pop(sub_id, None)
                        for mint in sorted(desired):
                            if (mint not in subscribed and mint not in pending_requests.values()
                                    and len(subscribed) + len(pending_requests) < 4):
                                pending_requests[request_id] = mint
                                await ws.send_json({
                                    "jsonrpc": "2.0", "id": request_id,
                                    "method": "logsSubscribe",
                                    "params": [{"mentions": [mint]}, {"commitment": "confirmed"}],
                                })
                                request_id += 1
                        try:
                            message = await asyncio.wait_for(ws.receive(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        if message.type == WSMsgType.TEXT:
                            frame_bytes = len(message.data.encode("utf-8"))
                            try:
                                payload = json.loads(message.data)
                            except (TypeError, ValueError):
                                payload = None
                            signature = ""
                            mint_for_signature = ""
                            lookup = False
                            if isinstance(payload, dict) and payload.get("method") == "logsNotification":
                                params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
                                result = params.get("result") if isinstance(params.get("result"), dict) else {}
                                value = result.get("value") if isinstance(result.get("value"), dict) else {}
                                signature = str(value.get("signature") or "").strip()
                                mint_for_signature = subscription_mints.get(params.get("subscription"), "")
                                lookup = bool(signature and mint_for_signature and value.get("err") is None
                                              and signature not in seen_signatures)
                            try:
                                budget = await supabase_request(
                                    "POST", "rpc/sniper_helius_budget_consume",
                                    payload={"p_bytes": frame_bytes, "p_lookup": lookup},
                                )
                            except Exception as exc:
                                state["heliusError"] = "budget_rpc_unavailable"
                                budget_rpc_failed = True
                                log.warning("[Sniper] Helius budget check failed (%s); stream stopped", type(exc).__name__)
                                await ws.close()
                                break
                            if not isinstance(budget, dict) or budget.get("allowed") is not True:
                                reason = str(budget.get("reason") if isinstance(budget, dict) else "invalid_budget_response")
                                state["heliusError"] = reason[:64]
                                log.warning("[Sniper] Helius stream stopped by hard usage budget reason=%s", reason[:64])
                                budget_blocked = True
                                await ws.close()
                                break
                            state["heliusBudget"] = budget
                            state["heliusMessagesReceived"] = int(state.get("heliusMessagesReceived", 0)) + 1
                            if isinstance(payload, dict):
                                response_id = payload.get("id")
                                if response_id in pending_requests and isinstance(payload.get("result"), int):
                                    mint = pending_requests.pop(response_id)
                                    subscribed[mint] = payload["result"]
                                    subscription_mints[payload["result"]] = mint
                                    log.info("[Sniper] Helius logsSubscribe accepted (active subscriptions=%s)", len(subscribed))
                                elif response_id in pending_requests and payload.get("error"):
                                    pending_requests.pop(response_id, None)
                                    state["heliusError"] = "logs_subscribe_rejected"
                                if lookup:
                                    seen_signatures.add(signature)
                                    if len(seen_signatures) > 2000:
                                        seen_signatures.clear()
                                    state["heliusTransactionsLookedUp"] = int(state.get("heliusTransactionsLookedUp", 0)) + 1
                                    rpc_payload = {
                                        "jsonrpc": "2.0", "id": request_id,
                                        "method": "getTransaction",
                                        "params": [signature, {
                                            "encoding": "jsonParsed",
                                            "commitment": "confirmed",
                                            "maxSupportedTransactionVersion": 0,
                                        }],
                                    }
                                    request_id += 1
                                    try:
                                        async with session.post(
                                            rpc_url, json=rpc_payload, timeout=ClientTimeout(total=8)
                                        ) as response:
                                            tx_response = await response.json(content_type=None)
                                        if response.status != 200 or not isinstance(tx_response, dict) or tx_response.get("error"):
                                            state["heliusError"] = "get_transaction_failed"
                                            continue
                                        normalized = _normalize_helius_trade_event(
                                            tx_response.get("result"), mint_for_signature, signature
                                        )
                                        if not normalized:
                                            state["heliusTradesSkipped"] = int(state.get("heliusTradesSkipped", 0)) + 1
                                            continue
                                        persisted = await supabase_request(
                                            "POST", "rpc/sniper_trade_upsert",
                                            payload={"p_event": normalized},
                                        )
                                        if not isinstance(persisted, dict) or not persisted.get("duplicate"):
                                            state["heliusTradesPersisted"] = int(state.get("heliusTradesPersisted", 0)) + 1
                                            state["tradesReceived"] = int(state.get("tradesReceived", 0)) + 1
                                            state["lastTradeAt"] = int(time.time() * 1000)
                                        state["heliusLastSignature"] = signature[:128]
                                        state["heliusError"] = None
                                    except asyncio.CancelledError:
                                        raise
                                    except Exception as exc:
                                        state["heliusError"] = type(exc).__name__
                                        log.warning("[Sniper] Helius transaction processing failed (%s)", type(exc).__name__)
                        elif message.type == WSMsgType.ERROR:
                            raise ws.exception() or RuntimeError("Helius websocket error")
                        elif message.type in (WSMsgType.CLOSED, WSMsgType.CLOSE):
                            raise RuntimeError("Helius websocket closed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state["heliusError"] = type(exc).__name__
            if launch_enabled.is_set():
                log.warning("[Sniper] Helius Standard WSS disconnected (%s); retry in %ss", type(exc).__name__, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)
        finally:
            state["heliusConnected"] = False
        if budget_blocked:
            now = time.time()
            seconds_to_utc_reset = max(60, ((int(now) // 86400) + 1) * 86400 - now)
            log.warning("[Sniper] Helius Standard WSS paused until the next UTC day after reaching its hard budget")
            await asyncio.sleep(seconds_to_utc_reset)
        elif budget_rpc_failed:
            await asyncio.sleep(60)
