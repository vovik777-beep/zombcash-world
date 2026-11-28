# Supabase migration history alignment

This is a source-only alignment against the connected production project's `supabase_migrations.schema_migrations` records inspected on 2026-11-28.

## What this change does

- Renames migration files whose SQL statements match the recorded applied migration (ignoring comments and formatting only) so the local 14-digit filename version matches the version already recorded by Supabase.
- Restores exact SQL for applied versions `20261127185058`, `20261120101554`, and `20261120101615` from production migration history where the corresponding source was missing or version-misaligned.
- Does not execute SQL, modify the database, change wallet records, or touch production settings.

## Pending source migrations

The following are the only pending migration files identified by comparing local 14-digit versions with production migration history:

- `20261128233100_sniper_enforce_entry_social_and_buy_count.sql`
- `20261128233200_sniper_live_exit_retry_cooldown.sql`

The legacy price-scale/reopen migration `20261127185228_sniper_paper_legacy_price_scale_and_reopen.sql` is already recorded as applied in production. The two Demo migrations are also restored under their actual applied versions and are no longer pending.

Review the two new migrations individually before any production migration run. They tighten entry eligibility and add safe Live exit retry/readiness support.

## Important

This alignment is not authorization to run `supabase db push` or any production migration. Obtain explicit approval first, inspect the pending SQL, and verify the migration plan against the current production history. Live activation remains separate and must not occur until all readiness checks pass and Live activation is explicitly approved.
