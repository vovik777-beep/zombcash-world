# Sniper trade-feed failover

## Behavior
- PumpDev remains the primary launch/trade source.
- PumpPortal is a secondary trade source using the same websocket connection already used for free launch and migration events.
- PumpPortal trade subscriptions are only enabled when `PUMPPORTAL_API_KEY` is configured. Without the key, the connection remains launch/migration-only and status reports `launch_only_no_key`.
- Each token trade subscription is limited to the early-entry window (60 seconds) and a maximum of 20 concurrently tracked mints.
- Both sources feed the same `sniper_trade_upsert` RPC. The RPC signature conflict guard prevents duplicate transaction processing.
- Source attribution is stored as `pumpportal`.

## FadeHost environment
Add `PUMPPORTAL_API_KEY` as a secret environment variable. This is not enabled automatically by this change. PumpPortal requires an API key linked to a wallet funded with at least 0.02 SOL for trade subscriptions; it charges 0.01 SOL per 10,000 streamed trade messages. Review the account and charges before enabling.

## Health fields
The launch API includes `pumpportalTradeEnabled`, `pumpportalTradeConnected`, `pumpportalTradeStatus`, `pumpportalTradeSubscriptions`, `pumpportalTradeEventsSeen`, `pumpportalTradePersistErrors`, and `pumpportalTradeLastError`.

## Live trading
This change only restores redundant market-data ingestion and paper-engine input. It does not arm real trading or change wallets, trade sizes, risk settings, or the global Live gate. The Live order lifecycle still requires explicit implementation and end-to-end validation before enabling real funds.

## Free Shrine trade fallback
- Shrine's keyless per-token Socket.IO `subscribe_trades` stream is used as a free independent trade source; it is not the paid Advanced Data Stream.
- The worker follows newly detected mints for the same first-minute entry window, with at most 20 concurrent subscriptions.
- Shrine's documented `trade` event is normalized from UI-unit `token_amount`, `sol_amount`, `price`, `is_buy`, `quote`, `timestamp`, and `signature`. Non-SOL quote trades are rejected.
- Events are stored through the same signature-deduplicating `sniper_trade_upsert` RPC with source `shrine`.
- The source uses one free Socket.IO connection per process. Its health is exposed as `shrineTradeStatus`, `shrineTradeSubscriptions`, and event/persistence counters.
- The source is data-only. It does not enable Live trading or alter user risk settings.
