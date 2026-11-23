-- Per-registered-user managed Solana wallets.
-- Private key material is accepted only by this SECURITY DEFINER function,
-- encrypted immediately by Supabase Vault, and never returned by any wallet RPC.

alter table sniper.user_settings
  add column if not exists live_armed boolean not null default false,
  add column if not exists live_confirmed_at timestamptz;
alter table sniper.user_settings
  add column if not exists paper_capital_sol numeric not null default 10 check (paper_capital_sol > 0),
  add column if not exists min_buy_count integer not null default 3 check (min_buy_count >= 1 and min_buy_count <= 100),
  add column if not exists min_price_move_pct numeric not null default 0.2 check (min_price_move_pct >= 0 and min_price_move_pct <= 1000),
  add column if not exists stop_loss_pct numeric not null default 5 check (stop_loss_pct > 0 and stop_loss_pct <= 100),
  add column if not exists idle_exit_seconds integer not null default 60 check (idle_exit_seconds >= 15 and idle_exit_seconds <= 3600),
  add column if not exists paper_fee_bps integer not null default 100 check (paper_fee_bps >= 0 and paper_fee_bps <= 5000);

alter table sniper.user_settings
  add column if not exists paper_network_fee_sol numeric not null default 0.00005 check (paper_network_fee_sol >= 0 and paper_network_fee_sol <= 0.01);

alter table sniper.paper_positions
  add column if not exists last_movement_at timestamptz not null default now();

create or replace function public.sniper_wallet_provision(
  p_telegram_id bigint,
  p_public_key text,
  p_secret_key text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_existing record;
  v_secret_id uuid;
begin
  if p_telegram_id is null or p_telegram_id <= 0 then
    raise exception 'registered_profile_required';
  end if;
  if p_public_key is null or p_public_key !~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$' then
    raise exception 'invalid_wallet_public_key';
  end if;
  if p_secret_key is null or pg_catalog.length(p_secret_key) < 80
     or pg_catalog.length(p_secret_key) > 100
     or p_secret_key !~ '^[1-9A-HJ-NP-Za-km-z]+$' then
    raise exception 'invalid_wallet_secret_payload';
  end if;

  perform 1 from public.survivor_profiles
   where telegram_id = p_telegram_id
   for update;
  if not found then raise exception 'registered_profile_required'; end if;

  select id, public_key, status
    into v_existing
    from sniper.wallets
   where telegram_id = p_telegram_id
   limit 1;

  if found then
    return pg_catalog.jsonb_build_object(
      'walletId', v_existing.id,
      'publicKey', v_existing.public_key,
      'status', v_existing.status,
      'created', false
    );
  end if;

  v_secret_id := vault.create_secret(
    p_secret_key,
    'zombcash-solana-wallet-' || p_telegram_id::text,
    'Managed Solana wallet secret. Access only through the server-side signer.'
  );

  insert into sniper.wallets (
    telegram_id, chain, public_key, vault_secret_id,
    custody_mode, status, key_version, activated_at
  ) values (
    p_telegram_id, 'solana', p_public_key, v_secret_id,
    'managed', 'active', 1, pg_catalog.now()
  )
  returning id into v_existing.id;

  insert into sniper.user_settings (
    telegram_id, enabled, mode, take_profit_pct, max_trade_sol,
    max_open_positions, max_daily_loss_sol, max_slippage_bps, buy_window_seconds
  ) values (
    p_telegram_id, false, 'paper', 10, 0.05, 1, 0.10, 300, 60
  )
  on conflict (telegram_id) do nothing;

  return pg_catalog.jsonb_build_object(
    'walletId', v_existing.id,
    'publicKey', p_public_key,
    'status', 'active',
    'created', true
  );
end;
$function$;

revoke all on function public.sniper_wallet_provision(bigint,text,text) from public, anon, authenticated;
grant execute on function public.sniper_wallet_provision(bigint,text,text) to service_role;

create or replace function public.sniper_wallet_get(p_telegram_id bigint)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $function$
  select coalesce(
    (
      select pg_catalog.jsonb_build_object(
        'walletId', w.id,
        'publicKey', w.public_key,
        'status', w.status,
        'custodyMode', w.custody_mode,
        'createdAt', (extract(epoch from w.created_at) * 1000)::bigint
      )
      from sniper.wallets w
      where w.telegram_id = p_telegram_id
      limit 1
    ),
    '{}'::jsonb
  );
$function$;

revoke all on function public.sniper_wallet_get(bigint) from public, anon, authenticated;
grant execute on function public.sniper_wallet_get(bigint) to service_role;

-- This function is intentionally service-role-only. It is not exposed to browser users.
create or replace function public.sniper_wallet_secret(p_telegram_id bigint)
returns text
language sql
stable
security definer
set search_path = ''
as $function$
  select ds.decrypted_secret
  from sniper.wallets w
  join vault.decrypted_secrets ds on ds.id = w.vault_secret_id
  where w.telegram_id = p_telegram_id
    and w.status = 'active'
    and w.custody_mode = 'managed'
  limit 1;
$function$;

revoke all on function public.sniper_wallet_secret(bigint) from public, anon, authenticated;
grant execute on function public.sniper_wallet_secret(bigint) to service_role;


create or replace function public.sniper_user_settings_get(p_telegram_id bigint)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $function$
  select coalesce(
    (
      select pg_catalog.jsonb_build_object(
        'enabled', s.enabled,
        'mode', s.mode,
        'live_armed', s.live_armed,
        'paper_capital_sol', s.paper_capital_sol,
        'min_buy_count', s.min_buy_count,
        'min_price_move_pct', s.min_price_move_pct,
        'stop_loss_pct', s.stop_loss_pct,
        'idle_exit_seconds', s.idle_exit_seconds,
        'paper_fee_bps', s.paper_fee_bps,
        'paper_network_fee_sol', s.paper_network_fee_sol,
        'take_profit_pct', s.take_profit_pct,
        'max_trade_sol', s.max_trade_sol,
        'max_open_positions', s.max_open_positions,
        'max_daily_loss_sol', s.max_daily_loss_sol,
        'max_slippage_bps', s.max_slippage_bps,
        'buy_window_seconds', s.buy_window_seconds
      )
      from sniper.user_settings s
      where s.telegram_id = p_telegram_id
      limit 1
    ),
    pg_catalog.jsonb_build_object(
      'enabled', false, 'mode', 'paper', 'live_armed', false,
      'paper_capital_sol', 10, 'min_buy_count', 3, 'min_price_move_pct', 0.2,
      'stop_loss_pct', 5, 'idle_exit_seconds', 60, 'paper_fee_bps', 100, 'paper_network_fee_sol', 0.00005,
      'take_profit_pct', 10,
      'max_trade_sol', 0.05, 'max_open_positions', 1,
      'max_daily_loss_sol', 0.10, 'max_slippage_bps', 300,
      'buy_window_seconds', 60
    )
  );
$function$;

revoke all on function public.sniper_user_settings_get(bigint) from public, anon, authenticated;
grant execute on function public.sniper_user_settings_get(bigint) to service_role;


alter table sniper.user_settings
  add column if not exists live_armed boolean not null default false,
  add column if not exists live_confirmed_at timestamptz;

create or replace function public.sniper_user_settings_set(
  p_telegram_id bigint,
  p_enabled boolean,
  p_mode text,
  p_live_confirmation text default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_row sniper.user_settings%rowtype;
  v_live_armed boolean := false;
begin
  if p_telegram_id is null or not exists (
    select 1 from public.survivor_profiles where telegram_id = p_telegram_id
  ) then
    raise exception 'registered_profile_required';
  end if;
  if p_enabled is null or p_mode not in ('paper','live') then
    raise exception 'invalid_sniper_settings';
  end if;

  if p_mode = 'live' and p_enabled then
    if p_live_confirmation is distinct from 'ENABLE LIVE TRADING' then
      raise exception 'explicit_live_confirmation_required';
    end if;
    if not exists (
      select 1 from sniper.wallets
      where telegram_id = p_telegram_id and status = 'active'
        and custody_mode = 'managed' and vault_secret_id is not null
    ) then
      raise exception 'active_managed_wallet_required';
    end if;
    v_live_armed := true;
  end if;

  insert into sniper.user_settings (telegram_id, enabled, mode, live_armed, live_confirmed_at)
  values (
    p_telegram_id, p_enabled, p_mode, v_live_armed,
    case when v_live_armed then pg_catalog.now() else null end
  )
  on conflict (telegram_id) do update set
    enabled = excluded.enabled,
    mode = excluded.mode,
    live_armed = excluded.live_armed,
    live_confirmed_at = case
      when excluded.live_armed then pg_catalog.now()
      else null
    end,
    updated_at = pg_catalog.now()
  returning * into v_row;

  return pg_catalog.jsonb_build_object(
    'enabled', v_row.enabled,
    'mode', v_row.mode,
    'liveArmed', v_row.live_armed,
    'liveConfirmedAt', v_row.live_confirmed_at
  );
end;
$function$;

revoke all on function public.sniper_user_settings_set(bigint,boolean,text,text) from public, anon, authenticated;
grant execute on function public.sniper_user_settings_set(bigint,boolean,text,text) to service_role;


-- Server-side demo engine: it continues to run when no user has the website open.
alter table sniper.paper_positions
  drop constraint if exists paper_positions_exit_reason_check;
alter table sniper.paper_positions
  add constraint paper_positions_exit_reason_check
  check (exit_reason is null or exit_reason in ('target','stop','manual','stagnant'));

create or replace function public.sniper_paper_engine_process(p_mint text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_launch record;
  v_stats record;
  v_price numeric;
  v_now timestamptz := pg_catalog.now();
  v_user record;
  v_position record;
  v_fee_fraction numeric;
  v_net numeric;
  v_pct numeric;
  v_daily_loss numeric;
  v_available numeric;
  v_buy numeric;
  v_quantity numeric;
  v_reason text;
  v_entered integer := 0;
  v_updated integer := 0;
  v_closed integer := 0;
begin
  if p_mint is null or pg_catalog.length(p_mint) < 32 or pg_catalog.length(p_mint) > 44
     or p_mint !~ '^[1-9A-HJ-NP-Za-km-z]+$' then
    raise exception 'invalid_sniper_paper_mint';
  end if;

  select l.event_at, l.token_name, l.token_symbol
    into v_launch
    from sniper.launches l
   where l.mint = p_mint;
  if not found then
    return pg_catalog.jsonb_build_object('ok', false, 'reason', 'launch_not_found');
  end if;

  select
    count(*)::integer as trade_count,
    count(*) filter (where t.side='buy')::integer as buy_count,
    count(*) filter (where t.side='sell')::integer as sell_count,
    coalesce(sum(t.sol_amount) filter (where t.side='buy'),0) as buy_sol,
    coalesce(sum(t.sol_amount) filter (where t.side='sell'),0) as sell_sol,
    (array_agg(t.price_sol order by t.event_at asc) filter (where t.price_sol is not null))[1] as first_price,
    (array_agg(t.price_sol order by t.event_at desc) filter (where t.price_sol is not null))[1] as last_price
    into v_stats
    from sniper.token_trades t
   where t.mint = p_mint
     and t.event_at >= v_launch.event_at
     and t.event_at < v_launch.event_at + interval '60 seconds';

  select t.price_sol into v_price
    from sniper.token_trades t
   where t.mint = p_mint and t.price_sol > 0
   order by t.event_at desc
   limit 1;
  if v_price is null or v_price <= 0 then
    return pg_catalog.jsonb_build_object('ok', true, 'entered', 0, 'updated', 0, 'closed', 0);
  end if;

  for v_user in
    select s.*
      from sniper.user_settings s
     where s.mode = 'paper'
       and (
         s.enabled = true
         or exists (
           select 1 from sniper.paper_positions p
           where p.telegram_id = s.telegram_id and p.status = 'open'
         )
       )
     order by s.telegram_id
  loop
    select p.*
      into v_position
      from sniper.paper_positions p
     where p.telegram_id = v_user.telegram_id
       and p.mint = p_mint
       and p.status = 'open'
     order by p.created_at desc
     limit 1
     for update;

    if found then
      v_fee_fraction := greatest(0, least(v_user.paper_fee_bps,5000))::numeric / 20000;
      v_net := (v_position.token_quantity * v_price * (1 - v_fee_fraction)) - v_user.paper_network_fee_sol - v_position.buy_amount_sol;
      v_pct := case when v_position.buy_amount_sol > 0 then v_net / v_position.buy_amount_sol * 100 else 0 end;
      v_reason := case
        when v_pct >= v_user.take_profit_pct then 'target'
        when v_pct <= -v_user.stop_loss_pct then 'stop'
        else null
      end;

      update sniper.paper_positions
         set last_price_sol = v_price,
             net_pnl_sol = v_net,
             net_pnl_pct = v_pct,
             status = case when v_reason is null then 'open' else 'closed' end,
             exit_reason = v_reason,
             closed_at = case when v_reason is null then null else v_now end,
             last_movement_at = case
               when v_position.last_price_sol > 0
                 and abs((v_price / v_position.last_price_sol - 1) * 100) >= v_user.min_price_move_pct
                 then v_now
               else v_position.last_movement_at
             end,
             updated_at = v_now
       where id = v_position.id;

      v_updated := v_updated + 1;
      if v_reason is not null then
        v_closed := v_closed + 1;
        insert into sniper.events (telegram_id, event_type, severity, event_code, details)
        values (
          v_user.telegram_id, 'paper_sell', 'info', v_reason,
          pg_catalog.jsonb_build_object('mint',p_mint,'netPnlSol',v_net,'netPnlPct',v_pct,'priceSol',v_price)
        );
      end if;
      continue;
    end if;

    -- Pausing stops new entries, but an existing position remains managed to its exit.
    if not v_user.enabled then
      continue;
    end if;

    -- Never re-enter the same Mint after a previous position was closed.
    if exists (
      select 1 from sniper.paper_positions p
       where p.telegram_id = v_user.telegram_id and p.mint = p_mint
    ) then
      continue;
    end if;

    -- Entry is strictly within the Mint's first 60 seconds and does not depend on a DEX pool.
    if v_now > v_launch.event_at + interval '60 seconds'
       or v_stats.trade_count < v_user.min_buy_count
       or v_stats.buy_count <= v_stats.sell_count
       or (coalesce(v_stats.buy_sol,0) - coalesce(v_stats.sell_sol,0)) <= 0
       or v_stats.first_price is null or v_stats.first_price <= 0
       or v_stats.last_price is null or v_stats.last_price <= 0
       or ((v_stats.last_price / v_stats.first_price - 1) * 100) < v_user.min_price_move_pct then
      continue;
    end if;

    if (
      select count(*) from sniper.paper_positions p
       where p.telegram_id = v_user.telegram_id and p.status = 'open'
    ) >= v_user.max_open_positions then
      continue;
    end if;

    select coalesce(sum(greatest(-p.net_pnl_sol,0)),0)
      into v_daily_loss
      from sniper.paper_positions p
     where p.telegram_id = v_user.telegram_id
       and p.status = 'closed'
       and p.closed_at >= pg_catalog.date_trunc('day',v_now);
    if v_daily_loss >= v_user.max_daily_loss_sol then
      continue;
    end if;

    select
      v_user.paper_capital_sol
      + coalesce(sum(p.net_pnl_sol) filter (where p.status='closed'),0)
      - coalesce(sum(p.buy_amount_sol) filter (where p.status='open'),0)
      into v_available
      from sniper.paper_positions p
     where p.telegram_id = v_user.telegram_id;
    v_buy := least(v_user.max_trade_sol,0.05,coalesce(v_available,0));
    if v_buy <= 0 then
      continue;
    end if;

    v_fee_fraction := greatest(0, least(v_user.paper_fee_bps,5000))::numeric / 20000;
    v_quantity := (v_buy - v_user.paper_network_fee_sol - (v_buy * v_fee_fraction)) / v_price;
    if v_quantity <= 0 then
      continue;
    end if;

    insert into sniper.paper_positions (
      id, telegram_id, mint, token_symbol, token_name, buy_amount_sol,
      entry_price_sol, last_price_sol, token_quantity, net_pnl_sol,
      net_pnl_pct, status, exit_reason, opened_at, updated_at
    ) values (
      pg_catalog.gen_random_uuid(), v_user.telegram_id, p_mint,
      coalesce(nullif(v_launch.token_symbol,''),'TOKEN'),
      coalesce(v_launch.token_name,''),
      v_buy, v_price, v_price, v_quantity, 0, 0,
      'open', null, v_now, v_now
    );

    insert into sniper.events (telegram_id, event_type, severity, event_code, details)
    values (
      v_user.telegram_id, 'paper_buy', 'info', 'first_minute_momentum',
      pg_catalog.jsonb_build_object(
        'mint',p_mint,'buySol',v_buy,'entryPriceSol',v_price,
        'tradeCount',v_stats.trade_count,'buyCount',v_stats.buy_count,
        'sellCount',v_stats.sell_count,'netSol',v_stats.buy_sol-v_stats.sell_sol,
        'priceChangePct',(v_stats.last_price/v_stats.first_price-1)*100
      )
    );
    v_entered := v_entered + 1;
  end loop;

  return pg_catalog.jsonb_build_object(
    'ok', true, 'entered', v_entered, 'updated', v_updated, 'closed', v_closed
  );
end;
$function$;

revoke all on function public.sniper_paper_engine_process(text) from public, anon, authenticated;
grant execute on function public.sniper_paper_engine_process(text) to service_role;

create or replace function public.sniper_paper_engine_tick()
returns integer
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_closed integer := 0;
begin
  with expired as (
    update sniper.paper_positions p
       set status = 'closed',
           exit_reason = 'stagnant',
           closed_at = pg_catalog.now(),
           updated_at = pg_catalog.now()
      from sniper.user_settings s
     where s.telegram_id = p.telegram_id
       and p.status = 'open'
       and p.last_movement_at <= pg_catalog.now() - pg_catalog.make_interval(secs => s.idle_exit_seconds)
    returning p.telegram_id, p.mint, p.net_pnl_sol, p.net_pnl_pct, p.last_price_sol
  ), logged as (
    insert into sniper.events (telegram_id, event_type, severity, event_code, details)
    select
      e.telegram_id, 'paper_sell', 'info', 'stagnant',
      pg_catalog.jsonb_build_object(
        'mint',e.mint,'netPnlSol',e.net_pnl_sol,
        'netPnlPct',e.net_pnl_pct,'priceSol',e.last_price_sol
      )
    from expired e
    returning 1
  )
  select count(*)::integer into v_closed from expired;
  return v_closed;
end;
$function$;

revoke all on function public.sniper_paper_engine_tick() from public, anon, authenticated;
grant execute on function public.sniper_paper_engine_tick() to service_role;


-- Keep browser-synced manual/demo positions compatible with server idle-rotation exits.
create or replace function public.sniper_paper_save(p_telegram_id bigint, p_position jsonb)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_id uuid;
  v_mint text;
  v_symbol text;
  v_name text;
  v_buy numeric;
  v_entry numeric;
  v_last numeric;
  v_quantity numeric;
  v_net numeric;
  v_pct numeric;
  v_status text;
  v_exit_reason text;
  v_opened_at timestamptz;
  v_closed_at timestamptz;
  v_result jsonb;
begin
  if p_telegram_id is null or not exists (
    select 1 from public.survivor_profiles sp where sp.telegram_id = p_telegram_id
  ) then raise exception 'registered_profile_required'; end if;
  if p_position is null or pg_catalog.jsonb_typeof(p_position) <> 'object' then
    raise exception 'invalid_paper_position';
  end if;

  v_id := coalesce(nullif(p_position->>'id','')::uuid, pg_catalog.gen_random_uuid());
  v_mint := coalesce(p_position->>'mint','');
  v_symbol := coalesce(nullif(pg_catalog.left(p_position->>'symbol',24),''),'TOKEN');
  v_name := coalesce(pg_catalog.left(p_position->>'name',96),'');
  v_buy := nullif(p_position->>'buySol','')::numeric;
  v_entry := nullif(p_position->>'entryPrice','')::numeric;
  v_last := nullif(p_position->>'lastPrice','')::numeric;
  v_quantity := nullif(p_position->>'quantity','')::numeric;
  v_net := coalesce(nullif(p_position->>'netPnl','')::numeric,0);
  v_pct := coalesce(nullif(p_position->>'netPct','')::numeric,0);
  v_status := p_position->>'status';
  v_exit_reason := nullif(p_position->>'exitReason','');

  if pg_catalog.length(v_mint) < 32 or pg_catalog.length(v_mint) > 44
     or v_mint !~ '^[1-9A-HJ-NP-Za-km-z]+$' then raise exception 'invalid_token_mint'; end if;
  if v_buy is null or v_buy <= 0 or v_buy > 0.05 or v_entry is null or v_entry <= 0
     or v_last is null or v_last <= 0 or v_quantity is null or v_quantity <= 0 then
    raise exception 'invalid_paper_amounts';
  end if;
  if v_status not in ('open','closed') then raise exception 'invalid_paper_status'; end if;
  if v_status = 'open' and v_exit_reason is not null then
    raise exception 'invalid_open_paper_exit_reason';
  end if;
  if v_status = 'closed' and v_exit_reason is null then v_exit_reason := 'manual'; end if;
  if v_exit_reason is not null and v_exit_reason not in ('target','stop','manual','stagnant') then
    raise exception 'invalid_paper_exit_reason';
  end if;

  v_opened_at := coalesce(
    pg_catalog.to_timestamp(((nullif(p_position->>'openedAt','')::numeric / 1000))::double precision),
    pg_catalog.now()
  );
  v_closed_at := case
    when v_status = 'closed' and nullif(p_position->>'closedAt','') is not null
      then pg_catalog.to_timestamp(((p_position->>'closedAt')::numeric / 1000)::double precision)
    else null
  end;
  if v_status = 'closed' and v_closed_at is null then
    raise exception 'closed_paper_position_needs_closed_at';
  end if;

  insert into sniper.paper_positions (
    id, telegram_id, mint, token_symbol, token_name, buy_amount_sol,
    entry_price_sol, last_price_sol, token_quantity, net_pnl_sol,
    net_pnl_pct, status, exit_reason, opened_at, closed_at, updated_at
  ) values (
    v_id, p_telegram_id, v_mint, v_symbol, v_name, v_buy,
    v_entry, v_last, v_quantity, v_net,
    v_pct, v_status, v_exit_reason, v_opened_at, v_closed_at, pg_catalog.now()
  )
  on conflict (id) do update set
    mint = excluded.mint, token_symbol = excluded.token_symbol, token_name = excluded.token_name,
    buy_amount_sol = excluded.buy_amount_sol, entry_price_sol = excluded.entry_price_sol,
    last_price_sol = excluded.last_price_sol, token_quantity = excluded.token_quantity,
    net_pnl_sol = excluded.net_pnl_sol, net_pnl_pct = excluded.net_pnl_pct,
    status = excluded.status, exit_reason = excluded.exit_reason, opened_at = excluded.opened_at,
    closed_at = excluded.closed_at,
    last_movement_at = case
      when sniper.paper_positions.last_price_sol > 0
        and abs((excluded.last_price_sol / sniper.paper_positions.last_price_sol - 1) * 100)
          >= coalesce((
            select settings.min_price_move_pct
            from sniper.user_settings settings
            where settings.telegram_id = p_telegram_id
          ),0.2)
        then pg_catalog.now()
      else sniper.paper_positions.last_movement_at
    end,
    updated_at = pg_catalog.now()
  where sniper.paper_positions.telegram_id = p_telegram_id
  returning pg_catalog.jsonb_build_object(
    'id', id, 'mint', mint, 'symbol', token_symbol, 'name', token_name,
    'buySol', buy_amount_sol, 'entryPrice', entry_price_sol, 'lastPrice', last_price_sol,
    'quantity', token_quantity, 'netPnl', net_pnl_sol, 'netPct', net_pnl_pct,
    'status', status, 'exitReason', exit_reason,
    'openedAt', (extract(epoch from opened_at) * 1000)::bigint,
    'closedAt', case when closed_at is null then null else (extract(epoch from closed_at) * 1000)::bigint end
  ) into v_result;

  if v_result is null then raise exception 'paper_position_not_owned'; end if;
  delete from sniper.paper_positions p
   where p.telegram_id = p_telegram_id and p.status = 'closed'
     and p.id not in (
       select keep.id from sniper.paper_positions keep
        where keep.telegram_id = p_telegram_id and keep.status = 'closed'
        order by keep.created_at desc limit 50
     );
  return v_result;
end;
$function$;

revoke all on function public.sniper_paper_save(bigint,jsonb) from public, anon, authenticated;
grant execute on function public.sniper_paper_save(bigint,jsonb) to service_role;

drop function if exists public.sniper_user_settings_set_v2(bigint,boolean,text,text,numeric,numeric,numeric,numeric,numeric,integer);

create or replace function public.sniper_user_settings_set_v2(
  p_telegram_id bigint,
  p_enabled boolean,
  p_mode text,
  p_live_confirmation text default null,
  p_paper_capital_sol numeric default null,
  p_max_trade_sol numeric default null,
  p_take_profit_pct numeric default null,
  p_stop_loss_pct numeric default null,
  p_min_price_move_pct numeric default null,
  p_idle_exit_seconds integer default null,
  p_paper_fee_bps integer default null,
  p_paper_network_fee_sol numeric default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_row sniper.user_settings%rowtype;
  v_live_armed boolean := false;
begin
  if p_telegram_id is null or not exists (
    select 1 from public.survivor_profiles where telegram_id = p_telegram_id
  ) then raise exception 'registered_profile_required'; end if;
  if p_enabled is null or p_mode not in ('paper','live') then
    raise exception 'invalid_sniper_settings';
  end if;
  if p_paper_capital_sol is not null and (p_paper_capital_sol <= 0 or p_paper_capital_sol > 1000) then
    raise exception 'invalid_paper_capital';
  end if;
  if p_max_trade_sol is not null and (p_max_trade_sol <= 0 or p_max_trade_sol > 0.05) then
    raise exception 'invalid_paper_trade_size';
  end if;
  if p_take_profit_pct is not null and (p_take_profit_pct <= 0 or p_take_profit_pct > 500) then
    raise exception 'invalid_take_profit';
  end if;
  if p_stop_loss_pct is not null and (p_stop_loss_pct <= 0 or p_stop_loss_pct > 100) then
    raise exception 'invalid_stop_loss';
  end if;
  if p_min_price_move_pct is not null and (p_min_price_move_pct < 0.05 or p_min_price_move_pct > 2) then
    raise exception 'invalid_minimum_price_move';
  end if;
  if p_idle_exit_seconds is not null and (p_idle_exit_seconds < 15 or p_idle_exit_seconds > 300) then
    raise exception 'invalid_idle_exit_seconds';
  end if;
  if p_paper_fee_bps is not null and (p_paper_fee_bps < 0 or p_paper_fee_bps > 5000) then
    raise exception 'invalid_paper_fee_bps';
  end if;
  if p_paper_network_fee_sol is not null and (p_paper_network_fee_sol < 0 or p_paper_network_fee_sol > 0.01) then
    raise exception 'invalid_paper_network_fee';
  end if;

  if p_mode = 'live' and p_enabled then
    if p_live_confirmation is distinct from 'ENABLE LIVE TRADING' then
      raise exception 'explicit_live_confirmation_required';
    end if;
    if not exists (
      select 1 from sniper.wallets
      where telegram_id = p_telegram_id and status = 'active'
        and custody_mode = 'managed' and vault_secret_id is not null
    ) then raise exception 'active_managed_wallet_required'; end if;
    v_live_armed := true;
  end if;

  insert into sniper.user_settings (
    telegram_id, enabled, mode, live_armed, live_confirmed_at,
    paper_capital_sol, max_trade_sol, take_profit_pct, stop_loss_pct,
    min_price_move_pct, idle_exit_seconds, paper_fee_bps, paper_network_fee_sol
  ) values (
    p_telegram_id, p_enabled, p_mode, v_live_armed,
    case when v_live_armed then pg_catalog.now() else null end,
    coalesce(p_paper_capital_sol,10),
    coalesce(p_max_trade_sol,0.05),
    coalesce(p_take_profit_pct,10),
    coalesce(p_stop_loss_pct,5),
    coalesce(p_min_price_move_pct,0.2),
    coalesce(p_idle_exit_seconds,60),
    coalesce(p_paper_fee_bps,100),
    coalesce(p_paper_network_fee_sol,0.00005)
  )
  on conflict (telegram_id) do update set
    enabled = excluded.enabled,
    mode = excluded.mode,
    live_armed = excluded.live_armed,
    live_confirmed_at = case when excluded.live_armed then pg_catalog.now() else null end,
    paper_capital_sol = coalesce(p_paper_capital_sol, sniper.user_settings.paper_capital_sol),
    max_trade_sol = coalesce(p_max_trade_sol, sniper.user_settings.max_trade_sol),
    take_profit_pct = coalesce(p_take_profit_pct, sniper.user_settings.take_profit_pct),
    stop_loss_pct = coalesce(p_stop_loss_pct, sniper.user_settings.stop_loss_pct),
    min_price_move_pct = coalesce(p_min_price_move_pct, sniper.user_settings.min_price_move_pct),
    idle_exit_seconds = coalesce(p_idle_exit_seconds, sniper.user_settings.idle_exit_seconds),
    paper_fee_bps = coalesce(p_paper_fee_bps, sniper.user_settings.paper_fee_bps),
    paper_network_fee_sol = coalesce(p_paper_network_fee_sol, sniper.user_settings.paper_network_fee_sol),
    updated_at = pg_catalog.now()
  returning * into v_row;

  return pg_catalog.jsonb_build_object(
    'enabled', v_row.enabled, 'mode', v_row.mode, 'liveArmed', v_row.live_armed,
    'paperCapitalSol', v_row.paper_capital_sol, 'maxTradeSol', v_row.max_trade_sol,
    'takeProfitPct', v_row.take_profit_pct, 'stopLossPct', v_row.stop_loss_pct,
    'minPriceMovePct', v_row.min_price_move_pct, 'idleExitSeconds', v_row.idle_exit_seconds,
    'paperFeeBps', v_row.paper_fee_bps, 'paperNetworkFeeSol', v_row.paper_network_fee_sol
  );
end;
$function$;

revoke all on function public.sniper_user_settings_set_v2(bigint,boolean,text,text,numeric,numeric,numeric,numeric,numeric,integer,integer,numeric) from public, anon, authenticated;
grant execute on function public.sniper_user_settings_set_v2(bigint,boolean,text,text,numeric,numeric,numeric,numeric,numeric,integer,integer,numeric) to service_role;
