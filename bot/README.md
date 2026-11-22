# ZombieCash Bot — Public Source Workspace

This is a staged, selective migration. The existing private bot remains the production source of truth until a separate cutover is explicitly approved.

## First migration slice: existing publishing functions only

Preserve the current production behavior without changing cadence:

1. **Editorial Telegram rotation remains hourly (every 60 minutes).** The existing four-slot content rotation is separate from website promotion. Do not change it to a three-hour interval.
2. **Website promotion runs every three hours (10,800 seconds).** This is a separate scheduled website spotlight that cycles through the site's pages, includes a page button and a link to ZombieCash HQ, and attempts to attach a screenshot. It has its own persisted state and retry behavior.
3. **News publication** remains a separate worker. It publishes sourced news to `@ZOMBCASH` and mirrors successful publications to the ZombieCash website API when the configured publishing token is present.

The three-hour cadence applies ONLY to website promotion. It must never be applied to the hourly editorial rotation or news polling/publication.

## Explicitly out of scope for this first migration

Do not migrate or refactor the following unless required as a direct dependency of the three in-scope functions and separately reviewed:

- Meme Radar and Boost Monitor
- Zombie Lab games, player accounts, leaderboards, and game state
- API server and unrelated endpoints
- Sniper/trading modules and wallet data
- SOI analytics and ingestion
- LINE, Moltbook, Bluesky, or other social integrations
- experimental AI agents, diagnostics, and internal operations tools

These remain in the private repository and continue to be maintained there.

## Migration rules

- Preserve behavior first; do not redesign stable features during the move.
- Extract only the smallest dependency set required by the three publishing functions.
- Do not copy the private repository's Git history.
- Never publish API keys, Telegram tokens, wallet/private keys, user data, runtime databases, local state, session cookies, production logs, or private diagnostic reports.
- Use environment variables / hosting secret storage. Document variable names only, never values.
- Keep the existing public site at repository root unchanged.
- Keep the existing private bot running. No production cutover, deployment, or duplicate live posting during the migration.
- Test the isolated public implementation before any cutover. Switch production only after explicit approval.

## Current layout

- `bot/` — staged publishing worker source and focused tests
- `bot/requirements.txt` — minimal dependencies for the current publishing workers
- `.github/workflows/zombcash-publisher-ci.yml` — isolated publishing CI

## Current verified source locations (private repository main)

- `auto_post_schedule.py` — hourly editorial rotation configuration (3,600 seconds).
- `bot.py` — hourly Telegram publishing and website links in captions/onboarding.
- `website_showcase.py` — separate website spotlight schedule (default 10,800 seconds / three hours).
- `news_worker.py` — independent news generation/publication and website API mirroring.
- `start.sh` — currently supervises multiple services; do not copy wholesale because it would start out-of-scope services.

## Hourly editorial rotation: extraction boundary review

The hourly publisher is implemented in `bot.py` as a flow beginning at `_auto_post_job_once`, with a lock wrapper in `auto_post_job`. It is not safe to copy this block alone: it calls shared helpers that currently live elsewhere in the monolithic bot.

Required dependencies identified from the current private `main` source:

- Image lifecycle: `generate_zombiecash_image`, pending-image persistence helpers, and `release_image_memory`.
- Caption/context: `ask_gemini`, `load_project_memory`.
- Cross-post adapters: `publish_line_meme`, `publish_bluesky_post`, `_bluesky_text_fits`.
- Rotation state/scheduling: rotation-state helpers, Telegram Application JobQueue, Telegram Bot API, shared `/data` state directory and POSIX `fcntl` lock.

### Extracted groundwork: durable pending-image state

`bot/auto_post_state.py` now isolates pending-image persistence from the monolithic bot. It retains the production safety rule: a record marked `sending` is ambiguous and is treated as already sent, so an automatic retry cannot resend it. Image bytes and metadata are written atomically. Unit tests cover save/reload, ambiguous delivery, sent-message IDs, cleanup, and supported styles.

**Important:** this module is currently isolated and tested; the private production bot has not been rewired to use it, and this public workspace does not start an hourly scheduler. The remaining image-generation, AI-caption, Telegram, retry, and cross-post adapters still need to be extracted and tested before the hourly publisher can be considered independently runnable.

The hourly cadence is 3,600 seconds. The separate website spotlight remains 10,800 seconds. News polling remains 600 seconds. These schedules are independent.

## Important

The public workspace must be populated from reviewed source files, not by importing the private repository's history. The existing private repository remains the operational fallback until the user explicitly approves a tested cutover.
