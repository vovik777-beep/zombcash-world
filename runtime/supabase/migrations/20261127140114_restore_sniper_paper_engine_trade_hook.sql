-- Reconstructed from the live Supabase function definition during the 2026-11-27 recovery audit.
-- Restores the atomic Paper Engine hook after each newly persisted trade.
CREATE OR REPLACE FUNCTION public.sniper_trade_upsert(p_event jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
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
  if v_price is null then
    return pg_catalog.jsonb_build_object('ignored',true,'reason','missing_sol_curve_price','signature',v_signature);
  end if;
  v_trader := nullif(pg_catalog.left(p_event->>'trader',64),'');
  v_event_at := coalesce(
    pg_catalog.to_timestamp(((nullif(p_event->>'eventAt','')::numeric / 1000))::double precision),
    pg_catalog.now()
  );
  insert into sniper.token_trades (
    mint,signature,trader,side,sol_amount,token_amount,price_sol,
    market_cap_sol,v_sol_in_bonding_curve,v_tokens_in_bonding_curve,event_at,received_at
  ) values (
    v_mint,v_signature,v_trader,v_side,v_sol,v_tokens,v_price,
    v_market_cap,v_v_sol,v_v_tokens,v_event_at,pg_catalog.now()
  )
  on conflict (signature) do nothing
  returning pg_catalog.jsonb_build_object(
    'mint',mint,'signature',signature,'side',side,'solAmount',sol_amount,
    'tokenAmount',token_amount,'priceSol',price_sol,
    'eventAt',(extract(epoch from event_at)*1000)::bigint
  ) into v_result;

  -- Evaluate paper entries/exits atomically for each newly persisted trade.
  if v_result is not null then
    perform public.sniper_paper_engine_process(v_mint);
  end if;

  delete from sniper.token_trades where received_at < pg_catalog.now()-interval '48 hours';
  delete from sniper.token_trades where id in (
    select id from sniper.token_trades order by received_at desc offset 100000
  );
  return coalesce(v_result,pg_catalog.jsonb_build_object('duplicate',true,'signature',v_signature));
end;
$function$


revoke all on function public.sniper_trade_upsert(jsonb) from public, anon, authenticated;
grant execute on function public.sniper_trade_upsert(jsonb) to service_role;
