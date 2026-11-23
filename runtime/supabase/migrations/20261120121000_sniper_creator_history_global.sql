-- Report creator history against the complete launch table, not only the
-- latest page returned to the UI. This makes FIRST SEEN / REPEAT DEV reliable.
create or replace function public.sniper_launch_list(p_limit integer default 50)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $function$
  select coalesce(
    pg_catalog.jsonb_agg(q.item order by q.received_at desc),
    '[]'::jsonb
  )
  from (
    select
      pg_catalog.jsonb_build_object(
        'mint', l.mint,
        'signature', l.signature,
        'name', l.token_name,
        'symbol', l.token_symbol,
        'creator', l.creator,
        'creatorLaunchCount', case when l.creator is null then null else (
          select count(*)::integer from sniper.launches history where history.creator=l.creator
        ) end,
        'creatorHistoryStatus', case
          when l.creator is null then 'unknown'
          when (select count(*) from sniper.launches history where history.creator=l.creator)>1 then 'repeat'
          else 'first_seen'
        end,
        'uri', l.metadata_uri,
        'source', l.source,
        'status', l.status,
        'initialBuy', l.initial_buy,
        'marketCapSol', l.market_cap_sol,
        'vSolInBondingCurve', l.v_sol_in_bonding_curve,
        'vTokensInBondingCurve', l.v_tokens_in_bonding_curve,
        'pool', l.pool,
        'eventAt', (extract(epoch from l.event_at) * 1000)::bigint,
        'receivedAt', (extract(epoch from l.received_at) * 1000)::bigint,
        'firstMinute', pg_catalog.jsonb_build_object(
          'tradeCount', coalesce(t.trade_count,0),
          'buyCount', coalesce(t.buy_count,0),
          'sellCount', coalesce(t.sell_count,0),
          'uniqueBuyers', coalesce(t.unique_buyers,0),
          'buySol', coalesce(t.buy_sol,0),
          'sellSol', coalesce(t.sell_sol,0),
          'netSol', coalesce(t.buy_sol,0) - coalesce(t.sell_sol,0),
          'firstPriceSol', t.first_price_sol,
          'lastPriceSol', t.last_price_sol,
          'priceChangePct', case
            when t.first_price_sol > 0 and t.last_price_sol is not null
              then ((t.last_price_sol / t.first_price_sol) - 1) * 100
            else null
          end,
          'windowComplete', l.event_at <= pg_catalog.now() - interval '60 seconds'
        )
      ) as item,
      l.received_at
    from sniper.launches l
    left join lateral (
      select
        count(*)::integer as trade_count,
        count(*) filter (where x.side = 'buy')::integer as buy_count,
        count(*) filter (where x.side = 'sell')::integer as sell_count,
        count(distinct x.trader) filter (where x.side = 'buy')::integer as unique_buyers,
        coalesce(sum(x.sol_amount) filter (where x.side = 'buy'),0) as buy_sol,
        coalesce(sum(x.sol_amount) filter (where x.side = 'sell'),0) as sell_sol,
        (array_agg(x.price_sol order by x.event_at asc) filter (where x.price_sol is not null))[1] as first_price_sol,
        (array_agg(x.price_sol order by x.event_at desc) filter (where x.price_sol is not null))[1] as last_price_sol
      from sniper.token_trades x
      where x.mint = l.mint
        and x.event_at >= l.event_at
        and x.event_at < l.event_at + interval '60 seconds'
    ) t on true
    order by l.received_at desc
    limit greatest(1, least(coalesce(p_limit,50),100))
  ) q;
$function$;

revoke all on function public.sniper_launch_list(integer) from public, anon, authenticated;
grant execute on function public.sniper_launch_list(integer) to service_role;
