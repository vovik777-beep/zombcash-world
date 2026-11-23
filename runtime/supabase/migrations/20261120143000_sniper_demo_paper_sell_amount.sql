-- Preserve the DEMO UI's gross exit amount when positions are synchronized.
-- This is additive and only affects the isolated DEMO table and RPCs.
alter table sniper.demo_paper_positions
  add column if not exists sell_amount_sol numeric not null default 0;
alter table sniper.demo_paper_positions
  add constraint demo_paper_sell_amount check (sell_amount_sol >= 0 and sell_amount_sol <= 1000);

create or replace function public.sniper_demo_paper_list(p_telegram_id bigint)
returns jsonb language plpgsql security definer set search_path = ''
as $function$
begin
  if p_telegram_id is null or not exists (
    select 1 from public.survivor_profiles sp where sp.telegram_id=p_telegram_id
  ) then raise exception 'registered_profile_required'; end if;

  return coalesce((
    select pg_catalog.jsonb_agg(q.item order by q.created_at desc)
    from (
      select pg_catalog.jsonb_build_object(
        'id',p.id,'mint',p.mint,'symbol',p.token_symbol,'name',p.token_name,
        'buySol',p.buy_amount_sol,'entryPrice',p.entry_price_sol,'lastPrice',p.last_price_sol,
        'quantity',p.token_quantity,'netPnl',p.net_pnl_sol,'netPct',p.net_pnl_pct,
        'sellSol',p.sell_amount_sol,'status',p.status,'exitReason',p.exit_reason,
        'openedAt',(extract(epoch from p.opened_at)*1000)::bigint,
        'closedAt',case when p.closed_at is null then null else (extract(epoch from p.closed_at)*1000)::bigint end
      ) as item,p.created_at
      from sniper.demo_paper_positions p
      where p.telegram_id=p_telegram_id
      order by p.created_at desc limit 51
    ) q
  ),'[]'::jsonb);
end;
$function$;

create or replace function public.sniper_demo_paper_save(p_telegram_id bigint,p_position jsonb)
returns jsonb language plpgsql security definer set search_path = ''
as $function$
declare
  v_id uuid; v_mint text; v_symbol text; v_name text; v_buy numeric;
  v_entry numeric; v_last numeric; v_quantity numeric; v_net numeric; v_pct numeric;
  v_sell numeric; v_status text; v_exit_reason text; v_opened_at timestamptz; v_closed_at timestamptz;
  v_result jsonb;
begin
  if p_telegram_id is null or not exists (
    select 1 from public.survivor_profiles sp where sp.telegram_id=p_telegram_id
  ) then raise exception 'registered_profile_required'; end if;
  if p_position is null or pg_catalog.jsonb_typeof(p_position)<>'object' then
    raise exception 'invalid_paper_position';
  end if;

  v_id:=coalesce(nullif(p_position->>'id','')::uuid,pg_catalog.gen_random_uuid());
  v_mint:=coalesce(p_position->>'mint','');
  v_symbol:=coalesce(nullif(pg_catalog.left(p_position->>'symbol',24),''),'TOKEN');
  v_name:=coalesce(pg_catalog.left(p_position->>'name',96),'');
  v_buy:=nullif(p_position->>'buySol','')::numeric;
  v_entry:=nullif(p_position->>'entryPrice','')::numeric;
  v_last:=nullif(p_position->>'lastPrice','')::numeric;
  v_quantity:=nullif(p_position->>'quantity','')::numeric;
  v_net:=coalesce(nullif(p_position->>'netPnl','')::numeric,0);
  v_pct:=coalesce(nullif(p_position->>'netPct','')::numeric,0);
  v_sell:=coalesce(nullif(p_position->>'sellSol','')::numeric,0);
  v_status:=p_position->>'status';
  v_exit_reason:=nullif(p_position->>'exitReason','');

  if pg_catalog.length(v_mint) not between 32 and 44 or v_mint !~ '^[1-9A-HJ-NP-Za-km-z]+$'
    then raise exception 'invalid_token_mint'; end if;
  if v_buy is null or v_buy<=0 or v_buy>1000 or v_entry is null or v_entry<=0
    or v_last is null or v_last<=0 or v_quantity is null or v_quantity<=0
    then raise exception 'invalid_paper_amounts'; end if;
  if v_sell<0 or v_sell>1000 then raise exception 'invalid_paper_sell_amount'; end if;
  if v_status not in ('open','closed') then raise exception 'invalid_paper_status'; end if;
  if v_status='open' and v_exit_reason is not null then raise exception 'invalid_open_paper_exit_reason'; end if;
  if v_status='closed' and v_exit_reason is null then v_exit_reason:='manual'; end if;
  if v_exit_reason is not null and v_exit_reason not in ('target','stop','manual','stagnant')
    then raise exception 'invalid_paper_exit_reason'; end if;

  v_opened_at:=coalesce(
    pg_catalog.to_timestamp(((nullif(p_position->>'openedAt','')::numeric/1000))::double precision),
    pg_catalog.now()
  );
  v_closed_at:=case when v_status='closed' and nullif(p_position->>'closedAt','') is not null
    then pg_catalog.to_timestamp(((p_position->>'closedAt')::numeric/1000)::double precision)
    else null end;
  if v_status='closed' and v_closed_at is null then raise exception 'closed_paper_position_needs_closed_at'; end if;

  insert into sniper.demo_paper_positions(
    id,telegram_id,mint,token_symbol,token_name,buy_amount_sol,entry_price_sol,
    last_price_sol,token_quantity,net_pnl_sol,net_pnl_pct,sell_amount_sol,status,exit_reason,opened_at,closed_at,updated_at
  ) values (
    v_id,p_telegram_id,v_mint,v_symbol,v_name,v_buy,v_entry,v_last,v_quantity,
    v_net,v_pct,v_sell,v_status,v_exit_reason,v_opened_at,v_closed_at,pg_catalog.now()
  )
  on conflict(id) do update set
    mint=excluded.mint,token_symbol=excluded.token_symbol,token_name=excluded.token_name,
    buy_amount_sol=excluded.buy_amount_sol,entry_price_sol=excluded.entry_price_sol,
    last_price_sol=excluded.last_price_sol,token_quantity=excluded.token_quantity,
    net_pnl_sol=excluded.net_pnl_sol,net_pnl_pct=excluded.net_pnl_pct,
    sell_amount_sol=excluded.sell_amount_sol,status=excluded.status,exit_reason=excluded.exit_reason,
    opened_at=excluded.opened_at,closed_at=excluded.closed_at,updated_at=pg_catalog.now()
  where sniper.demo_paper_positions.telegram_id=p_telegram_id
  returning pg_catalog.jsonb_build_object(
    'id',id,'mint',mint,'symbol',token_symbol,'name',token_name,
    'buySol',buy_amount_sol,'entryPrice',entry_price_sol,'lastPrice',last_price_sol,
    'quantity',token_quantity,'netPnl',net_pnl_sol,'netPct',net_pnl_pct,
    'sellSol',sell_amount_sol,'status',status,'exitReason',exit_reason,
    'openedAt',(extract(epoch from opened_at)*1000)::bigint,
    'closedAt',case when closed_at is null then null else (extract(epoch from closed_at)*1000)::bigint end
  ) into v_result;

  if v_result is null then raise exception 'paper_position_not_owned'; end if;
  delete from sniper.demo_paper_positions p
   where p.telegram_id=p_telegram_id and p.status='closed'
     and p.id not in (
       select keep.id from sniper.demo_paper_positions keep
        where keep.telegram_id=p_telegram_id and keep.status='closed'
        order by keep.created_at desc limit 50
     );
  return v_result;
end;
$function$;
