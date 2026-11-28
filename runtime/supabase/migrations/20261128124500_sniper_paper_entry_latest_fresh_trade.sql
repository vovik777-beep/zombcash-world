-- Use the latest freshly received trade as the Paper execution price
-- once the configured minimum-buy threshold has been reached.
-- Existing position management, exits, thresholds, and profile settings are unchanged.

DO $migration$
DECLARE
  v_definition text;
  v_old text;
  v_new text;
BEGIN
  SELECT pg_catalog.pg_get_functiondef(
    'public.sniper_paper_engine_process(text)'::pg_catalog.regprocedure
  ) INTO v_definition;

  v_old := $old$
    if not v_user.enabled or v_price is null or v_price<=0 then continue; end if;
$old$;
  v_new := $new$
    if not v_user.enabled then continue; end if;
$new$;
  IF pg_catalog.strpos(v_definition, v_old) = 0 THEN
    RAISE EXCEPTION 'Paper entry migration: enabled/price guard not found';
  END IF;
  v_definition := pg_catalog.replace(v_definition, v_old, v_new);

  v_old := $old$
    select t.price_sol,t.event_at,t.trader into v_entry
      from sniper.token_trades t
     where t.mint=p_mint and t.side='buy' and t.price_sol>0
       and t.event_at>=v_launch.event_at
       and t.event_at<=v_launch.event_at+pg_catalog.make_interval(secs=>v_user.buy_window_seconds)
     order by t.event_at asc limit 1;
    if v_entry.price_sol is null or v_now>v_launch.event_at+pg_catalog.make_interval(secs=>v_user.buy_window_seconds)
       then continue; end if;
$old$;
  v_new := $new$
    -- The buy-count threshold is the signal; execute the Paper entry at the latest
    -- real trade price freshly received when this threshold is evaluated.
    select t.price_sol,t.event_at,t.trader,t.side,t.received_at into v_entry
      from sniper.token_trades t
     where t.mint=p_mint
       and t.side in ('buy','sell')
       and t.price_sol>0
       and t.event_at>=v_launch.event_at
       and t.event_at<=v_launch.event_at+pg_catalog.make_interval(secs=>v_user.buy_window_seconds)
       and t.event_at<=v_now+interval '5 seconds'
       and t.received_at>=v_now-interval '30 seconds'
       and t.received_at<=v_now+interval '5 seconds'
     order by t.event_at desc,t.received_at desc
     limit 1;
    if v_entry.price_sol is null
       or v_now>v_launch.event_at+pg_catalog.make_interval(secs=>v_user.buy_window_seconds)
       then continue; end if;
$old$;
  IF pg_catalog.strpos(v_definition, v_old) = 0 THEN
    RAISE EXCEPTION 'Paper entry migration: first-buy selector not found';
  END IF;
  v_definition := pg_catalog.replace(v_definition, v_old, v_new);

  v_old := $old$
      v_buy,v_entry.price_sol,v_price,v_quantity,0,0,'open',null,v_now,v_now);
$old$;
  v_new := $new$
      v_buy,v_entry.price_sol,v_entry.price_sol,v_quantity,0,0,'open',null,v_now,v_now);
$new$;
  IF pg_catalog.strpos(v_definition, v_old) = 0 THEN
    RAISE EXCEPTION 'Paper entry migration: position insert values not found';
  END IF;
  v_definition := pg_catalog.replace(v_definition, v_old, v_new);

  v_old := $old$
      pg_catalog.jsonb_build_object('mint',p_mint,'buySol',v_buy,'entryPriceSol',v_entry.price_sol,
        'entryTradeAt',v_entry.event_at,'entryTrader',v_entry.trader,
        'tradeCount',v_stats.trade_count,'buyCount',v_stats.buy_count,'uniqueBuyers',v_stats.unique_buyers,
$old$;
  v_new := $new$
      pg_catalog.jsonb_build_object('mint',p_mint,'buySol',v_buy,'entryPriceSol',v_entry.price_sol,
        'entryTradeAt',v_entry.event_at,'entryTrader',v_entry.trader,
        'entryTradeSide',v_entry.side,'entryReceivedAt',v_entry.received_at,
        'entryPricingPolicy','latest_fresh_trade_when_buy_threshold_met',
        'tradeCount',v_stats.trade_count,'buyCount',v_stats.buy_count,'uniqueBuyers',v_stats.unique_buyers,
$new$;
  IF pg_catalog.strpos(v_definition, v_old) = 0 THEN
    RAISE EXCEPTION 'Paper entry migration: Paper buy event payload not found';
  END IF;
  v_definition := pg_catalog.replace(v_definition, v_old, v_new);

  EXECUTE v_definition;
END;
$migration$;

DO $verify$
DECLARE
  v_definition text;
BEGIN
  SELECT pg_catalog.pg_get_functiondef(
    'public.sniper_paper_engine_process(text)'::pg_catalog.regprocedure
  ) INTO v_definition;

  IF pg_catalog.strpos(v_definition, 'latest_fresh_trade_when_buy_threshold_met') = 0
     OR pg_catalog.strpos(v_definition, 't.received_at>=v_now-interval ''30 seconds''') = 0
     OR pg_catalog.strpos(v_definition, 'order by t.event_at desc,t.received_at desc') = 0 THEN
    RAISE EXCEPTION 'Paper entry migration verification failed';
  END IF;
END;
$verify$;
