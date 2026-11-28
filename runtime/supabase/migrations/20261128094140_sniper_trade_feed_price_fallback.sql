ALTER TABLE sniper.paper_price_marks
    DROP CONSTRAINT IF EXISTS paper_price_marks_source_check;
ALTER TABLE sniper.paper_price_marks
    ADD CONSTRAINT paper_price_marks_source_check
    CHECK (source IN ('dexscreener', 'jupiter', 'geckoterminal', 'trade_feed'));

ALTER TABLE sniper.paper_price_marks
    DROP CONSTRAINT IF EXISTS paper_price_marks_liquidity_usd_check;
ALTER TABLE sniper.paper_price_marks
    ADD CONSTRAINT paper_price_marks_liquidity_usd_check
    CHECK (liquidity_usd >= 500 OR (source = 'trade_feed' AND liquidity_usd = 0));

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
    IF v_source NOT IN ('dexscreener', 'jupiter', 'geckoterminal', 'trade_feed') THEN
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
       OR v_liquidity IS NULL OR v_liquidity < 0 OR v_liquidity >= 'Infinity'::numeric
       OR (v_source <> 'trade_feed' AND v_liquidity < 500)
       OR (v_source = 'trade_feed' AND v_liquidity <> 0)
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
$function$;

CREATE OR REPLACE FUNCTION public.sniper_trade_latest(p_mint text)
 RETURNS jsonb
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO ''
AS $function$
  select coalesce(
    (
      select pg_catalog.jsonb_build_object(
        'mint', latest.mint,
        'source', latest.source,
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
