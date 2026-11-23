-- Add server-verified social metadata and enforce it as a Paper Engine entry gate.
-- Trusted social metadata is populated only by the server's fixed DexScreener
-- lookup. A missing/expired row is unknown and fails closed when required.
alter table sniper.user_settings
  add column if not exists require_socials boolean not null default false;

create table if not exists sniper.token_social_status (
  mint text primary key,
  has_socials boolean not null,
  twitter_url text,
  telegram_url text,
  website_url text,
  checked_at timestamptz not null default pg_catalog.now(),
  provider text not null default 'dexscreener'
);
create index if not exists token_social_status_checked_at_idx
  on sniper.token_social_status(checked_at desc);
revoke all on table sniper.token_social_status from public, anon, authenticated;
grant select,insert,update on table sniper.token_social_status to service_role;

create or replace function public.sniper_user_settings_set_v5(
  p_telegram_id bigint,p_enabled boolean,p_mode text,p_live_confirmation text default null,
  p_paper_capital_sol numeric default null,p_max_trade_sol numeric default null,
  p_take_profit_pct numeric default null,p_stop_loss_pct numeric default null,
  p_min_price_move_pct numeric default null,p_idle_exit_seconds integer default null,
  p_paper_fee_bps integer default null,p_paper_network_fee_sol numeric default null,
  p_max_open_positions integer default null,p_max_daily_loss_sol numeric default null,
  p_min_buy_count integer default null,p_creator_policy text default null,
  p_require_socials boolean default null
)
returns jsonb language plpgsql security definer set search_path = ''
as $function$
declare v_result jsonb; v_required boolean;
begin
  v_result:=public.sniper_user_settings_set_v4(p_telegram_id,p_enabled,p_mode,p_live_confirmation,
    p_paper_capital_sol,p_max_trade_sol,p_take_profit_pct,p_stop_loss_pct,p_min_price_move_pct,
    p_idle_exit_seconds,p_paper_fee_bps,p_paper_network_fee_sol,p_max_open_positions,
    p_max_daily_loss_sol,p_min_buy_count,p_creator_policy);
  update sniper.user_settings set require_socials=coalesce(p_require_socials,require_socials),
    updated_at=pg_catalog.now() where telegram_id=p_telegram_id returning require_socials into v_required;
  return coalesce(v_result,'{}'::jsonb)||pg_catalog.jsonb_build_object('requireSocials',v_required);
end;
$function$;
revoke all on function public.sniper_user_settings_set_v5(bigint,boolean,text,text,numeric,numeric,numeric,numeric,numeric,integer,integer,numeric,integer,numeric,integer,text,boolean) from public,anon,authenticated;
grant execute on function public.sniper_user_settings_set_v5(bigint,boolean,text,text,numeric,numeric,numeric,numeric,numeric,integer,integer,numeric,integer,numeric,integer,text,boolean) to service_role;


-- Keep the private sniper schema out of PostgREST exposure. The API writes
-- trusted social evidence through this narrowly scoped service-role RPC.
create or replace function public.sniper_token_social_status_upsert(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = ''
as $function$
declare v_count integer;
begin
  if pg_catalog.jsonb_typeof(p_rows) <> 'array'
     or pg_catalog.jsonb_array_length(p_rows) > 90 then
    raise exception 'invalid_social_status_batch';
  end if;

  insert into sniper.token_social_status(
    mint,has_socials,twitter_url,telegram_url,website_url,checked_at,provider
  )
  select
    x.mint,x.has_socials,x.twitter_url,x.telegram_url,x.website_url,
    coalesce(x.checked_at,pg_catalog.now()),'dexscreener'
  from pg_catalog.jsonb_to_recordset(p_rows) as x(
    mint text,has_socials boolean,twitter_url text,telegram_url text,
    website_url text,checked_at timestamptz,provider text
  )
  where x.mint ~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$'
  on conflict (mint) do update set
    has_socials=excluded.has_socials,
    twitter_url=excluded.twitter_url,
    telegram_url=excluded.telegram_url,
    website_url=excluded.website_url,
    checked_at=excluded.checked_at,
    provider='dexscreener';

  get diagnostics v_count = row_count;
  return v_count;
end;
$function$;

revoke all on function public.sniper_token_social_status_upsert(jsonb) from public,anon,authenticated;
grant execute on function public.sniper_token_social_status_upsert(jsonb) to service_role;
