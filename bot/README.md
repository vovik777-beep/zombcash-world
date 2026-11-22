# ZombieCash Bot — Public Source Workspace

This is a staged, selective migration. The existing private bot remains the production source of truth until a separate cutover is explicitly approved.

## Migration scope: only the stable publishing functions

The public workspace is intended to contain only:

1. **Scheduled Telegram content rotation** — preserve the currently used publishing flow and its rotation state handling. The private `main` branch currently defines a 60-minute interval in `auto_post_schedule.py`. Before cutover, confirm the desired production cadence against the latest approved schedule; do not silently change it.
2. **Website promotion** — preserve the existing website links and attribution in the established Telegram post captions and onboarding/welcome messages. No new mass-promotion campaign is introduced unless separately approved.
3. **News publication** — isolate the existing `news_worker.py` flow that prepares news posts for `@ZOMBCASH` and mirrors successful publications to the ZombieCash website API.

## Explicitly out of scope for this first migration

Do not migrate or refactor the following unless needed as a direct dependency of the three publishing functions and separately reviewed:

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
- Extract only the smallest dependency set required by the three in-scope functions.
- Do not copy the private repository's Git history.
- Never publish API keys, Telegram tokens, wallet/private keys, user data, runtime databases, local state, session cookies, production logs, or private diagnostic reports.
- Use environment variables / hosting secret storage. Document variable names only, never values.
- Keep the existing public site at repository root unchanged.
- Keep the existing private bot running. No production cutover, deployment, or duplicate live posting during the migration.
- Test the isolated public implementation before any cutover. Switch production only after explicit approval.

## Planned layout

- `src/publisher/` — scheduled content, website links, and news publishing
- `tests/` — focused regression tests for the publishing flows
- `docs/` — public deployment and configuration instructions
- `requirements.txt` — only dependencies required by this publishing slice
- `.github/workflows/` — isolated CI for the publishing slice

## Current verified source locations

- `auto_post_schedule.py` — shared post interval and content rotation.
- `bot.py` — scheduled Telegram publishing and website links in captions/onboarding.
- `news_worker.py` — news generation/publication and website API mirroring.
- `start.sh` — currently supervises multiple services; do not copy wholesale because it would start out-of-scope services.

## Important

The public workspace must be populated from reviewed source files, not by importing the private repository's history. The existing private repository remains the operational fallback until the user explicitly approves a tested cutover.
