# ZombieCash full runtime migration

This branch stages the existing bot/runtime source under `runtime/` while preserving the static website files at the repository root.

## Source and scope

- Source repository: `vovik777-beep/zombcash-bot`, branch `main`, source commit `28fb0b1dfedaa9a88b1e99db37ad06a224a6f608`.
- Destination repository: `vovik777-beep/zombcash-world`, branch `migration/full-bot-public`.
- Runtime source files are placed under `runtime/` to avoid collisions with the static site and the existing `zombcash_publisher` package under `bot/`.
- Existing Telegram handlers, API, RSS worker, Radar, Boost Monitor, optional Moltbook, LINE, TV API, SOI and Sniper modules are preserved as source files.
- Existing Supabase schema and migration SQL are copied as source history only. They are not executed by this migration.

## Runtime layout

The root `start.sh` starts the existing service set from `runtime/`:
1. API server
2. one Telegram bot polling process
3. one RSS news worker
4. Meme Radar
5. Boost Monitor
6. optional Moltbook agent when `MOLTBOOK_ENABLED=true`

The existing hourly rotation, three-hour website spotlight, ten-minute RSS poll, SOI schedule and all current handlers remain in the copied source. No second FadeHost app or duplicate worker is introduced.

## Public-repository safety boundary

Not copied:
- SQLite databases and other binary databases
- `seen_news.json`, `survival_stats.json` and other mutable runtime state
- production logs, diagnostic data dumps and generated reports
- secrets, API tokens, bot tokens, private keys or local environment files
- private Git history

A static project description file, `runtime/project_memory.json`, is included because it contains public project/brand references, not credentials. The code scan of migrated Python, shell, SQL and workflow source found only dummy test credentials in tests/workflows; no matching live API-key or private-key patterns were found. This is a pattern scan, not a guarantee that every possible secret format is absent.

## Required before production cutover

1. Review the final diff and run public GitHub Actions checks.
2. Compare the environment-variable names used by the runtime with the current FadeHost environment; configure required values in the existing app without copying secrets into GitHub.
3. Confirm persistent storage compatibility. Preserve the existing `/data` files and folders in place, including:
   - `/data/auto_post_state.json`
   - `/data/auto_post_pending_images/`
   - `/data/auto_post_rotation.lock`
   - `/data/auto_post_history.json`
   - `/data/news_delivery.sqlite3`
   - `/data/news_daily_journal.json`
   - `/data/soi/`
4. Confirm the existing `site_promo_state.json` remains in the FadeHost working directory.
5. Install dependencies from the root `requirements.txt`.
6. Run offline compile and unit tests; do not send real Telegram messages or invoke paid providers during tests.
7. Only after explicit approval, change the existing FadeHost app's repository to `vovik777-beep/zombcash-world`, branch `migration/full-bot-public` (or the eventual reviewed main), and restart that same app.
8. Verify health endpoints, one Telegram polling process, one RSS worker, and each existing service before considering the migration complete.

## Important

This is a staging branch, not a production cutover. Do not merge, deploy, restart FadeHost, change repository settings, or alter persistent state until the user explicitly approves the cutover after review.
