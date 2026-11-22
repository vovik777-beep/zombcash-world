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

## Planned layout

- `src/publisher/` — hourly editorial rotation, three-hour website spotlight, and news publication
- `tests/` — focused regression tests for cadence, rotation, website spotlight, and news publishing
- `docs/` — public deployment and configuration instructions
- `requirements.txt` — only dependencies required by this publishing slice
- `.github/workflows/` — isolated CI for the publishing slice

## Current verified source locations (private repository main)

- `auto_post_schedule.py` — hourly editorial rotation configuration (3,600 seconds).
- `bot.py` — hourly Telegram publishing and website links in captions/onboarding.
- `website_showcase.py` — separate website spotlight schedule (default 10,800 seconds / three hours).
- `news_worker.py` — independent news generation/publication and website API mirroring.
- `start.sh` — currently supervises multiple services; do not copy wholesale because it would start out-of-scope services.

## Important

The public workspace must be populated from reviewed source files, not by importing the private repository's history. The existing private repository remains the operational fallback until the user explicitly approves a tested cutover.


## Hourly editorial rotation: extraction boundary review

The hourly publisher is implemented in `bot.py` as a 224-line flow beginning at `_auto_post_job_once`, with a lock wrapper in `auto_post_job`. It is not safe to copy this block alone: it calls shared helpers that currently live elsewhere in the monolithic bot.

Required dependencies identified from the current private `main` source:

- Image lifecycle: `generate_zombiecash_image`, `load_pending_auto_post_image`, `save_pending_auto_post_image`, `mark_pending_auto_post_image_sending`, `mark_pending_auto_post_image_sent`, `clear_pending_auto_post_image`, `release_image_memory`.
- Caption/context: `ask_gemini`, `load_project_memory`.
- Cross-post adapters: `publish_line_meme`, `publish_bluesky_post`, `_bluesky_text_fits`.
- Rotation state/scheduling: `load_next_rotation_index`, `save_rotation_success`, `schedule_next_auto_post`, `schedule_auto_post_retry`.
- Runtime integration: Telegram Application JobQueue, Telegram Bot API, shared `/data` state directory and POSIX `fcntl` lock.

Migration decision: keep the existing hourly publisher running in the private bot until these dependencies have been separated behind explicit interfaces and tested in isolation. Do not start an additional hourly scheduler from this public workspace during the transition; that would duplicate channel posts.

The hourly cadence is 3,600 seconds. The separate website spotlight remains 10,800 seconds. News polling remains 600 seconds. These schedules are independent.
