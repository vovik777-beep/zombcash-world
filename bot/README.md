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
- Moltbook and unrelated social integrations. **LINE and Bluesky cross-posting are part of the existing hourly editorial publisher and MUST be preserved**, including their current failure isolation and platform-specific formatting.
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

- `bot/` — staged publishing worker source and focused tests; packaged for installation as `zombcash_publisher`
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
- Cross-post adapters: `publish_line_meme`, `publish_bluesky_post`, `_bluesky_text_fits`. LINE and Bluesky are required parity behavior for hourly rotation; they are not optional features.
- Rotation state/scheduling: rotation-state helpers, Telegram Application JobQueue, Telegram Bot API, shared `/data` state directory and POSIX `fcntl` lock.

### Extracted groundwork: image runtime and resource safety

`bot/image_runtime.py` now isolates the container-memory guard, process RSS diagnostics, memory-release helper, and Pillow normalization path. It preserves the source thresholds (maximum 65% cgroup memory usage and at least 600 MiB free headroom), refuses generation when cgroup metrics are unavailable, rejects source images above 2.5 MP, normalizes to at most 1024×1024 JPEG, and caps normalized output at 2 MB. The CI compiles the complete `bot/` tree and runs focused tests for the extracted modules.

### Extracted groundwork: Pollinations image API adapter

`bot/pollinations_image_client.py` separates the authenticated Pollinations image request from prompt composition. It preserves the production request model, 1024×1024 output, 90-second timeout, bounded 10 MB JSON response, 8 MB encoded-image and 6 MB source-image limits, one retry with a varied seed and simpler composition, and the existing Pillow normalizer. Its tests use a mocked HTTP response and do not contact Pollinations or spend API quota.

### Extracted groundwork: persistent scene history

`bot/scene_history.py` now isolates the style-specific recent-scene selector. It retains the existing eight-scene avoidance window, falls back to the full scene list when all entries are recent, keeps histories separate by style, and writes UTF-8 JSON atomically with `fsync` + `os.replace`. Tests cover style isolation, history trimming, malformed files, fallback cycling, and atomic-write cleanup. The production bot has not yet been rewired to this helper; its `/data/auto_post_history.json` remains untouched.

### Extracted groundwork: LINE queue and Bluesky publishing adapters

`bot/social_publishers.py` now contains the existing LINE shared-content-queue sync, native Bluesky image-post adapter, and non-publishing Bluesky session diagnostic. `bot/bluesky_admin.py` restores the admin-only `/bskycheck` command and explicitly reports that no post is published during authentication checks. LINE preserves the `NEWS_PUBLISH_TOKEN` authentication, payload fields, Telegram source link, 4 MB image-embedding threshold and 20-second request timeout. Bluesky preserves app-password session creation, native JPEG blob upload (950 KB ceiling), image alt text/aspect ratio, hashtag and URL facets, and post-record creation. Credentials are read only from environment variables at runtime. Tests use mocked HTTP responses and local in-memory images; they do not publish externally.

### Extracted content and cross-post formatting

`bot/auto_post_content.py` isolates the three existing editorial voices, exact caption-generation instructions, Telegram HTML caption/footer (including CA, website, X and Telegram links), and Bluesky post composition. Bluesky formatting removes hashtags only when needed, preserves the complete generated caption, and skips rather than truncates if the platform limit still cannot be met. Tests cover all three styles, escaping, link retention, and length behavior.

### Original image prompts and guarded generation

`bot/image_prompt.py` was extracted directly from the private production `bot.py`: the original HORROR, CARTOON and PHOTO_PINUP scene lists, visual directions, wardrobe variants, composition options, anatomy rules and branding constraints are retained. `bot/image_service.py` joins that prompt builder to the Pollinations image client and production memory guard, returning the same `BytesIO` metadata contract (`zombcash_scene`, `zombcash_meme.jpg`) expected by the publisher.

### Project memory and AI caption fallback

`bot/system_prompt.py` contains only the original production system prompt. `bot/project_memory.py` reads the runtime project memory without copying it into the public repository; configure `ZOMBCASH_PROJECT_MEMORY_FILE` or place the file beside the module in the private runtime. `bot/pollinations_text_client.py` retains the OpenAI-compatible Pollinations text request. `bot/caption_generator.py` combines system prompt + project memory + task and uses `bot/ai_text_client.py` for the original Gemini model order, two attempts for transient failures, quota detection, parsed retry-after cooldown (minimum five minutes), and Pollinations fallback. Tests use fake providers and temporary files only.

### Extracted groundwork: durable pending-image state

`bot/auto_post_state.py` now isolates pending-image persistence from the monolithic bot. It retains the production safety rule: a record marked `sending` is ambiguous and is treated as already sent, so an automatic retry cannot resend it. Image bytes and metadata are written atomically. Unit tests cover save/reload, ambiguous delivery, sent-message IDs, cleanup, and supported styles.

### Integrated hourly publisher (staging only)

`bot/hourly_publisher.py` now wires image generation, persistent pending-image recovery, the original caption prompt and AI fallback, Telegram image delivery, LINE website/API queue sync, Bluesky native-image cross-posting, retry backoff, POSIX process lock, rotation-state persistence and elapsed-time restoration for the hourly schedule. Telegram success is authoritative: LINE or Bluesky failure is isolated and cannot roll back the successful Telegram post. An ambiguous `sending` marker is finalized without re-sending the image. Integration tests use fake Telegram delivery and mocked cross-post failures; they verify the hourly slot advances and the three-hour website cadence is not involved.

**Important:** this is still staging code. The private production bot has not been rewired, no public scheduler is started, and the public implementation has not been deployed. Runtime bootstrap must explicitly provide the Gemini client and the existing admin-check callback, then call `register_publishing_schedules(application, gemini_client, is_admin_fn)` from `bot/publisher_runtime.py` only during an approved cutover. This registers the hourly rotation and the independent three-hour website spotlight; the RSS news worker remains a separate process. The private `project_memory.json` remains runtime data and is not copied into the public repository. Do not remove or disable LINE or Bluesky during migration.

The hourly cadence is 3,600 seconds. The separate website spotlight remains 10,800 seconds. News polling remains 600 seconds. These schedules are independent.

## Important

The public workspace must be populated from reviewed source files, not by importing the private repository's history. The existing private repository remains the operational fallback until the user explicitly approves a tested cutover.


## FadeHost deployment constraint — current plan

The existing FadeHost Starter account has one production bot slot. Do **not** create a second FadeHost app, upgrade the plan, or change the current app's repository as part of this migration.

The current FadeHost app is still connected to the private repository `vovik777-beep/zombcash-bot`, branch `main`. Its root `start.sh` supervises several services:

- `api_server.py` — project API
- `bot.py` — main Telegram bot, handlers, hourly editorial rotation, website spotlight and other bot features
- `news_worker.py` — separate RSS news publisher
- `radar_bot.py` — Meme Radar
- `boost_monitor.py` — Solana Boost Monitor
- optional `moltbook_agent.py` when `MOLTBOOK_ENABLED=true`

This public migration slice intentionally does **not** contain the API server, main bot handlers, Radar, Boost Monitor or the optional Moltbook agent. Therefore, pointing the existing FadeHost app directly at this repository/branch would remove working services. Do not do that.

The public branch is currently for source review and offline CI only. There is no live staging deployment on the current FadeHost plan.

## Safe validation boundary

- The GitHub Actions suite compiles the public publishing package and runs mocked/unit tests. It does not contact Telegram, Gemini, Pollinations, LINE, Bluesky or the website API.
- `python -m zombcash_publisher.publisher_main` is a staging-only Telegram polling entry point. It requires a separate staging bot token and private test channel. Do not run it on the existing FadeHost app or alongside the production `bot.py` polling process.
- `python -m zombcash_publisher.news_worker` is a separate RSS process. The existing production `start.sh` already starts its own private-repository `news_worker.py`; do not start this public worker as an additional production worker.
- Never run a second production hourly scheduler or RSS publisher.
- Do not send real test posts, generate paid images, or authenticate external providers during offline CI.

## Production state that must remain untouched

The existing production runtime uses persistent files under `/data`, including:

- `/data/auto_post_state.json` — hourly rotation timestamp and next slot
- `/data/auto_post_pending_images/` — pending/sending/sent image recovery records
- `/data/auto_post_rotation.lock` — cross-process hourly publisher lock
- `/data/auto_post_history.json` — recent scene history
- `/data/news_delivery.sqlite3` — news delivery/idempotency ledger
- `/data/news_daily_journal.json` — daily news journal

The current private runtime also reads `project_memory.json` beside `bot.py`; the existing website spotlight state is stored beside the private `website_showcase.py` module. These are live runtime data, not source files to copy into this public repository.

Do not delete, rename, reset, overwrite, or move these files. Do not copy their contents, production logs, secrets, user data or session data into the public workspace.

## Requirements before any future cutover

The public publishing modules are not a drop-in replacement for the private production application. A future cutover must first be designed as an integration into the existing single-app runtime, preserving all currently supervised services and Telegram handlers while replacing—not duplicating—the hourly and website scheduling paths and retaining the separate RSS worker exactly once.

The Python import-name collision with the private top-level `bot.py` has been addressed in the public workspace: `pyproject.toml` installs the source under the unique import namespace `zombcash_publisher`, while source modules use package-relative imports. The repository folder remains `bot/` so the existing focused tests can run unchanged. CI installs the distribution and smoke-tests imports from `zombcash_publisher`. The private runtime has not been modified, and this packaging change does not authorize a deployment or cutover.

Before any cutover is considered:

1. Review source parity for hourly publishing, website spotlight and RSS news against the current private `main`.
2. Verify that the public modules use the existing persistent state formats and paths without resetting them.
3. Prepare an explicit integration/startup plan that retains `api_server.py`, `bot.py` handlers, `radar_bot.py`, `boost_monitor.py`, optional Moltbook, and the independent RSS worker.
4. Keep the existing private deployment as the operational source of truth until the user explicitly approves a cutover.
5. Do not merge PR #1, change FadeHost's GitHub connection, redeploy, restart production, or send live publications without explicit approval.

The three cadences remain independent: hourly editorial rotation = 3,600 seconds; website spotlight = 10,800 seconds; RSS polling = 600 seconds with at most one eligible story per cycle.


## Collision-free Python package namespace

Install the public source with `python -m pip install .` and import it as `zombcash_publisher` (not `bot`). The source remains under `bot/` for the isolated test suite, but the distribution maps that directory to the unique runtime package name. This avoids collision with the private application's top-level `bot.py`. The CI workflow verifies the installed namespace before running regression tests.


## Existing private host integration note (reviewed 2026-11-23)

The current private `bot.py` already registers an admin-only `/bskycheck` command. When integrating this package into that host, call `register_publishing_schedules(application, client, is_admin, register_admin_command=False)` so the existing handler is retained and no duplicate `/bskycheck` handler is added. The host's existing `is_admin(update)` callback and Gemini client are passed through; do not create a second Telegram application or polling loop.

The private startup currently schedules the hourly job and website spotlight directly. During an approved cutover, replace those two startup registrations with the package registration call above; `register_hourly_publisher` removes/replaces the existing `zombcash_auto_post_rotation` job, and the website registration clears its stale named job before restoring its own cadence. Do not leave both old and new schedule registrations in place.

For the RSS worker, keep exactly one process in the existing `start.sh` supervision tree. The public worker requires `ZOMBCASH_ENV=production` explicitly; if/when its source replaces the private worker, update the existing single News worker command rather than adding another supervisor entry. Preserve the existing `/data/news_delivery.sqlite3` and `/data/news_daily_journal.json` state paths.
