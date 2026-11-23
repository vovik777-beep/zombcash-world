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
- Cross-post adapters: `publish_line_meme`, `publish_bluesky_post`, `_bluesky_text_fits`. LINE and Bluesky are required parity behavior for hourly rotation; they are not optional features.
- Rotation state/scheduling: rotation-state helpers, Telegram Application JobQueue, Telegram Bot API, shared `/data` state directory and POSIX `fcntl` lock.

### Extracted groundwork: image runtime and resource safety

`bot/image_runtime.py` now isolates the container-memory guard, process RSS diagnostics, memory-release helper, and Pillow normalization path. It preserves the source thresholds (maximum 65% cgroup memory usage and at least 600 MiB free headroom), refuses generation when cgroup metrics are unavailable, rejects source images above 2.5 MP, normalizes to at most 1024×1024 JPEG, and caps normalized output at 2 MB. The CI compiles the complete `bot/` tree and runs focused tests for the extracted modules.

### Extracted groundwork: Pollinations image API adapter

`bot/pollinations_image_client.py` separates the authenticated Pollinations image request from prompt composition. It preserves the production request model, 1024×1024 output, 90-second timeout, bounded 10 MB JSON response, 8 MB encoded-image and 6 MB source-image limits, one retry with a varied seed and simpler composition, and the existing Pillow normalizer. Its tests use a mocked HTTP response and do not contact Pollinations or spend API quota.

### Extracted groundwork: persistent scene history

`bot/scene_history.py` now isolates the style-specific recent-scene selector. It retains the existing eight-scene avoidance window, falls back to the full scene list when all entries are recent, keeps histories separate by style, and writes UTF-8 JSON atomically with `fsync` + `os.replace`. Tests cover style isolation, history trimming, malformed files, fallback cycling, and atomic-write cleanup. The production bot has not yet been rewired to this helper; its `/data/auto_post_history.json` remains untouched.

### Extracted groundwork: LINE queue and Bluesky publishing adapters

`bot/social_publishers.py` now contains the existing LINE shared-content-queue sync and native Bluesky image-post adapter. LINE preserves the `NEWS_PUBLISH_TOKEN` authentication, payload fields, Telegram source link, 4 MB image-embedding threshold and 20-second request timeout. Bluesky preserves app-password session creation, native JPEG blob upload (950 KB ceiling), image alt text/aspect ratio, hashtag and URL facets, and post-record creation. Credentials are read only from environment variables at runtime. Tests use mocked HTTP responses and local in-memory images; they do not publish externally.

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

**Important:** this is still staging code. The private production bot has not been rewired, no public scheduler is started, and the public implementation has not been deployed. Runtime bootstrap must explicitly provide the Gemini client and call `register_publishing_schedules(application, gemini_client)` from `bot/publisher_runtime.py` only during an approved cutover. This registers the hourly rotation and the independent three-hour website spotlight; the RSS news worker remains a separate process. The private `project_memory.json` remains runtime data and is not copied into the public repository. Do not remove or disable LINE or Bluesky during migration.

The hourly cadence is 3,600 seconds. The separate website spotlight remains 10,800 seconds. News polling remains 600 seconds. These schedules are independent.

## Important

The public workspace must be populated from reviewed source files, not by importing the private repository's history. The existing private repository remains the operational fallback until the user explicitly approves a tested cutover.
