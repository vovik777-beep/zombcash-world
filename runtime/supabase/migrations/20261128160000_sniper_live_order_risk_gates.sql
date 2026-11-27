-- Enforce user risk limits and sell-quantity ownership inside the database transaction.
-- This function still does not broadcast transactions; it only prepares an idempotent order.
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
     or p_side not in ('buy','sell')
     or p_amount is null or p_amount <= 0 or p_amount >= 1000000000
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

CREATE INDEX IF NOT EXISTS positions_live_open_wallet_mint_idx
  ON sniper.positions (telegram_id,wallet_id,mint)
  WHERE status IN ('buy_pending','open','sell_pending');

CREATE INDEX IF NOT EXISTS live_orders_pending_user_side_mint_idx
  ON sniper.live_orders (telegram_id,side,mint)
  WHERE status IN ('prepared','submitted','unknown');
