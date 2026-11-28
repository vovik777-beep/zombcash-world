-- Allow safe retries of definitively failed Live exits without ever
-- rebroadcasting prepared, broadcasting, submitted, or unknown orders.

CREATE OR REPLACE FUNCTION public.sniper_live_exit_retry_allowed(
  p_order_id uuid,
  p_cooldown_seconds integer DEFAULT 30
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO ''
AS $function$
DECLARE
  v_status text;
  v_side text;
  v_updated_at timestamptz;
  v_cooldown integer := greatest(5, least(coalesce(p_cooldown_seconds,30),300));
BEGIN
  IF p_order_id IS NULL THEN
    RETURN true;
  END IF;

  SELECT o.status,o.side,o.updated_at
    INTO v_status,v_side,v_updated_at
    FROM sniper.live_orders o
   WHERE o.id=p_order_id;

  IF NOT FOUND OR v_side <> 'sell' THEN
    RETURN false;
  END IF;

  IF v_status='confirmed' THEN
    RETURN true;
  END IF;

  IF v_status='failed' THEN
    RETURN v_updated_at <= pg_catalog.now()-pg_catalog.make_interval(secs=>v_cooldown);
  END IF;

  -- Unknown and in-flight outcomes are never retried automatically.
  RETURN false;
END;
$function$;

CREATE OR REPLACE FUNCTION public.sniper_live_position_note_exit_attempt(
  p_position_id uuid,
  p_order_id uuid
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO ''
AS $function$
DECLARE
  v_count integer;
BEGIN
  IF p_position_id IS NULL OR p_order_id IS NULL THEN
    RETURN false;
  END IF;

  UPDATE sniper.positions p
     SET last_exit_order_id=o.id,
         updated_at=pg_catalog.now()
    FROM sniper.live_orders o
   WHERE p.id=p_position_id
     AND p.status='open'
     AND o.id=p_order_id
     AND o.telegram_id=p.telegram_id
     AND o.wallet_id=p.wallet_id
     AND o.mint=p.mint
     AND o.side='sell'
     AND o.status='failed';

  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count=1;
END;
$function$;

REVOKE ALL ON FUNCTION public.sniper_live_exit_retry_allowed(uuid,integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_exit_retry_allowed(uuid,integer) TO service_role;
REVOKE ALL ON FUNCTION public.sniper_live_position_note_exit_attempt(uuid,uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_position_note_exit_attempt(uuid,uuid) TO service_role;

CREATE OR REPLACE FUNCTION public.sniper_live_engine_health()
 RETURNS jsonb
 LANGUAGE sql
 STABLE SECURITY DEFINER
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
      (pg_catalog.to_regprocedure('public.sniper_live_open_positions()') is not null) as exit_monitoring,
      (pg_catalog.to_regprocedure('public.sniper_live_exit_retry_allowed(uuid,integer)') is not null) as exit_retry,
      (pg_catalog.to_regprocedure('public.sniper_live_position_note_exit_attempt(uuid,uuid)') is not null) as exit_attempt_recording,
      (
        pg_catalog.strpos(pg_catalog.pg_get_functiondef('public.sniper_paper_engine_process(text)'::regprocedure),'min_buy_count') > 0
        and pg_catalog.strpos(pg_catalog.pg_get_functiondef('public.sniper_live_entry_candidates()'::regprocedure),'min_buy_count') > 0
      ) as entry_buy_count_filter,
      (
        pg_catalog.strpos(pg_catalog.pg_get_functiondef('public.sniper_paper_engine_process(text)'::regprocedure),'require_socials') > 0
        and pg_catalog.strpos(pg_catalog.pg_get_functiondef('public.sniper_live_entry_candidates()'::regprocedure),'require_socials') > 0
      ) as entry_social_filter
  )
  select pg_catalog.jsonb_build_object(
    'ready', fill_ledger and wallet_bound_orders and cost_basis and movement_tracking
      and order_prepare and order_transition and order_wallet_secret and fill_accounting
      and price_marking and fill_reconciliation and unknown_order_reconciliation and entry_candidates and exit_monitoring
      and exit_retry and exit_attempt_recording and entry_buy_count_filter and entry_social_filter,
    'checks', pg_catalog.to_jsonb(checks)
  )
  from checks;
$function$

REVOKE ALL ON FUNCTION public.sniper_live_engine_health() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_engine_health() TO service_role;
