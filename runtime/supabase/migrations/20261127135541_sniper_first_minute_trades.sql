-- ZombieCash Sniper: capture PumpPortal bonding-curve trades immediately after mint creation.
-- Apply through Supabase migrations / SQL editor. This migration does not enable live trading.

create table if not exists sniper.token_trades (
  id bigint generated always as identity primary key,
  mint text not null,
  signature text not null unique,
  trader text,
  side text not null check (side in ('buy','sell')),
  sol_amount numeric,
  token_amount numeric,
  price_sol numeric,
  market_cap_sol numeric,
  v_sol_in_bonding_curve numeric,
  v_tokens_in_bonding_curve numeric,
  event_at timestamptz not null,
  received_at timestamptz not null default now(),
  constraint token_trades_mint_format check (length(mint) between 32 and 44 and mint ~ '^[1-9A-HJ-NP-Za-km-z]+$')
);

create index if not exists token_trades_mint_event_idx
  on sniper.token_trades (mint, event_at desc);
create index if not exists token_trades_received_idx
  on sniper.token_trades (received_at desc);

alter table sniper.token_trades enable row level security;
revoke all on table sniper.token_trades from anon, authenticated;
grant select, insert, update, delete on table sniper.token_trades to service_role;
grant usage, select on sequence sniper.token_trades_id_seq to service_role;

create or replace function public.sniper_trade_upsert(p_event jsonb)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_mint text;
  v_signature text;
  v_side text;
  v_sol numeric;
  v_tokens numeric;
  v_price numeric;
  v_market_cap numeric;
  v_v_sol numeric;
  v_v_tokens numeric;
  v_event_at timestamptz;
  v_trader text;
  v_result jsonb;
begin
  if p_event is null or pg_catalog.jsonb_typeof(p_event) <> 'object' then
    raise exception 'invalid_sniper_trade_event';
  end if;

  v_mint := coalesce(p_event->>'mint','');
  if pg_catalog.length(v_mint) < 32 or pg_catalog.length(v_mint) > 44
     or v_mint !~ '^[1-9A-HJ-NP-Za-km-z]+$' then
    raise exception 'invalid_sniper_trade_mint';
  end if;

  v_signature := nullif(pg_catalog.left(p_event->>'signature',128),'');
  if v_signature is null then raise exception 'sniper_trade_signature_required'; end if;

  v_side := lower(coalesce(p_event->>'side',''));
  if v_side not in ('buy','sell') then raise exception 'invalid_sniper_trade_side'; end if;

  v_sol := nullif(p_event->>'solAmount','')::numeric;
  v_tokens := nullif(p_event->>'tokenAmount','')::numeric;
  v_price := nullif(p_event->>'priceSol','')::numeric;
  v_market_cap := nullif(p_event->>'marketCapSol','')::numeric;
  v_v_sol := nullif(p_event->>'vSolInBondingCurve','')::numeric;
  v_v_tokens := nullif(p_event->>'vTokensInBondingCurve','')::numeric;
  if v_sol is not null and v_sol < 0 then raise exception 'invalid_sniper_trade_sol_amount'; end if;
  if v_tokens is not null and v_tokens < 0 then raise exception 'invalid_sniper_trade_token_amount'; end if;
  if v_price is null and v_sol > 0 and v_tokens > 0 then v_price := v_sol / v_tokens; end if;
  if v_price is null and v_v_sol > 0 and v_v_tokens > 0 then v_price := v_v_sol / v_v_tokens; end if;
  if v_price is not null and v_price <= 0 then raise exception 'invalid_sniper_trade_price'; end if;

  v_trader := nullif(pg_catalog.left(p_event->>'trader',64),'');
  v_event_at := coalesce(
    pg_catalog.to_timestamp(((nullif(p_event->>'eventAt','')::numeric / 1000))::double precision),
    pg_catalog.now()
  );

  insert into sniper.token_trades (
    mint, signature, trader, side, sol_amount, token_amount, price_sol,
    market_cap_sol, v_sol_in_bonding_curve, v_tokens_in_bonding_curve,
    event_at, received_at
  ) values (
    v_mint, v_signature, v_trader, v_side, v_sol, v_tokens, v_price,
    v_market_cap, v_v_sol, v_v_tokens, v_event_at, pg_catalog.now()
  )
  on conflict (signature) do nothing
  returning pg_catalog.jsonb_build_object(
    'mint', mint, 'signature', signature, 'side', side,
    'solAmount', sol_amount, 'tokenAmount', token_amount,
    'priceSol', price_sol, 'eventAt', (extract(epoch from event_at) * 1000)::bigint
  ) into v_result;

  delete from sniper.token_trades
   where received_at < pg_catalog.now() - interval '48 hours';
  delete from sniper.token_trades
   where id in (
     select id from sniper.token_trades
     order by received_at desc offset 100000
   );

  return coalesce(v_result, pg_catalog.jsonb_build_object('duplicate', true, 'signature', v_signature));
end;
$function$;

revoke all on function public.sniper_trade_upsert(jsonb) from public, anon, authenticated;
grant execute on function public.sniper_trade_upsert(jsonb) to service_role;

create or replace function public.sniper_trade_window(p_mint text, p_seconds integer default 60)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $function$
  select coalesce(
    pg_catalog.jsonb_agg(q.item order by q.event_at asc),
    '[]'::jsonb
  )
  from (
    select
      pg_catalog.jsonb_build_object(
        'signature', t.signature,
        'side', t.side,
        'trader', t.trader,
        'solAmount', t.sol_amount,
        'tokenAmount', t.token_amount,
        'priceSol', t.price_sol,
        'marketCapSol', t.market_cap_sol,
        'vSolInBondingCurve', t.v_sol_in_bonding_curve,
        'vTokensInBondingCurve', t.v_tokens_in_bonding_curve,
        'eventAt', (extract(epoch from t.event_at) * 1000)::bigint,
        'receivedAt', (extract(epoch from t.received_at) * 1000)::bigint
      ) as item,
      t.event_at
    from sniper.token_trades t
    where t.mint = p_mint
      and t.event_at >= pg_catalog.now() - pg_catalog.make_interval(secs => greatest(1, least(coalesce(p_seconds,60),60)))
    order by t.event_at asc
    limit 2000
  ) q;
$function$;

revoke all on function public.sniper_trade_window(text, integer) from public, anon, authenticated;
grant execute on function public.sniper_trade_window(text, integer) to service_role;


create or replace function public.sniper_launch_active_mints(p_limit integer default 1000)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $function$
  select coalesce(
    pg_catalog.jsonb_agg(q.item order by q.event_at desc),
    '[]'::jsonb
  )
  from (
    select
      pg_catalog.jsonb_build_object(
        'mint', l.mint,
        'eventAt', (extract(epoch from l.event_at) * 1000)::bigint
      ) as item,
      l.event_at
    from sniper.launches l
    where l.event_at >= pg_catalog.now() - interval '60 seconds'
      and l.event_at <= pg_catalog.now()
    order by l.event_at desc
    limit greatest(1, least(coalesce(p_limit,1000),1000))
  ) q;
$function$;

revoke all on function public.sniper_launch_active_mints(integer) from public, anon, authenticated;
grant execute on function public.sniper_launch_active_mints(integer) to service_role;


-- Include live first-minute bonding-curve trade aggregates in the launch feed.
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


create or replace function public.sniper_trade_latest(p_mint text)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $function$
  select coalesce(
    (
      select pg_catalog.jsonb_build_object(
        'mint', latest.mint,
        'priceSol', latest.price_sol,
        'side', latest.side,
        'eventAt', (extract(epoch from latest.event_at) * 1000)::bigint,
        'vSolInBondingCurve', latest.v_sol_in_bonding_curve,
        'vTokensInBondingCurve', latest.v_tokens_in_bonding_curve,
        'marketCapSol', latest.market_cap_sol,
        'window', stats.window_data
      )
      from (
        select t.*
        from sniper.token_trades t
        where t.mint = p_mint
        order by t.event_at desc
        limit 1
      ) latest
      cross join lateral (
        select pg_catalog.jsonb_build_object(
          'tradeCount', count(*)::integer,
          'buyCount', count(*) filter (where x.side='buy')::integer,
          'sellCount', count(*) filter (where x.side='sell')::integer,
          'buySol', coalesce(sum(x.sol_amount) filter (where x.side='buy'),0),
          'sellSol', coalesce(sum(x.sol_amount) filter (where x.side='sell'),0),
          'netSol', coalesce(sum(x.sol_amount) filter (where x.side='buy'),0)
                    - coalesce(sum(x.sol_amount) filter (where x.side='sell'),0)
        ) as window_data
        from sniper.token_trades x
        where x.mint = p_mint
          and x.event_at >= pg_catalog.now() - interval '60 seconds'
      ) stats
    ),
    '{}'::jsonb
  );
$function$;

revoke all on function public.sniper_trade_latest(text) from public, anon, authenticated;
grant execute on function public.sniper_trade_latest(text) to service_role;


create or replace function public.sniper_trade_mint_monitored(p_mint text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $function$
  select exists (
    select 1 from sniper.positions p
    where p.mint = p_mint and p.status in ('watching','buy_pending','open','sell_pending')
  ) or exists (
    select 1 from sniper.paper_positions pp
    where pp.mint = p_mint and pp.status = 'open'
  );
$function$;

revoke all on function public.sniper_trade_mint_monitored(text) from public, anon, authenticated;
grant execute on function public.sniper_trade_mint_monitored(text) to service_role;
