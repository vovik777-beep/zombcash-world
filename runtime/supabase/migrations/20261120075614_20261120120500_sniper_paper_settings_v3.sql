-- Versioned settings RPC for server Paper Engine risk controls.
create or replace function public.sniper_user_settings_set_v3(
  p_telegram_id bigint,p_enabled boolean,p_mode text,p_live_confirmation text default null,
  p_paper_capital_sol numeric default null,p_max_trade_sol numeric default null,
  p_take_profit_pct numeric default null,p_stop_loss_pct numeric default null,
  p_min_price_move_pct numeric default null,p_idle_exit_seconds integer default null,
  p_paper_fee_bps integer default null,p_paper_network_fee_sol numeric default null,
  p_max_open_positions integer default null,p_max_daily_loss_sol numeric default null,
  p_min_buy_count integer default null
)
returns jsonb language plpgsql security definer set search_path = ''
as $function$
declare v_result jsonb; v_row sniper.user_settings%rowtype;
begin
  if p_max_open_positions is not null and (p_max_open_positions<1 or p_max_open_positions>20) then raise exception 'invalid_max_open_positions'; end if;
  if p_max_daily_loss_sol is not null and (p_max_daily_loss_sol<0 or p_max_daily_loss_sol>1000) then raise exception 'invalid_max_daily_loss_sol'; end if;
  if p_min_buy_count is not null and (p_min_buy_count<1 or p_min_buy_count>100) then raise exception 'invalid_min_buy_count'; end if;
  v_result:=public.sniper_user_settings_set_v2(p_telegram_id,p_enabled,p_mode,p_live_confirmation,
    p_paper_capital_sol,p_max_trade_sol,p_take_profit_pct,p_stop_loss_pct,p_min_price_move_pct,
    p_idle_exit_seconds,p_paper_fee_bps,p_paper_network_fee_sol);
  update sniper.user_settings set max_open_positions=coalesce(p_max_open_positions,max_open_positions),
    max_daily_loss_sol=coalesce(p_max_daily_loss_sol,max_daily_loss_sol),
    min_buy_count=coalesce(p_min_buy_count,min_buy_count),updated_at=pg_catalog.now()
    where telegram_id=p_telegram_id returning * into v_row;
  return coalesce(v_result,'{}'::jsonb)||pg_catalog.jsonb_build_object(
    'maxOpenPositions',v_row.max_open_positions,'maxDailyLossSol',v_row.max_daily_loss_sol,'minBuyCount',v_row.min_buy_count);
end;
$function$;
revoke all on function public.sniper_user_settings_set_v3(bigint,boolean,text,text,numeric,numeric,numeric,numeric,numeric,integer,integer,numeric,integer,numeric,integer) from public,anon,authenticated;
grant execute on function public.sniper_user_settings_set_v3(bigint,boolean,text,text,numeric,numeric,numeric,numeric,numeric,integer,integer,numeric,integer,numeric,integer) to service_role;
