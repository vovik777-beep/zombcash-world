"""Deterministic first-hour trade summaries from observed Pump.fun TradeEvents."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable


LAMPORTS_PER_SOL = 1_000_000_000


def summarize_first_hour_trades(observations: Iterable[object], launch_at: str,
                                mint: str | None = None) -> dict:
    """Summarize observed trade observations for [launch_at, launch_at + 60m).

    `sol_amount` is reported in SOL only when the event's quote mint is native SOL
    (wrapped SOL mint). Non-SOL quote events are counted but excluded from SOL volume.
    """
    start = datetime.fromisoformat(launch_at.replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    end = start + timedelta(minutes=60)
    wsol = "So11111111111111111111111111111111111111112"
    buys, sells = [], []
    sol_buy_lamports = sol_sell_lamports = 0
    unique_buyers, unique_sellers = set(), set()
    excluded_non_sol_quote = 0

    for obs in observations:
        if getattr(obs, "kind", "") not in ("trade_buy", "trade_sell"):
            continue
        if mint is not None and getattr(obs, "mint", None) != mint:
            continue
        event_at = getattr(obs, "event_at", None)
        if not event_at:
            continue
        stamp = datetime.fromisoformat(event_at.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if not (start <= stamp < end):
            continue
        payload = getattr(obs, "payload", {}) or {}
        event = payload.get("event", {})
        user = event.get("user")
        is_buy = getattr(obs, "kind") == "trade_buy"
        (buys if is_buy else sells).append(obs)
        if user:
            (unique_buyers if is_buy else unique_sellers).add(user)
        if event.get("quote_mint") == wsol:
            amount = int(event.get("sol_amount", 0))
            if is_buy:
                sol_buy_lamports += amount
            else:
                sol_sell_lamports += amount
        else:
            excluded_non_sol_quote += 1

    return {
        "window_start": start.astimezone(timezone.utc).isoformat(),
        "window_end": end.astimezone(timezone.utc).isoformat(),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "unique_buyers": len(unique_buyers),
        "unique_sellers": len(unique_sellers),
        "sol_buy_volume": sol_buy_lamports / LAMPORTS_PER_SOL,
        "sol_sell_volume": sol_sell_lamports / LAMPORTS_PER_SOL,
        "sol_total_volume": (sol_buy_lamports + sol_sell_lamports) / LAMPORTS_PER_SOL,
        "non_sol_quote_trades_excluded_from_sol_volume": excluded_non_sol_quote,
        "mint_filter": mint,
        "method": "Pump.fun TradeEvent.sol_amount; native SOL quote only; event time in [launch, launch+60m); optional mint filter",
    }
