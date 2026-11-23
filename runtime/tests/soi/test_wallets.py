from soi.models import Observation
from soi.analytics.wallets import analyze_early_wallets, WSOL

MINT = "WalletTestMint"
BASE = "2026-11-17T10:00:00+00:00"


def ob(source, ref, kind, at, event, slot=1):
    return Observation(source, ref, at, at, kind, MINT,
        {"signature": ref.split(":")[0], "slot": slot, "event": event})


def test_wallet_summary_uses_observed_wsol_and_descriptive_tags_only():
    items = [
        ob("pumpfun_rpc", "launch:0", "token_created", BASE, {"mint": MINT}),
        ob("pumpfun_rpc", "buy-a:0", "trade_buy", "2026-11-17T10:00:10+00:00",
           {"user": "WalletA", "quote_mint": WSOL, "sol_amount": 2_000_000_000}, 10),
        ob("pumpswap_rpc", "buy-a2:0", "trade_buy", "2026-11-17T10:00:30+00:00",
           {"user": "WalletA", "pool": "P", "quote_amount_in": 1_000_000_000, "quote_mint": WSOL}, 11),
        ob("pumpswap_rpc", "sell-a:0", "trade_sell", "2026-11-17T10:02:00+00:00",
           {"user": "WalletA", "pool": "P", "quote_amount_out": 500_000_000, "quote_mint": WSOL}, 12),
        ob("pumpfun_rpc", "buy-b:0", "trade_buy", "2026-11-17T10:05:00+00:00",
           {"user": "WalletB", "quote_mint": "USDC", "sol_amount": 99_000_000_000}, 13),
    ]
    result = analyze_early_wallets(items, MINT)
    a = next(x for x in result["wallets"] if x["wallet"] == "WalletA")
    b = next(x for x in result["wallets"] if x["wallet"] == "WalletB")
    assert result["wallet_count"] == 2
    assert a["sol_buy_volume"] == 3
    assert a["sol_sell_volume"] == 0.5
    assert a["behavior_tags"] == ["early_entry", "repeat_buyer", "rapid_buy_to_sell", "both_sides_observed"]
    assert b["sol_buy_volume"] == 0
    assert result["non_wsol_or_unresolved_quote_trades"] == 1
    assert result["top1_share_known_sol_buy_volume"] == 1
    assert result["methodology"]["sniper_or_bundle_classification"] is False


def test_wallet_metrics_are_unknown_without_confirmed_quote_amounts():
    items = [
        ob("pumpfun_rpc", "launch:0", "token_created", BASE, {"mint": MINT}),
        ob("pumpswap_rpc", "buy:0", "trade_buy", "2026-11-17T10:00:10+00:00",
           {"user": "WalletA", "pool": "unknown", "quote_amount_in": 10}),
    ]
    result = analyze_early_wallets(items, MINT)
    assert result["known_sol_buy_volume"] == 0
    assert result["top1_share_known_sol_buy_volume"] is None
    assert result["non_wsol_or_unresolved_quote_trades"] == 1



def test_first_buy_signature_tracks_earliest_buy_even_when_observations_unsorted():
    items = [
        ob("pumpfun_rpc", "launch:0", "token_created", BASE, {"mint": MINT}),
        ob("pumpfun_rpc", "buy-later:0", "trade_buy", "2026-11-17T10:00:30+00:00",
           {"user": "WalletA", "quote_mint": WSOL, "sol_amount": 1_000_000_000}),
        ob("pumpfun_rpc", "buy-first:0", "trade_buy", "2026-11-17T10:00:10+00:00",
           {"user": "WalletA", "quote_mint": WSOL, "sol_amount": 1_000_000_000}),
    ]
    result = analyze_early_wallets(items, MINT)
    wallet = result["wallets"][0]
    assert wallet["first_buy_seconds"] == 10
    assert wallet["first_buy_signature"] == "buy-first"


def test_equal_time_first_buys_with_different_signatures_mark_anchor_ambiguous():
    items = [
        ob("pumpfun_rpc", "launch:0", "token_created", BASE, {"mint": MINT}),
        ob("pumpfun_rpc", "buy-a:0", "trade_buy", "2026-11-17T10:00:10+00:00",
           {"user": "WalletA", "quote_mint": WSOL, "sol_amount": 1_000_000_000}),
        ob("pumpswap_rpc", "buy-b:0", "trade_buy", "2026-11-17T10:00:10+00:00",
           {"user": "WalletA", "quote_mint": WSOL, "quote_amount_in": 1_000_000_000}),
    ]
    wallet = analyze_early_wallets(items, MINT)["wallets"][0]
    assert wallet["first_buy_anchor_conflict"] is True
