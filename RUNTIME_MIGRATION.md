# ZombieCash full runtime migration

This branch stages the operational bot/runtime source under `runtime/` while preserving the static website files at the repository root.

## Source and scope

- Source repository: `vovik777-beep/zombcash-bot`, branch `main`, source commit `28fb0b1dfedaa9a88b1e99db37ad06a224a6f608`.
- Destination repository: `vovik777-beep/zombcash-world`, branch `migration/full-bot-public`.
- Migrated source includes the API, existing Telegram bot and handlers, RSS worker, Meme Radar, Boost Monitor, optional Moltbook, LINE modules, TV API, Sniper modules, SOI source package, 51 tests, documentation and Supabase SQL history.
- The static website and existing public publisher package remain in place.
- Root `start.sh` is the public deployment entrypoint. It runs the copied services from `runtime/` with one Telegram polling bot and one RSS worker.

## Runtime topology and cadence

The launcher supervises:
1. API server
2. one main Telegram bot process
3. one RSS news worker
4. Meme Radar
5. Boost Monitor
6. optional Moltbook agent when `MOLTBOOK_ENABLED=true`

The source retains the hourly editorial rotation (3,600 seconds), the website spotlight cadence (10,800 seconds), and the RSS poll interval (600 seconds). These are independent schedules in the existing main bot/news worker. Do not add a second Telegram poller, scheduler, RSS worker or FadeHost app.

**SOI status:** its source and tests are retained, but the current production bot source explicitly disables SOI pending redesign. The bot does not register SOI collection jobs. The public copy was made safer: it preserves `/data/soi/` and does not delete historical SOI state on startup. Do not claim live SOI collection is running.

## Public-repository safety boundary

Not copied:
- Private Git history
- SQLite databases and other binary databases
- Mutable JSON runtime state
- Logs, diagnostic data dumps and generated reports
- Environment files, credentials, API tokens, Telegram bot tokens and private keys

The static `runtime/project_memory.json` contains public project/brand references and no credentials. A pattern scan of migrated source found no matches for common live API-key/private-key formats; dummy test credentials are limited to tests. This is not a guarantee against every possible secret format, so manual review remains required.

Hard-coded Telegram owner and group identifiers were removed from the public copy. The runtime now reads these values from environment variables:
- `ZOMBCASH_ADMIN_IDS` (comma-separated admin IDs; existing supported setting)
- `ZOMBCASH_OWNER_ID` (optional owner ID)
- `WELCOME_CHAT_ID`
- `ZOMBCASH_GROUP_CHAT_ID` (falls back to `WELCOME_CHAT_ID`)
- `ZOMBCASH_RAID_CHAT_ID`
- `MOLTBOOK_ACTIVITY_GROUP_ID`

Configure the appropriate existing values in the current FadeHost environment before cutover. Never add those values to this repository.

## Persistent state — do not reset or overwrite

Keep the existing FadeHost persistent storage in place. Do not delete, rename, recreate, or initialize over any existing `/data` files or folders. Important paths include:
- `/data/auto_post_state.json`
- `/data/auto_post_pending_images/`
- `/data/auto_post_rotation.lock`
- `/data/auto_post_history.json`
- `/data/news_delivery.sqlite3`
- `/data/news_daily_journal.json`
- `/data/community_coins.sqlite3`
- `/data/survival_stats.json`
- `/data/boost_seen_tokens.json`
- `/data/boost_monitor_initialized.json`
- `/data/raid_manager.sqlite3`
- `/data/tv/tv.sqlite3`
- `/data/soi/`

The website spotlight now resolves the existing `site_promo_state.json` in the FadeHost working directory before falling back to a runtime-local file. Keep that existing root-level file in place.

The private repository also contains a committed `community_coins.sqlite3` and `survival_stats.json` seed/state file. They were deliberately not published. Before switching repositories, verify that the current persistent copies `/data/community_coins.sqlite3` and `/data/survival_stats.json` exist and contain the latest data. If either is missing, preserve/copy it from the current private app into the existing persistent volume using a secure private transfer; do not put the database or player data in GitHub. Also preserve any other mutable root-level JSON/database files still used by the current deployment.

## Dependencies and validation

- Root `requirements.txt` installs the runtime dependencies and the existing public publisher package.
- The public full-runtime CI installs dependencies, compiles all migrated Python files, checks shell syntax, and runs the migrated runtime and publisher tests.
- Supabase schema and migration SQL are included as source history only. No SQL was executed and no Supabase database was modified by this migration.
- The private GitHub Actions billing block is no longer a blocker for validation of this public branch.

## Required before production cutover

1. Get a green public CI run and review every failure rather than weakening tests to make CI green.
2. Compare the current FadeHost environment variable names with the code and set the owner/group IDs listed above in FadeHost only.
3. Confirm all existing persistent state paths above are present and current; especially check `community_coins.sqlite3`, `survival_stats.json`, and `site_promo_state.json`.
4. Install dependencies from the public root `requirements.txt`.
5. Verify the public launcher starts the same existing service set, with one Telegram polling process and one RSS worker.
6. Perform a controlled cutover only after explicit user approval: change the existing FadeHost app's repository to `vovik777-beep/zombcash-world` and the reviewed branch/commit, preserve the same app and persistent volume, then restart that same app.
7. Verify API health, Telegram polling, RSS delivery deduplication, hourly rotation, website spotlight cadence, Radar, Boost Monitor and optional services.

## Safety status

This is a staging branch, not a production cutover. The private repository, private `main`, running FadeHost app and persistent production storage have not been modified. Do not merge, deploy, restart FadeHost, change repository settings or alter persistent state until the user explicitly approves the cutover after review.
