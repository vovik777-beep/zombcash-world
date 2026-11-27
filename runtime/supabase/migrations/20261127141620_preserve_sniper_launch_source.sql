-- Reconstructed from the live Supabase function definition during the 2026-11-27 recovery audit.
-- Preserve the original launch source when PumpDev and PumpPortal both report the same mint.
CREATE OR REPLACE FUNCTION public.sniper_launch_upsert(p_event jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  v_mint text;
  v_signature text;
  v_name text;
  v_symbol text;
  v_creator text;
  v_uri text;
  v_status text;
  v_initial_buy numeric;
  v_market_cap numeric;
  v_v_sol numeric;
  v_v_tokens numeric;
  v_pool text;
  v_event_at timestamptz;
  v_result jsonb;
begin
  if p_event is null or pg_catalog.jsonb_typeof(p_event) <> 'object' then
    raise exception 'invalid_sniper_launch_event';
  end if;
  v_mint := coalesce(p_event->>'mint','');
  if pg_catalog.length(v_mint) < 32 or pg_catalog.length(v_mint) > 44 or v_mint !~ '^[1-9A-HJ-NP-Za-km-z]+$' then
    raise exception 'invalid_sniper_launch_mint';
  end if;
  v_signature := nullif(pg_catalog.left(p_event->>'signature',128),'');
  v_name := coalesce(pg_catalog.left(p_event->>'name',96),'');
  v_symbol := coalesce(nullif(pg_catalog.left(p_event->>'symbol',24),''),'TOKEN');
  v_creator := nullif(pg_catalog.left(p_event->>'creator',64),'');
  v_uri := nullif(pg_catalog.left(p_event->>'uri',512),'');
  v_pool := nullif(pg_catalog.left(p_event->>'pool',64),'');
  v_status := case
    when lower(coalesce(p_event->>'eventType',p_event->>'txType','create')) in ('complete','migration','migrate') then 'migrated'
    else 'detected'
  end;
  v_initial_buy := nullif(p_event->>'initialBuy','')::numeric;
  v_market_cap := nullif(p_event->>'marketCapSol','')::numeric;
  v_v_sol := nullif(p_event->>'vSolInBondingCurve','')::numeric;
  v_v_tokens := nullif(p_event->>'vTokensInBondingCurve','')::numeric;
  v_event_at := coalesce(
    pg_catalog.to_timestamp(((nullif(p_event->>'eventAt','')::numeric / 1000))::double precision),
    pg_catalog.now()
  );

  insert into sniper.launches (
    mint, signature, token_name, token_symbol, creator, metadata_uri,
    source, status, initial_buy, market_cap_sol, v_sol_in_bonding_curve,
    v_tokens_in_bonding_curve, pool, event_at, received_at, updated_at
  ) values (
    v_mint, v_signature, v_name, v_symbol, v_creator, v_uri,
    coalesce(nullif(p_event->>'source',''),'pumpportal'), v_status, v_initial_buy, v_market_cap, v_v_sol,
    v_v_tokens, v_pool, v_event_at, pg_catalog.now(), pg_catalog.now()
  )
  on conflict (mint) do update set
    signature = coalesce(excluded.signature, sniper.launches.signature),
    token_name = coalesce(nullif(excluded.token_name,''), sniper.launches.token_name),
    token_symbol = coalesce(nullif(excluded.token_symbol,'TOKEN'), sniper.launches.token_symbol),
    creator = coalesce(excluded.creator, sniper.launches.creator),
    metadata_uri = coalesce(excluded.metadata_uri, sniper.launches.metadata_uri),
    status = case when excluded.status = 'migrated' then 'migrated' else sniper.launches.status end,
    initial_buy = coalesce(excluded.initial_buy, sniper.launches.initial_buy),
    market_cap_sol = coalesce(excluded.market_cap_sol, sniper.launches.market_cap_sol),
    v_sol_in_bonding_curve = coalesce(excluded.v_sol_in_bonding_curve, sniper.launches.v_sol_in_bonding_curve),
    v_tokens_in_bonding_curve = coalesce(excluded.v_tokens_in_bonding_curve, sniper.launches.v_tokens_in_bonding_curve),
    pool = coalesce(excluded.pool, sniper.launches.pool),
    event_at = least(sniper.launches.event_at, excluded.event_at),
    updated_at = pg_catalog.now()
  returning pg_catalog.jsonb_build_object(
    'mint', mint, 'name', token_name, 'symbol', token_symbol, 'creator', creator,
    'uri', metadata_uri, 'source', source, 'status', status, 'pool', pool
  ) into v_result;

  delete from sniper.launches where received_at < pg_catalog.now() - interval '48 hours';
  delete from sniper.launches where mint in (
    select old.mint from sniper.launches old
    order by old.received_at desc offset 1000
  );
  return v_result;
end;
$function$


revoke all on function public.sniper_launch_upsert(jsonb) from public, anon, authenticated;
grant execute on function public.sniper_launch_upsert(jsonb) to service_role;
