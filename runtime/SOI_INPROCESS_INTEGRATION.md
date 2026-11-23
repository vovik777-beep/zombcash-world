# SOI integration in the existing ZombieCash bot

SOI (Solana Outbreak Intelligence) runs as a scheduled job inside the existing
bot.py Telegram application. It does not start a second bot, daemon, FadeHost
app, or OS process.

## Runtime path

1. The existing Telegram bot starts through the unchanged start.sh process list.
2. Its existing python-telegram-bot JobQueue schedules soi.runtime.run_cycle
   every 60 seconds by default; SOI_POLL_SECONDS can override this (60..3600).
   The first-hour snapshot queue runs separately every 15 seconds.
3. The bounded pipeline stores observations and first-hour snapshot tasks in
   /data/soi/soi.sqlite. It does not reuse ZombieCash game or bot state files.
4. The collection job writes /data/soi/daily-report.json atomically. Partial source failures
   remain visible as a partial report rather than suppressing publication.
5. The already-running api_server.py exposes the report read-only at
   /api/soi/report. The Russian site reads it from the existing FadeHost API.
6. The public page remains /ru/zombcash-market-data.html; no separate page or
   process is introduced.

## Existing collectors remain in place

- boost_monitor.py continues its existing 15-second DexScreener boost checks and writes distinct observed boost states into the SOI archive; SOI does not poll that boost endpoint a second time.
- news_worker.py continues its existing RSS/news cycle.
- radar_bot.py remains the on-demand Meme Radar bot.
- SOI adds bounded Pump.fun/PumpSwap on-chain event capture, market/promotion
  observations, holder/mint state, wallet funding evidence, first-hour snapshots,
  and conservative daily aggregation. It does not replace or duplicate the
  existing Telegram publishing schedules.

## Runtime defaults (unless FadeHost environment variables override them)

- SOI collection interval: 60 seconds; first-hour snapshot queue: 15 seconds.
- Pump.fun / PumpSwap RPC limits: 15 signatures per source per cycle.
- Pump.fun / PumpSwap bootstrap pages: 2 each.
- RPC pause: 1.1 seconds between transaction reads.
- Pool state snapshots: 3; market snapshots: 10; holder snapshots: 3.
- Funding wallets: 2; funding lookback: 24 hours.
- Snapshot queue: up to 10 due tasks per 15-second tick; the collection pipeline
  separately processes up to 5 due tasks per collection cycle.
- SOI_ENABLED=false is an emergency switch; default is enabled.

SOI is read-only: no wallet signing, token purchases, or transaction submission.
Unknown or unobserved data is not presented as a negative finding.
