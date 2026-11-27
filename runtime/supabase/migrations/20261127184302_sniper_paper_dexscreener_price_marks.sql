-- Recovery source for the live DexScreener Paper valuation migration.
CREATE TABLE IF NOT EXISTS sniper.paper_price_marks (
    mint text PRIMARY KEY,
    source text NOT NULL CHECK (source = 'dexscreener'),
    price_sol numeric NOT NULL CHECK (price_sol > 0),
    liquidity_usd numeric NOT NULL CHECK (liquidity_usd >= 500),
    pair_address text NOT NULL,
    observed_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    CONSTRAINT paper_price_marks_mint_format CHECK (mint ~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$')
);
REVOKE ALL ON TABLE sniper.paper_price_marks FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.sniper_paper_price_mark_upsert(p_mark jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
DECLARE
    v_mint text;
    v_source text;
    v_price numeric;
    v_liquidity numeric;
    v_pair text;
    v_observed_ms numeric;
    v_observed_at timestamptz;
BEGIN
    IF p_mark IS NULL OR pg_catalog.jsonb_typeof(p_mark) <> 'object' THEN
        RETURN pg_catalog.jsonb_build_object('ok', false, 'reason', 'invalid_payload');
    END IF;
    v_mint := coalesce(p_mark->>'mint', '');
    IF v_mint !~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$' THEN
        RETURN pg_catalog.jsonb_build_object('ok', false, 'reason', 'invalid_mint');
    END IF;
    v_source := lower(coalesce(p_mark->>'source', ''));
    IF v_source <> 'dexscreener' THEN
        RETURN pg_catalog.jsonb_build_object('ok', false, 'reason', 'invalid_source');
    END IF;
    BEGIN
        v_price := (p_mark->>'priceSol')::numeric;
        v_liquidity := (p_mark->>'liquidityUsd')::numeric;
        v_observed_ms := (p_mark->>'observedAt')::numeric;
    EXCEPTION WHEN OTHERS THEN
        RETURN pg_catalog.jsonb_build_object('ok', false, 'reason', 'invalid_numeric_value');
    END;
    IF v_price IS NULL OR v_price <= 0 OR v_price >= 'Infinity'::numeric
       OR v_liquidity IS NULL OR v_liquidity < 500 OR v_liquidity >= 'Infinity'::numeric
       OR v_observed_ms IS NULL OR v_observed_ms <= 0 OR v_observed_ms >= 'Infinity'::numeric THEN
        RETURN pg_catalog.jsonb_build_object('ok', false, 'reason', 'invalid_price_or_liquidity');
    END IF;
    IF v_observed_ms < (extract(epoch FROM (pg_catalog.now() - interval '2 minutes')) * 1000)
       OR v_observed_ms > (extract(epoch FROM (pg_catalog.now() + interval '5 seconds')) * 1000) THEN
        RETURN pg_catalog.jsonb_build_object('ok', false, 'reason', 'stale_or_future_mark');
    END IF;
    v_pair := nullif(pg_catalog.left(p_mark->>'pairAddress', 128), '');
    IF v_pair IS NULL THEN
        RETURN pg_catalog.jsonb_build_object('ok', false, 'reason', 'missing_pair_address');
    END IF;
    v_observed_at := pg_catalog.to_timestamp((v_observed_ms / 1000)::double precision);
    INSERT INTO sniper.paper_price_marks(mint, source, price_sol, liquidity_usd, pair_address, observed_at, updated_at)
    VALUES (v_mint, v_source, v_price, v_liquidity, v_pair, v_observed_at, pg_catalog.now())
    ON CONFLICT (mint) DO UPDATE SET
        source = EXCLUDED.source,
        price_sol = EXCLUDED.price_sol,
        liquidity_usd = EXCLUDED.liquidity_usd,
        pair_address = EXCLUDED.pair_address,
        observed_at = EXCLUDED.observed_at,
        updated_at = pg_catalog.now()
    WHERE EXCLUDED.observed_at >= sniper.paper_price_marks.observed_at;
    RETURN pg_catalog.jsonb_build_object('ok', true, 'mint', v_mint, 'priceSol', v_price,
        'liquidityUsd', v_liquidity, 'observedAt', v_observed_ms);
END;
$function$
;
REVOKE ALL ON FUNCTION public.sniper_paper_price_mark_upsert(jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_paper_price_mark_upsert(jsonb) TO service_role;

CREATE OR REPLACE FUNCTION public.sniper_paper_engine_process(p_mint text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  v_launch record; v_stats record; v_entry record; v_price numeric; v_mark_price numeric;
  v_now timestamptz := pg_catalog.now(); v_user record; v_position record; v_price_at timestamptz; v_mark_price_at timestamptz;
  v_fee_fraction numeric; v_net numeric; v_pct numeric; v_daily_loss numeric;
  v_open_count integer; v_available numeric; v_buy numeric; v_quantity numeric;
  v_reason text; v_entered integer := 0; v_updated integer := 0; v_closed integer := 0;
begin
  if p_mint is null or pg_catalog.length(p_mint) < 32 or pg_catalog.length(p_mint) > 44
     or p_mint !~ '^[1-9A-HJ-NP-Za-km-z]+$' then raise exception 'invalid_sniper_paper_mint'; end if;

  select l.event_at,l.token_name,l.token_symbol,l.creator,l.source into v_launch
    from sniper.launches l where l.mint=p_mint;
  if not found then return pg_catalog.jsonb_build_object('ok',false,'reason','launch_not_found'); end if;

  select count(*)::integer as trade_count,
    count(*) filter(where t.side='buy')::integer as buy_count,
    count(*) filter(where t.side='sell')::integer as sell_count,
    count(distinct t.trader) filter(where t.side='buy')::integer as unique_buyers,
    coalesce(sum(t.sol_amount) filter(where t.side='buy'),0) as buy_sol,
    coalesce(sum(t.sol_amount) filter(where t.side='sell'),0) as sell_sol,
    (array_agg(t.price_sol order by t.event_at asc) filter(where t.price_sol is not null))[1] as first_price,
    (array_agg(t.price_sol order by t.event_at desc) filter(where t.price_sol is not null))[1] as last_price
  into v_stats from sniper.token_trades t
   where t.mint=p_mint and t.event_at>=v_launch.event_at
     and t.event_at<v_launch.event_at+interval '60 seconds';

  select t.price_sol,t.event_at into v_price,v_price_at from sniper.token_trades t
   where t.mint=p_mint and t.price_sol>0 order by t.event_at desc limit 1;

  -- Fresh market marks value existing Paper positions only; never use them as entry signals.
  select m.price_sol,m.observed_at into v_mark_price,v_mark_price_at
    from sniper.paper_price_marks m
   where m.mint=p_mint and m.source='dexscreener'
     and m.liquidity_usd>=500 and m.observed_at>=v_now-interval '90 seconds'
   order by m.observed_at desc limit 1;
  if v_price is not null and v_price_at>=v_now-interval '30 seconds' then
    v_mark_price:=v_price;
    v_mark_price_at:=v_price_at;
  end if;

  for v_user in
    select s.* from sniper.user_settings s
     where s.mode='paper' and (s.enabled=true or exists(
       select 1 from sniper.paper_positions p where p.telegram_id=s.telegram_id and p.status='open'))
     order by s.telegram_id
  loop
    select p.* into v_position from sniper.paper_positions p
     where p.telegram_id=v_user.telegram_id and p.mint=p_mint and p.status='open'
     order by p.created_at desc limit 1 for update;

    if found then
      if v_mark_price is null or v_mark_price<=0 or v_mark_price_at is null
         or v_mark_price_at < v_now-interval '90 seconds' then continue; end if;
      v_fee_fraction:=greatest(0,least(v_user.paper_fee_bps,5000))::numeric/20000;
      v_net:=(v_position.token_quantity*v_mark_price*(1-v_fee_fraction))-v_user.paper_network_fee_sol-v_position.buy_amount_sol;
      v_pct:=case when v_position.buy_amount_sol>0 then v_net/v_position.buy_amount_sol*100 else 0 end;
      v_reason:=case when v_pct>=v_user.take_profit_pct then 'target'
                     when v_pct<=-v_user.stop_loss_pct then 'stop'
                     when v_position.last_movement_at is not null
                       and v_now>=v_position.last_movement_at+pg_catalog.make_interval(secs=>v_user.idle_exit_seconds)
                       then 'stagnant'
                     else null end;
      update sniper.paper_positions set last_price_sol=v_mark_price,net_pnl_sol=v_net,net_pnl_pct=v_pct,
        status=case when v_reason is null then 'open' else 'closed' end,exit_reason=v_reason,
        closed_at=case when v_reason is null then null else v_now end,
        last_movement_at=case when v_mark_price is not null and v_position.last_price_sol>0
          and abs((v_mark_price/v_position.last_price_sol-1)*100)>=v_user.min_price_move_pct
          then v_now else v_position.last_movement_at end,
        updated_at=v_now where id=v_position.id;
      v_updated:=v_updated+1;
      if v_reason is not null then
        v_closed:=v_closed+1;
        insert into sniper.events(telegram_id,event_type,severity,event_code,details)
        values(v_user.telegram_id,'paper_sell','info',v_reason,
          pg_catalog.jsonb_build_object('mint',p_mint,'netPnlSol',v_net,'netPnlPct',v_pct,'priceSol',v_mark_price,
            'stopLossTargetPct',v_user.stop_loss_pct,
            'stopLossOvershootPct',case when v_reason='stop' then greatest(0,abs(v_pct)-v_user.stop_loss_pct) else 0 end,
            'source',v_launch.source,
            'watchdogCheck',not (v_price_at is not null and v_price_at>=v_now-interval '30 seconds'),
            'priceSource',case when v_price_at is not null and v_price_at>=v_now-interval '30 seconds'
                               then 'trade' else 'dexscreener' end));
      end if;
      continue;
    end if;

    if not v_user.enabled or v_price is null or v_price<=0 then continue; end if;
    if exists(select 1 from sniper.paper_positions p
      where p.telegram_id=v_user.telegram_id and p.mint=p_mint) then continue; end if;

    if v_user.creator_policy='first' and v_launch.creator is not null
       and (select count(*) from sniper.launches history where history.creator=v_launch.creator)>1 then continue; end if;
    if v_user.creator_policy='repeat' and (v_launch.creator is null
       or (select count(*) from sniper.launches history where history.creator=v_launch.creator)<=1) then continue; end if;

    -- Early-entry Paper strategy: use the first valid BUY trade price in the user's launch window.
    select t.price_sol,t.event_at,t.trader into v_entry
      from sniper.token_trades t
     where t.mint=p_mint and t.side='buy' and t.price_sol>0
       and t.event_at>=v_launch.event_at
       and t.event_at<=v_launch.event_at+pg_catalog.make_interval(secs=>v_user.buy_window_seconds)
     order by t.event_at asc limit 1;
    if v_entry.price_sol is null or v_now>v_launch.event_at+pg_catalog.make_interval(secs=>v_user.buy_window_seconds)
       then continue; end if;

    select count(*)::integer into v_open_count from sniper.paper_positions p
     where p.telegram_id=v_user.telegram_id and p.status='open';
    if v_open_count>=v_user.max_open_positions then continue; end if;

    select coalesce(sum(p.net_pnl_sol) filter(where p.net_pnl_sol<0),0) into v_daily_loss
      from sniper.paper_positions p where p.telegram_id=v_user.telegram_id
       and p.status='closed' and p.closed_at>=date_trunc('day',v_now);
    if v_user.max_daily_loss_sol>0 and abs(least(0,coalesce(v_daily_loss,0)))>=v_user.max_daily_loss_sol then continue; end if;

    select v_user.paper_capital_sol+coalesce(sum(p.net_pnl_sol) filter(where p.status='closed'),0)
      -coalesce(sum(p.buy_amount_sol) filter(where p.status='open'),0) into v_available
      from sniper.paper_positions p where p.telegram_id=v_user.telegram_id;
    v_buy:=least(v_user.max_trade_sol,coalesce(v_available,0));
    if v_buy<=0 then continue; end if;

    v_fee_fraction:=greatest(0,least(v_user.paper_fee_bps,5000))::numeric/20000;
    v_quantity:=(v_buy-v_user.paper_network_fee_sol-(v_buy*v_fee_fraction))/v_entry.price_sol;
    if v_quantity<=0 then continue; end if;

    insert into sniper.paper_positions(id,telegram_id,mint,token_symbol,token_name,buy_amount_sol,
      entry_price_sol,last_price_sol,token_quantity,net_pnl_sol,net_pnl_pct,status,exit_reason,opened_at,updated_at)
    values(pg_catalog.gen_random_uuid(),v_user.telegram_id,p_mint,
      coalesce(nullif(v_launch.token_symbol,''),'TOKEN'),coalesce(v_launch.token_name,''),
      v_buy,v_entry.price_sol,v_price,v_quantity,0,0,'open',null,v_now,v_now);

    insert into sniper.events(telegram_id,event_type,severity,event_code,details)
    values(v_user.telegram_id,'paper_buy','info','first_valid_buy',
      pg_catalog.jsonb_build_object('mint',p_mint,'buySol',v_buy,'entryPriceSol',v_entry.price_sol,
        'entryTradeAt',v_entry.event_at,'entryTrader',v_entry.trader,
        'tradeCount',v_stats.trade_count,'buyCount',v_stats.buy_count,'uniqueBuyers',v_stats.unique_buyers,
        'sellCount',v_stats.sell_count,'netSol',v_stats.buy_sol-v_stats.sell_sol,
        'priceChangePct',case when v_stats.first_price>0 then (v_stats.last_price/v_stats.first_price-1)*100 else null end,
        'strategy','first_valid_buy_in_launch_window',
        'maxOpenPositions',v_user.max_open_positions,'maxDailyLossSol',v_user.max_daily_loss_sol,
        'source',v_launch.source));
    v_entered:=v_entered+1;
  end loop;
  return pg_catalog.jsonb_build_object('ok',true,'entered',v_entered,'updated',v_updated,'closed',v_closed);
end;
$function$
;
REVOKE ALL ON FUNCTION public.sniper_paper_engine_process(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_paper_engine_process(text) TO service_role;
