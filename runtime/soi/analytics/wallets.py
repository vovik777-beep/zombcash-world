"""Observed early-wallet behavior; descriptive metrics, not identity or sniper labels."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Iterable

WSOL = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000


def _dt(value):
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def analyze_early_wallets(observations: Iterable[object], mint: str,
                          early_window_seconds: int = 60,
                          rapid_exit_seconds: int = 300) -> dict:
    """Summarize wallet activity in the first hour after an observed launch.

    SOL buy volume is included only for trades with confirmed WSOL quote mint.
    Tags describe observed timing/activity; they do not identify bots, snipers,
    bundles, insiders, or coordinated actors.
    """
    items = list(observations)
    launches = [o for o in items if o.mint == mint and o.kind == "token_created" and o.event_at]
    if not launches:
        raise ValueError(f"No observed token_created event for mint {mint}")
    launch = min(launches, key=lambda o: _dt(o.event_at))
    start = _dt(launch.event_at)
    wallets = {}
    excluded_quote_trades = 0
    trade_count = 0

    for obs in items:
        if obs.mint != mint or obs.kind not in ("trade_buy", "trade_sell") or not obs.event_at:
            continue
        stamp = _dt(obs.event_at)
        age = (stamp - start).total_seconds()
        if not 0 <= age < 3600:
            continue
        event = (obs.payload or {}).get("event", {})
        wallet = event.get("user")
        if not wallet:
            continue
        trade_count += 1
        row = wallets.setdefault(wallet, {
            "wallet": wallet, "first_seen_seconds": age,
            "buy_count": 0, "sell_count": 0,
            "sol_buy_volume": 0.0, "sol_sell_volume": 0.0,
            "first_buy_seconds": None, "first_buy_signature": None, "first_buy_anchor_conflict": False, "first_sell_seconds": None,
            "last_buy_seconds": None, "last_sell_seconds": None,
            "slots": set(), "signatures": set(),
        })
        row["first_seen_seconds"] = min(row["first_seen_seconds"], age)
        row["slots"].add((obs.payload or {}).get("slot"))
        signature = (obs.payload or {}).get("signature")
        if signature:
            row["signatures"].add(signature)
        is_buy = obs.kind == "trade_buy"
        side = "buy" if is_buy else "sell"
        row[f"{side}_count"] += 1
        key = f"first_{side}_seconds"
        if row[key] is None or age < row[key]:
            row[key] = age
            if is_buy:
                row["first_buy_signature"] = signature
                row["first_buy_anchor_conflict"] = False
        elif is_buy and age == row[key] and signature != row["first_buy_signature"]:
            row["first_buy_anchor_conflict"] = True
        row[f"last_{side}_seconds"] = age if row[f"last_{side}_seconds"] is None else max(row[f"last_{side}_seconds"], age)

        quote_mint = event.get("quote_mint")
        raw_sol = None
        if quote_mint == WSOL:
            if obs.source == "pumpswap_rpc":
                amount_key = "quote_amount_in" if is_buy else "quote_amount_out"
                raw_sol = event.get(amount_key)
            else:
                raw_sol = event.get("sol_amount")
        elif quote_mint is None and obs.source == "pumpfun_rpc":
            # Legacy Pump.fun event schema before quote_mint was added.
            raw_sol = event.get("sol_amount")
        if raw_sol is None:
            excluded_quote_trades += 1
        else:
            row[f"sol_{side}_volume"] += int(raw_sol) / LAMPORTS_PER_SOL

    rows = []
    for wallet, row in wallets.items():
        row["slots"] = len(row["slots"] - {None})
        row["signatures"] = len(row["signatures"])
        row["net_observed_sol_flow"] = row["sol_buy_volume"] - row["sol_sell_volume"]
        tags = []
        if row["first_seen_seconds"] <= early_window_seconds:
            tags.append("early_entry")
        if row["buy_count"] > 1:
            tags.append("repeat_buyer")
        if row["sell_count"] > 0 and row["first_buy_seconds"] is not None:
            if row["first_sell_seconds"] is not None and row["first_sell_seconds"] - row["first_buy_seconds"] <= rapid_exit_seconds:
                tags.append("rapid_buy_to_sell")
        if row["buy_count"] and row["sell_count"]:
            tags.append("both_sides_observed")
        row["behavior_tags"] = tags
        rows.append(row)

    rows.sort(key=lambda x: (-x["sol_buy_volume"], x["first_seen_seconds"], x["wallet"]))
    total_sol_buy = sum(x["sol_buy_volume"] for x in rows)
    for row in rows:
        row["share_of_known_sol_buy_volume"] = row["sol_buy_volume"] / total_sol_buy if total_sol_buy else None
    top1 = sum(x["sol_buy_volume"] for x in rows[:1])
    top5 = sum(x["sol_buy_volume"] for x in rows[:5])
    return {
        "mint": mint, "launch_at": start.isoformat(),
        "window_seconds": 3600, "wallet_count": len(rows),
        "observed_trade_count_with_wallet": trade_count,
        "wallets_with_both_buy_and_sell": sum(bool(x["buy_count"] and x["sell_count"]) for x in rows),
        "early_entry_wallets": sum("early_entry" in x["behavior_tags"] for x in rows),
        "known_sol_buy_volume": total_sol_buy,
        "top1_share_known_sol_buy_volume": top1 / total_sol_buy if total_sol_buy else None,
        "top5_share_known_sol_buy_volume": top5 / total_sol_buy if total_sol_buy else None,
        "non_wsol_or_unresolved_quote_trades": excluded_quote_trades,
        "wallets": rows,
        "methodology": {
            "identity_inference": False, "sniper_or_bundle_classification": False,
            "early_entry_threshold_seconds": early_window_seconds,
            "rapid_exit_threshold_seconds": rapid_exit_seconds,
            "volume_basis": "WSOL-denominated observed buy/sell event amounts only; no USD conversion",
            "warning": "Behavior tags are descriptive heuristics, not proof of bots, snipers, bundles, insider status, or coordination. Missing observations can materially change wallet rankings and concentration.",
        },
    }
