# ZombieCash Bot — Public Source Workspace

This directory is the staged public workspace for the ZombieCash bot migration.

## Migration status

- [x] Preserve the existing ZombieCash World site at the repository root.
- [x] Start from a fresh branch based on the current public repository.
- [ ] Complete source and Git-history secret review of the private bot repository.
- [ ] Copy only reviewed application source, tests, documentation, and dependency manifests.
- [ ] Replace all credentials with environment-variable configuration and document required names only.
- [ ] Exclude runtime databases, user data, caches, generated reports, diagnostic dumps, and local state.
- [ ] Add isolated Python CI for this directory and verify it on GitHub-hosted runners.
- [ ] Keep production deployment and bot runtime pointed at the existing private repository until explicitly migrated.

## Security rules

Never commit API keys, bot tokens, private keys, wallet seeds, database files, user exports, session cookies, or production logs.

Use environment variables or the deployment platform's secret store. Example environment-variable names may be documented, but their values must never be included.

The private repository remains the source of truth during this migration. This directory must be populated from a reviewed clean export, not by importing the private repository's Git history.

## Planned layout

- `src/` — reviewed application modules
- `tests/` — automated tests
- `docs/` — public setup and operator documentation
- `requirements.txt` — reviewed runtime dependencies
- `.github/workflows/` — isolated CI for the bot workspace

## Important

The repository root contains the existing static ZombieCash World site. Do not move, replace, or reconfigure its root-level GitHub Pages deployment as part of this bot migration.
