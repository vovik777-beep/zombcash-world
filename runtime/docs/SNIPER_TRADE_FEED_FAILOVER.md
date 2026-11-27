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
