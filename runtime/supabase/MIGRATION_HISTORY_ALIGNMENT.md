# Supabase migration history alignment

This is a source-only alignment against the connected production project's `supabase_migrations.schema_migrations` records inspected on 2026-11-28.

## What this change does

- Renames migration files whose SQL statements match the recorded applied migration (ignoring comments and formatting only) so the local 14-digit filename version matches the version already recorded by Supabase.
- Restores the exact SQL for applied version `20261127185058` from the production migration history because that applied migration was missing from the repository source.
- Does not execute SQL, modify the database, change wallet records, or touch production settings.

## Deliberately left as pending source migrations

These local files differ from the already-applied database statements or have no corresponding applied version, so they are **not** renamed to an applied version:

- `20261120140000_sniper_demo_paper_isolation.sql`
- `20261120143000_sniper_demo_paper_sell_amount.sql`
- `20261127185228_sniper_paper_legacy_price_scale_and_reopen.sql`
- `20261128233100_sniper_enforce_entry_social_and_buy_count.sql`
- `20261128233200_sniper_live_exit_retry_cooldown.sql`

Review these files individually before any production migration run. In particular, `20261127185228` contains data updates that reopen selected legacy Paper positions and must not be applied casually.

## Important

This alignment is not authorization to run `supabase db push` or any production migration. Obtain explicit approval first, inspect the pending SQL, and verify the migration plan against the current production history. The runtime should remain Paper-only until all Live readiness checks pass and Live activation is separately approved.
