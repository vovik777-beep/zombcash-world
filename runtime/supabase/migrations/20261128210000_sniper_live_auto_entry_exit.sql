-- Add movement tracking and the same first-valid-buy launch policy used by Paper,
-- but only for users who explicitly armed Live trading.
ALTER TABLE sniper.positions
  ADD COLUMN IF NOT EXISTS last_price_sol numeric,
  ADD COLUMN IF NOT EXISTS last_movement_at timestamptz;

UPDATE sniper.positions
   SET last_price_sol = coalesce(last_price_sol,last_mark_price,entry_price),
       last_movement_at = coalesce(last_movement_at,last_mark_at,opened_at)
 WHERE last_price_sol IS NULL OR last_movement_at IS NULL;

CREATE OR REPLACE FUNCTION public.sniper_live_position_init_movement()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO ''
AS $function$
begin
  if new.last_price_sol is null then
    new.last_price_sol := coalesce(new.last_mark_price,new.entry_price);
  end if;
  if new.last_movement_at is null then
    new.last_movement_at := coalesce(new.last_mark_at,new.opened_at,pg_catalog.now());
  end if;
  return new;
end;
$function$;

DROP TRIGGER IF EXISTS sniper_live_position_init_movement_trigger ON sniper.positions;
CREATE TRIGGER sniper_live_position_init_movement_trigger
BEFORE INSERT ON sniper.positions
FOR EACH ROW EXECUTE FUNCTION public.sniper_live_position_init_movement();

CREATE OR REPLACE FUNCTION public.sniper_live_position_mark(
  p_mint text,
  p_price_sol numeric,
  p_observed_at timestamptz DEFAULT NULL
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO ''
AS $function$
declare
  v_count integer;
  v_observed_at timestamptz := coalesce(p_observed_at,pg_catalog.now());
begin
  if p_mint is null or p_price_sol is null or p_price_sol <= 0 then
    raise exception 'invalid_live_position_mark';
  end if;
  update sniper.positions p
     set last_mark_price = p_price_sol,
         last_mark_at = v_observed_at,
         last_movement_at = case
           when p.last_price_sol is null or p.last_price_sol <= 0 then coalesce(p.last_movement_at,v_observed_at)
           when abs((p_price_sol / p.last_price_sol - 1) * 100) >= coalesce(s.min_price_move_pct,0.5) then v_observed_at
           else p.last_movement_at
         end,
         last_price_sol = p_price_sol,
         expected_exit_sol = p.quantity * p_price_sol,
         gross_pnl_sol = coalesce(p.realized_proceeds_sol,0) + (p.quantity * p_price_sol) - coalesce(p.initial_invested_sol,p.invested_sol),
         net_pnl_sol = coalesce(p.realized_proceeds_sol,0) + (p.quantity * p_price_sol) - coalesce(p.initial_invested_sol,p.invested_sol) - coalesce(p.total_fees_sol,0),
         net_pnl_pct = case
           when coalesce(p.initial_invested_sol,p.invested_sol) > 0
           then ((coalesce(p.realized_proceeds_sol,0) + (p.quantity * p_price_sol) - coalesce(p.initial_invested_sol,p.invested_sol) - coalesce(p.total_fees_sol,0)) / coalesce(p.initial_invested_sol,p.invested_sol)) * 100
           else 0 end,
         updated_at = pg_catalog.now()
    from sniper.user_settings s
   where p.telegram_id = s.telegram_id
     and p.mint = p_mint
     and p.status = 'open';
  get diagnostics v_count = row_count;
  return v_count;
end;
$function$;

DROP FUNCTION IF EXISTS public.sniper_live_open_positions();
CREATE FUNCTION public.sniper_live_open_positions()
RETURNS TABLE(
  position_id uuid,
  telegram_id bigint,
  wallet_id uuid,
  mint text,
  quantity numeric,
  invested_sol numeric,
  initial_invested_sol numeric,
  realized_proceeds_sol numeric,
  entry_price numeric,
  last_mark_price numeric,
  last_price_sol numeric,
  last_mark_at timestamptz,
  last_movement_at timestamptz,
  opened_at timestamptz,
  total_fees_sol numeric,
  net_pnl_pct numeric,
  take_profit_pct numeric,
  stop_loss_pct numeric,
  idle_exit_seconds integer,
  min_price_move_pct numeric,
  max_slippage_bps integer,
  live_armed boolean,
  enabled boolean,
  mode text,
  last_exit_order_id uuid
)
LANGUAGE sql
SECURITY DEFINER
SET search_path TO ''
AS $function$
  select p.id,p.telegram_id,p.wallet_id,p.mint,p.quantity,p.invested_sol,
         p.initial_invested_sol,p.realized_proceeds_sol,p.entry_price,
         p.last_mark_price,p.last_price_sol,p.last_mark_at,p.last_movement_at,
         p.opened_at,p.total_fees_sol,p.net_pnl_pct,s.take_profit_pct,
         s.stop_loss_pct,s.idle_exit_seconds,s.min_price_move_pct,
         s.max_slippage_bps,s.live_armed,s.enabled,s.mode,p.last_exit_order_id
    from sniper.positions p
    left join sniper.user_settings s on s.telegram_id = p.telegram_id
   where p.status = 'open';
$function$;

CREATE OR REPLACE FUNCTION public.sniper_live_entry_candidates()
RETURNS TABLE(
  telegram_id bigint,
  mint text,
  launch_at timestamptz,
  entry_at timestamptz,
  entry_price_sol numeric,
  entry_trader text,
  max_trade_sol numeric,
  max_slippage_bps integer
)
LANGUAGE sql
SECURITY DEFINER
SET search_path TO ''
AS $function$
  select s.telegram_id,l.mint,l.event_at,entry.event_at,entry.price_sol,entry.trader,
         s.max_trade_sol,s.max_slippage_bps
    from sniper.user_settings s
    join sniper.wallets w
      on w.telegram_id = s.telegram_id
     and w.status = 'active'
     and w.custody_mode = 'managed'
     and w.vault_secret_id is not null
    join sniper.launches l
      on l.event_at <= pg_catalog.now()
     and pg_catalog.now() <= l.event_at + pg_catalog.make_interval(secs=>s.buy_window_seconds)
    cross join lateral (
      select t.event_at,t.price_sol,t.trader
        from sniper.token_trades t
       where t.mint = l.mint
         and t.side = 'buy'
         and t.price_sol > 0
         and t.event_at >= l.event_at
         and t.event_at <= l.event_at + pg_catalog.make_interval(secs=>s.buy_window_seconds)
       order by t.event_at asc
       limit 1
    ) entry
   where s.mode = 'live'
     and s.enabled = true
     and s.live_armed = true
     and s.live_confirmed_at is not null
     and not exists (
       select 1 from sniper.positions p
        where p.telegram_id = s.telegram_id
          and p.mint = l.mint
     )
     and not exists (
       select 1 from sniper.live_orders o
        where o.telegram_id = s.telegram_id
          and o.mint = l.mint
          and o.side = 'buy'
     )
     and (
       select count(*) from sniper.positions p
        where p.telegram_id = s.telegram_id
          and p.status in ('buy_pending','open','sell_pending')
     ) + (
       select count(*) from sniper.live_orders o
        where o.telegram_id = s.telegram_id
          and o.side = 'buy'
          and (
            o.status in ('prepared','broadcasting','submitted','unknown')
            or (o.status = 'confirmed' and not exists (
              select 1 from sniper.live_order_fills f where f.order_id = o.id
            ))
          )
     ) < s.max_open_positions
     and not (
       s.max_daily_loss_sol > 0
       and abs(least(0,coalesce((
         select sum(p.net_pnl_sol) filter(where p.net_pnl_sol < 0)
           from sniper.positions p
          where p.telegram_id = s.telegram_id
            and p.status = 'closed'
            and p.closed_at >= pg_catalog.date_trunc('day',pg_catalog.now())
       ),0))) >= s.max_daily_loss_sol
     )
     and not (
       s.creator_policy = 'first'
       and l.creator is not null
       and (select count(*) from sniper.launches history where history.creator = l.creator) > 1
     )
     and not (
       s.creator_policy = 'repeat'
       and (l.creator is null
         or (select count(*) from sniper.launches history where history.creator = l.creator) <= 1)
     )
   order by l.event_at asc
   limit 100;
$function$;

CREATE INDEX IF NOT EXISTS live_orders_user_mint_side_idx
  ON sniper.live_orders (telegram_id,mint,side);

CREATE INDEX IF NOT EXISTS token_trades_mint_side_event_at_idx
  ON sniper.token_trades (mint,side,event_at);

REVOKE ALL ON FUNCTION public.sniper_live_position_init_movement() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.sniper_live_position_mark(text,numeric,timestamptz) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_position_mark(text,numeric,timestamptz) TO service_role;
REVOKE ALL ON FUNCTION public.sniper_live_open_positions() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_open_positions() TO service_role;
REVOKE ALL ON FUNCTION public.sniper_live_entry_candidates() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_entry_candidates() TO service_role;


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
      (pg_catalog.to_regprocedure('public.sniper_live_entry_candidates()') is not null) as entry_candidates,
      (pg_catalog.to_regprocedure('public.sniper_live_open_positions()') is not null) as exit_monitoring
  )
  select pg_catalog.jsonb_build_object(
    'ready', fill_ledger and wallet_bound_orders and cost_basis and movement_tracking
      and order_prepare and order_transition and order_wallet_secret and fill_accounting
      and price_marking and fill_reconciliation and entry_candidates and exit_monitoring,
    'checks', pg_catalog.to_jsonb(checks)
  )
  from checks;
$function$;

REVOKE ALL ON FUNCTION public.sniper_live_engine_health() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_engine_health() TO service_role;
