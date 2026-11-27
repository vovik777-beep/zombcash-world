-- Confirmed Live orders are not considered complete until their actual fills
-- have been parsed and applied to the position ledger.
ALTER TABLE sniper.positions
  ADD COLUMN IF NOT EXISTS initial_invested_sol numeric NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS realized_proceeds_sol numeric NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_mark_price numeric,
  ADD COLUMN IF NOT EXISTS last_mark_at timestamptz,
  ADD COLUMN IF NOT EXISTS entry_order_id uuid REFERENCES sniper.live_orders(id),
  ADD COLUMN IF NOT EXISTS last_exit_order_id uuid REFERENCES sniper.live_orders(id);

UPDATE sniper.positions
   SET initial_invested_sol = CASE WHEN initial_invested_sol = 0 THEN coalesce(invested_sol,0) ELSE initial_invested_sol END,
       realized_proceeds_sol = coalesce(realized_proceeds_sol,0),
       last_mark_price = coalesce(last_mark_price,entry_price),
       last_mark_at = coalesce(last_mark_at,opened_at)
 WHERE initial_invested_sol = 0 OR last_mark_price IS NULL OR last_mark_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS positions_entry_order_id_unique
  ON sniper.positions (entry_order_id)
  WHERE entry_order_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS positions_one_open_wallet_mint_idx
  ON sniper.positions (telegram_id,wallet_id,mint)
  WHERE status IN ('buy_pending','open','sell_pending');

CREATE TABLE IF NOT EXISTS sniper.live_order_fills (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id uuid NOT NULL UNIQUE REFERENCES sniper.live_orders(id) ON DELETE RESTRICT,
  position_id uuid NOT NULL REFERENCES sniper.positions(id) ON DELETE RESTRICT,
  telegram_id bigint NOT NULL,
  wallet_id uuid NOT NULL REFERENCES sniper.wallets(id) ON DELETE RESTRICT,
  mint text NOT NULL,
  side text NOT NULL CHECK (side IN ('buy','sell')),
  token_amount numeric NOT NULL CHECK (token_amount > 0),
  sol_amount numeric NOT NULL CHECK (sol_amount > 0),
  fee_sol numeric NOT NULL DEFAULT 0 CHECK (fee_sol >= 0),
  price_sol numeric NOT NULL CHECK (price_sol > 0),
  chain_slot bigint,
  block_time timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE sniper.live_order_fills ENABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS live_order_fills_position_idx
  ON sniper.live_order_fills (position_id,created_at DESC);

CREATE INDEX IF NOT EXISTS live_orders_confirmed_unfilled_idx
  ON sniper.live_orders (telegram_id,side,mint)
  WHERE status = 'confirmed';

CREATE OR REPLACE FUNCTION public.sniper_live_order_prepare(
  p_telegram_id bigint,
  p_client_order_id uuid,
  p_mint text,
  p_side text,
  p_amount numeric,
  p_amount_unit text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO ''
AS $function$
declare
  v_settings sniper.user_settings%rowtype;
  v_order sniper.live_orders%rowtype;
  v_wallet_id uuid;
  v_open_count integer;
  v_pending_buys integer;
  v_daily_loss numeric;
  v_available_tokens numeric;
begin
  if p_telegram_id is null or p_client_order_id is null
     or p_mint is null or pg_catalog.length(p_mint) not between 32 and 44
     or p_mint !~ '^[1-9A-HJ-NP-Za-km-z]+$'
     or p_side is null or p_side not in ('buy','sell')
     or p_amount is null or p_amount <= 0 or p_amount >= 1000000000
     or p_amount_unit is null
     or (p_side = 'buy' and p_amount_unit <> 'sol')
     or (p_side = 'sell' and p_amount_unit <> 'token') then
    raise exception 'invalid_live_order_request';
  end if;

  select * into v_settings
    from sniper.user_settings
   where telegram_id = p_telegram_id
   for update;
  if not found or not v_settings.enabled or v_settings.mode <> 'live'
     or not v_settings.live_armed or v_settings.live_confirmed_at is null then
    raise exception 'live_trading_not_armed';
  end if;

  select id into v_wallet_id
    from sniper.wallets
   where telegram_id = p_telegram_id
     and status = 'active'
     and custody_mode = 'managed'
     and vault_secret_id is not null
   order by created_at desc
   limit 1
   for update;
  if v_wallet_id is null then
    raise exception 'active_managed_wallet_required';
  end if;

  -- Idempotent retries return the existing order and never repeat risk checks
  -- or create a second order with the same client id.
  select * into v_order
    from sniper.live_orders
   where client_order_id = p_client_order_id;
  if found then
    if v_order.telegram_id <> p_telegram_id
       or v_order.mint <> p_mint
       or v_order.side <> p_side
       or v_order.amount <> p_amount
       or v_order.amount_unit <> p_amount_unit then
      raise exception 'live_order_idempotency_conflict';
    end if;
    return pg_catalog.jsonb_build_object(
      'orderId', v_order.id,
      'clientOrderId', v_order.client_order_id,
      'mint', v_order.mint,
      'side', v_order.side,
      'amount', v_order.amount,
      'amountUnit', v_order.amount_unit,
      'status', v_order.status,
      'signature', v_order.signature,
      'createdAt', v_order.created_at
    );
  end if;

  if p_side = 'buy' then
    if p_amount > v_settings.max_trade_sol then
      raise exception 'live_order_exceeds_max_trade';
    end if;

    select count(*)::integer into v_open_count
      from sniper.positions p
     where p.telegram_id = p_telegram_id
       and p.wallet_id = v_wallet_id
       and p.status in ('buy_pending','open','sell_pending');

    select count(*)::integer into v_pending_buys
      from sniper.live_orders o
     where o.telegram_id = p_telegram_id
       and o.side = 'buy'
       and o.status in ('prepared','submitted','unknown');

    if coalesce(v_open_count,0) + coalesce(v_pending_buys,0) >= v_settings.max_open_positions then
      raise exception 'live_max_open_positions_reached';
    end if;

    if exists (
      select 1 from sniper.positions p
       where p.telegram_id = p_telegram_id
         and p.wallet_id = v_wallet_id
         and p.mint = p_mint
         and p.status in ('buy_pending','open','sell_pending')
    ) then
      raise exception 'live_position_already_open';
    end if;

    select coalesce(sum(p.net_pnl_sol) filter (where p.net_pnl_sol < 0),0)
      into v_daily_loss
      from sniper.positions p
     where p.telegram_id = p_telegram_id
       and p.status = 'closed'
       and p.closed_at >= pg_catalog.date_trunc('day',pg_catalog.now());

    if v_settings.max_daily_loss_sol > 0
       and abs(least(0,coalesce(v_daily_loss,0))) >= v_settings.max_daily_loss_sol then
      raise exception 'live_daily_loss_limit_reached';
    end if;
  else
    -- Lock existing open positions before calculating available token quantity.
    perform 1
      from sniper.positions p
     where p.telegram_id = p_telegram_id
       and p.wallet_id = v_wallet_id
       and p.mint = p_mint
       and p.status = 'open'
     for update;

    select coalesce(sum(p.quantity),0)
      into v_available_tokens
      from sniper.positions p
     where p.telegram_id = p_telegram_id
       and p.wallet_id = v_wallet_id
       and p.mint = p_mint
       and p.status = 'open';

    if coalesce(v_available_tokens,0) <= 0 then
      raise exception 'live_position_not_open';
    end if;
    if p_amount > v_available_tokens then
      raise exception 'live_sell_exceeds_available_quantity';
    end if;
    if exists (
      select 1 from sniper.live_orders o
       where o.telegram_id = p_telegram_id
         and o.mint = p_mint
         and o.side = 'sell'
         and o.status in ('prepared','submitted','unknown')
    ) then
      raise exception 'live_sell_already_pending';
    end if;
  end if;

  insert into sniper.live_orders (
    telegram_id, client_order_id, mint, side, amount, amount_unit
  ) values (
    p_telegram_id, p_client_order_id, p_mint, p_side, p_amount, p_amount_unit
  )
  returning * into v_order;

  return pg_catalog.jsonb_build_object(
    'orderId', v_order.id,
    'clientOrderId', v_order.client_order_id,
    'mint', v_order.mint,
    'side', v_order.side,
    'amount', v_order.amount,
    'amountUnit', v_order.amount_unit,
    'status', v_order.status,
    'signature', v_order.signature,
    'createdAt', v_order.created_at
  );
end;
$function$;

REVOKE ALL ON FUNCTION public.sniper_live_order_prepare(bigint,uuid,text,text,numeric,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_order_prepare(bigint,uuid,text,text,numeric,text) TO service_role;


CREATE OR REPLACE FUNCTION public.sniper_live_position_apply_fill(
  p_order_id uuid,
  p_token_amount numeric,
  p_sol_amount numeric,
  p_fee_sol numeric DEFAULT 0,
  p_chain_slot bigint DEFAULT NULL,
  p_block_time timestamptz DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO ''
AS $function$
declare
  v_order sniper.live_orders%rowtype;
  v_position sniper.positions%rowtype;
  v_fill sniper.live_order_fills%rowtype;
  v_wallet_id uuid;
  v_position_id uuid;
  v_price numeric;
  v_remaining_quantity numeric;
  v_allocated_cost numeric;
  v_remaining_invested numeric;
  v_realized_proceeds numeric;
  v_initial_invested numeric;
  v_total_fees numeric;
  v_expected_exit numeric;
  v_gross_pnl numeric;
  v_net_pnl numeric;
  v_net_pct numeric;
  v_closed boolean;
  v_block_time timestamptz;
begin
  if p_order_id is null
     or p_token_amount is null or p_token_amount <= 0
     or p_sol_amount is null or p_sol_amount <= 0
     or p_fee_sol is null or p_fee_sol < 0 then
    raise exception 'invalid_live_fill';
  end if;

  select * into v_order
    from sniper.live_orders
   where id = p_order_id
   for update;
  if not found or v_order.status <> 'confirmed' or v_order.signature is null then
    raise exception 'live_order_not_confirmed';
  end if;

  select * into v_fill
    from sniper.live_order_fills
   where order_id = p_order_id;
  if found then
    return pg_catalog.jsonb_build_object(
      'duplicate',true,'orderId',v_fill.order_id,
      'positionId',v_fill.position_id,'side',v_fill.side,
      'tokenAmount',v_fill.token_amount,'solAmount',v_fill.sol_amount
    );
  end if;

  select id into v_wallet_id
    from sniper.wallets
   where telegram_id = v_order.telegram_id
     and status = 'active'
     and custody_mode = 'managed'
     and vault_secret_id is not null
   order by created_at desc
   limit 1;
  if v_wallet_id is null then
    raise exception 'active_managed_wallet_required';
  end if;

  v_price := p_sol_amount / p_token_amount;
  v_block_time := coalesce(p_block_time,pg_catalog.now());

  if v_order.side = 'buy' then
    if exists (
      select 1 from sniper.positions p
       where p.telegram_id = v_order.telegram_id
         and p.wallet_id = v_wallet_id
         and p.mint = v_order.mint
         and p.status in ('buy_pending','open','sell_pending')
    ) then
      raise exception 'live_position_already_open';
    end if;

    insert into sniper.positions (
      telegram_id,wallet_id,mint,status,entry_price,quantity,invested_sol,
      initial_invested_sol,realized_proceeds_sol,expected_exit_sol,
      gross_pnl_sol,total_fees_sol,net_pnl_sol,net_pnl_pct,
      opened_at,entry_order_id,last_mark_price,last_mark_at
    ) values (
      v_order.telegram_id,v_wallet_id,v_order.mint,'open',v_price,p_token_amount,p_sol_amount,
      p_sol_amount,0,p_sol_amount,
      0,p_fee_sol,-p_fee_sol,(-p_fee_sol / p_sol_amount) * 100,
      v_block_time,p_order_id,v_price,v_block_time
    )
    returning id into v_position_id;
    v_remaining_quantity := p_token_amount;
    v_remaining_invested := p_sol_amount;
    v_realized_proceeds := 0;
    v_total_fees := p_fee_sol;
    v_gross_pnl := 0;
    v_net_pnl := -p_fee_sol;
    v_net_pct := (-p_fee_sol / p_sol_amount) * 100;
    v_expected_exit := p_sol_amount;
    v_closed := false;
  elsif v_order.side = 'sell' then
    select * into v_position
      from sniper.positions p
     where p.telegram_id = v_order.telegram_id
       and p.wallet_id = v_wallet_id
       and p.mint = v_order.mint
       and p.status = 'open'
     order by p.opened_at desc
     limit 1
     for update;
    if not found then raise exception 'live_position_not_open'; end if;
    if p_token_amount > v_position.quantity then
      raise exception 'live_fill_exceeds_position_quantity';
    end if;

    v_allocated_cost := v_position.invested_sol * (p_token_amount / v_position.quantity);
    v_remaining_quantity := v_position.quantity - p_token_amount;
    v_remaining_invested := greatest(0,v_position.invested_sol - v_allocated_cost);
    v_realized_proceeds := coalesce(v_position.realized_proceeds_sol,0) + p_sol_amount;
    v_initial_invested := coalesce(v_position.initial_invested_sol,v_position.invested_sol);
    v_total_fees := coalesce(v_position.total_fees_sol,0) + p_fee_sol;
    v_closed := v_remaining_quantity = 0;
    v_expected_exit := case
      when v_closed then 0
      else v_remaining_quantity * coalesce(v_position.last_mark_price,v_position.entry_price)
    end;
    v_gross_pnl := v_realized_proceeds + v_expected_exit - v_initial_invested;
    v_net_pnl := v_gross_pnl - v_total_fees;
    v_net_pct := case when v_initial_invested > 0 then (v_net_pnl / v_initial_invested) * 100 else 0 end;
    v_position_id := v_position.id;

    update sniper.positions
       set quantity = v_remaining_quantity,
           invested_sol = v_remaining_invested,
           realized_proceeds_sol = v_realized_proceeds,
           expected_exit_sol = v_expected_exit,
           gross_pnl_sol = v_gross_pnl,
           total_fees_sol = v_total_fees,
           net_pnl_sol = v_net_pnl,
           net_pnl_pct = v_net_pct,
           last_exit_order_id = p_order_id,
           status = case when v_closed then 'closed' else 'open' end,
           closed_at = case when v_closed then v_block_time else null end,
           updated_at = pg_catalog.now()
     where id = v_position_id;
  else
    raise exception 'invalid_live_order_side';
  end if;

  insert into sniper.live_order_fills (
    order_id,position_id,telegram_id,wallet_id,mint,side,
    token_amount,sol_amount,fee_sol,price_sol,chain_slot,block_time
  ) values (
    p_order_id,v_position_id,v_order.telegram_id,v_wallet_id,v_order.mint,v_order.side,
    p_token_amount,p_sol_amount,p_fee_sol,v_price,
    coalesce(p_chain_slot,v_order.chain_slot),v_block_time
  );

  return pg_catalog.jsonb_build_object(
    'orderId',p_order_id,
    'positionId',v_position_id,
    'side',v_order.side,
    'status',case when v_closed then 'closed' else 'open' end,
    'tokenAmount',p_token_amount,
    'solAmount',p_sol_amount,
    'priceSol',v_price,
    'feeSol',p_fee_sol,
    'remainingQuantity',v_remaining_quantity,
    'remainingInvestedSol',v_remaining_invested,
    'expectedExitSol',v_expected_exit,
    'grossPnlSol',v_gross_pnl,
    'netPnlSol',v_net_pnl,
    'netPnlPct',v_net_pct
  );
end;
$function$;

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
begin
  if p_mint is null or p_price_sol is null or p_price_sol <= 0 then
    raise exception 'invalid_live_position_mark';
  end if;
  update sniper.positions p
     set last_mark_price = p_price_sol,
         last_mark_at = coalesce(p_observed_at,pg_catalog.now()),
         expected_exit_sol = p.quantity * p_price_sol,
         gross_pnl_sol = coalesce(p.realized_proceeds_sol,0) + (p.quantity * p_price_sol) - coalesce(p.initial_invested_sol,p.invested_sol),
         net_pnl_sol = coalesce(p.realized_proceeds_sol,0) + (p.quantity * p_price_sol) - coalesce(p.initial_invested_sol,p.invested_sol) - coalesce(p.total_fees_sol,0),
         net_pnl_pct = case
           when coalesce(p.initial_invested_sol,p.invested_sol) > 0
           then ((coalesce(p.realized_proceeds_sol,0) + (p.quantity * p_price_sol) - coalesce(p.initial_invested_sol,p.invested_sol) - coalesce(p.total_fees_sol,0)) / coalesce(p.initial_invested_sol,p.invested_sol)) * 100
           else 0 end,
         updated_at = pg_catalog.now()
   where p.mint = p_mint and p.status = 'open';
  get diagnostics v_count = row_count;
  return v_count;
end;
$function$;

CREATE OR REPLACE FUNCTION public.sniper_open_live_position_mints()
RETURNS TABLE(mint text)
LANGUAGE sql
SECURITY DEFINER
SET search_path TO ''
AS $function$
  select distinct p.mint
    from sniper.positions p
   where p.status = 'open';
$function$;

CREATE OR REPLACE FUNCTION public.sniper_live_open_positions()
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
  last_mark_at timestamptz,
  take_profit_pct numeric,
  stop_loss_pct numeric,
  idle_exit_seconds integer,
  max_slippage_bps integer,
  live_armed boolean,
  enabled boolean,
  mode text
)
LANGUAGE sql
SECURITY DEFINER
SET search_path TO ''
AS $function$
  select p.id,p.telegram_id,p.wallet_id,p.mint,p.quantity,p.invested_sol,
         p.initial_invested_sol,p.realized_proceeds_sol,p.entry_price,
         p.last_mark_price,p.last_mark_at,s.take_profit_pct,s.stop_loss_pct,
         s.idle_exit_seconds,s.max_slippage_bps,s.live_armed,s.enabled,s.mode
    from sniper.positions p
    left join sniper.user_settings s on s.telegram_id = p.telegram_id
   where p.status = 'open';
$function$;

REVOKE ALL ON FUNCTION public.sniper_live_position_apply_fill(uuid,numeric,numeric,numeric,bigint,timestamptz) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_position_apply_fill(uuid,numeric,numeric,numeric,bigint,timestamptz) TO service_role;
REVOKE ALL ON FUNCTION public.sniper_live_position_mark(text,numeric,timestamptz) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_position_mark(text,numeric,timestamptz) TO service_role;
REVOKE ALL ON FUNCTION public.sniper_open_live_position_mints() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_open_live_position_mints() TO service_role;
REVOKE ALL ON FUNCTION public.sniper_live_open_positions() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_open_positions() TO service_role;
