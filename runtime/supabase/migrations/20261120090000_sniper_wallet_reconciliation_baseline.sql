create table if not exists sniper.wallet_reconciliation_state (
  wallet_id uuid primary key references sniper.wallets(id) on delete cascade,
  telegram_id bigint not null references public.survivor_profiles(telegram_id) on delete cascade,
  baseline_lamports bigint not null check (baseline_lamports >= 0),
  baseline_slot bigint not null check (baseline_slot >= 0),
  baseline_at timestamptz not null default now(),
  coverage_status text not null default 'baseline_pending_history'
    check (coverage_status in ('baseline_pending_history', 'scanning', 'history_complete', 'discrepancy', 'rpc_unavailable')),
  last_scanned_slot bigint,
  reconciled_balance_lamports bigint,
  discrepancy_lamports bigint,
  updated_at timestamptz not null default now()
);

alter table sniper.wallet_reconciliation_state enable row level security;
revoke all on table sniper.wallet_reconciliation_state from public, anon, authenticated;
grant select, insert, update on table sniper.wallet_reconciliation_state to service_role;

create or replace function public.sniper_wallet_reconciliation_baseline(
  p_telegram_id bigint,
  p_wallet_id uuid,
  p_balance_lamports bigint,
  p_slot bigint
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_state sniper.wallet_reconciliation_state%rowtype;
  v_inserted boolean := false;
  v_rows bigint := 0;
begin
  if p_telegram_id is null or p_telegram_id <= 0
     or p_wallet_id is null
     or p_balance_lamports is null or p_balance_lamports < 0
     or p_slot is null or p_slot < 0 then
    raise exception 'invalid_wallet_baseline';
  end if;

  if not exists (
    select 1 from sniper.wallets w
    where w.id = p_wallet_id and w.telegram_id = p_telegram_id
      and w.chain = 'solana' and w.status = 'active'
  ) then
    raise exception 'wallet_owner_mismatch';
  end if;

  insert into sniper.wallet_reconciliation_state (
    wallet_id, telegram_id, baseline_lamports, baseline_slot
  ) values (
    p_wallet_id, p_telegram_id, p_balance_lamports, p_slot
  )
  on conflict (wallet_id) do nothing;
  get diagnostics v_rows = row_count;
  v_inserted := v_rows > 0;

  select * into v_state
  from sniper.wallet_reconciliation_state
  where wallet_id = p_wallet_id and telegram_id = p_telegram_id;

  if not found then
    raise exception 'wallet_baseline_state_unavailable';
  end if;

  return jsonb_build_object(
    'baselineLamports', v_state.baseline_lamports,
    'baselineSol', v_state.baseline_lamports::numeric / 1000000000,
    'baselineSlot', v_state.baseline_slot,
    'baselineAt', v_state.baseline_at,
    'status', v_state.coverage_status,
    'created', v_inserted
  );
end;
$$;

revoke all on function public.sniper_wallet_reconciliation_baseline(bigint, uuid, bigint, bigint)
  from public, anon, authenticated;
grant execute on function public.sniper_wallet_reconciliation_baseline(bigint, uuid, bigint, bigint)
  to service_role;
