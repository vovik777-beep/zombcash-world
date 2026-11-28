create or replace function public.sniper_wallet_reconciliation_state(
  p_telegram_id bigint,
  p_wallet_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_state sniper.wallet_reconciliation_state%rowtype;
begin
  if p_telegram_id is null or p_telegram_id <= 0 or p_wallet_id is null then
    raise exception 'invalid_wallet_reconciliation_lookup';
  end if;

  if not exists (
    select 1 from sniper.wallets w
    where w.id = p_wallet_id and w.telegram_id = p_telegram_id
      and w.chain = 'solana' and w.status = 'active'
  ) then
    raise exception 'wallet_owner_mismatch';
  end if;

  select * into v_state
  from sniper.wallet_reconciliation_state
  where wallet_id = p_wallet_id and telegram_id = p_telegram_id;

  if not found then
    return null;
  end if;

  return jsonb_build_object(
    'baselineLamports', v_state.baseline_lamports,
    'baselineSol', v_state.baseline_lamports::numeric / 1000000000,
    'baselineSlot', v_state.baseline_slot,
    'baselineAt', v_state.baseline_at,
    'status', v_state.coverage_status,
    'lastScannedSlot', v_state.last_scanned_slot,
    'reconciledBalanceLamports', v_state.reconciled_balance_lamports,
    'discrepancyLamports', v_state.discrepancy_lamports,
    'updatedAt', v_state.updated_at
  );
end;
$$;

revoke all on function public.sniper_wallet_reconciliation_state(bigint, uuid)
  from public, anon, authenticated;
grant execute on function public.sniper_wallet_reconciliation_state(bigint, uuid)
  to service_role;
