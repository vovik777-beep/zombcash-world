-- Read-only list of signed Live orders whose submission outcome is still unknown.
CREATE OR REPLACE FUNCTION public.sniper_live_unknown_orders()
RETURNS TABLE(
  order_id uuid,
  telegram_id bigint,
  mint text,
  side text,
  signature text,
  chain_slot bigint
)
LANGUAGE sql
SECURITY DEFINER
SET search_path TO ''
AS $function$
  select o.id,o.telegram_id,o.mint,o.side,o.signature,o.chain_slot
    from sniper.live_orders o
   where o.status = 'unknown'
     and o.signature is not null
   order by o.updated_at asc
   limit 5;
$function$;

CREATE INDEX IF NOT EXISTS live_orders_unknown_signature_idx
  ON sniper.live_orders (updated_at)
  WHERE status = 'unknown' AND signature IS NOT NULL;

REVOKE ALL ON FUNCTION public.sniper_live_unknown_orders() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_unknown_orders() TO service_role;


-- Extend the readiness gate to include unknown-signature recovery.
CREATE OR REPLACE FUNCTION public.sniper_live_engine_health()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO ''
AS $function$
  with checks as (
    select
      (pg_catalog.to_regclass('sniper.live_order_fills') is not null) as fill_ledger,
      exists(select 1 from information_schema.columns where table_schema='sniper' and table_name='live_orders' and column_name='wallet_id' and is_nullable='NO') as wallet_bound_orders,
      exists(select 1 from information_schema.columns where table_schema='sniper' and table_name='positions' and column_name='initial_invested_sol') as cost_basis,
      exists(select 1 from information_schema.columns where table_schema='sniper' and table_name='positions' and column_name='last_movement_at') as movement_tracking,
      (pg_catalog.to_regprocedure('public.sniper_live_order_prepare(bigint,uuid,text,text,numeric,text)') is not null) as order_prepare,
      (pg_catalog.to_regprocedure('public.sniper_live_order_transition(uuid,text,text,text,bigint)') is not null) as order_transition,
      (pg_catalog.to_regprocedure('public.sniper_live_order_wallet_secret(uuid)') is not null) as order_wallet_secret,
      (pg_catalog.to_regprocedure('public.sniper_live_position_apply_fill(uuid,numeric,numeric,numeric,bigint,timestamptz)') is not null) as fill_accounting,
      (pg_catalog.to_regprocedure('public.sniper_live_position_mark(text,numeric,timestamptz)') is not null) as price_marking,
      (pg_catalog.to_regprocedure('public.sniper_live_unfilled_confirmed_orders()') is not null) as fill_reconciliation,
      (pg_catalog.to_regprocedure('public.sniper_live_unknown_orders()') is not null) as unknown_order_reconciliation,
      (pg_catalog.to_regprocedure('public.sniper_live_entry_candidates()') is not null) as entry_candidates,
      (pg_catalog.to_regprocedure('public.sniper_live_open_positions()') is not null) as exit_monitoring
  )
  select pg_catalog.jsonb_build_object(
    'ready', fill_ledger and wallet_bound_orders and cost_basis and movement_tracking
      and order_prepare and order_transition and order_wallet_secret and fill_accounting
      and price_marking and fill_reconciliation and unknown_order_reconciliation and entry_candidates and exit_monitoring,
    'checks', pg_catalog.to_jsonb(checks)
  )
  from checks;
$function$;

REVOKE ALL ON FUNCTION public.sniper_live_engine_health() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_engine_health() TO service_role;
