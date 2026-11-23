create table if not exists sniper.wallet_transactions (
  id uuid primary key default gen_random_uuid(),
  telegram_id bigint not null references public.survivor_profiles(telegram_id) on delete cascade,
  wallet_id uuid not null references sniper.wallets(id) on delete cascade,
  signature text not null,
  slot bigint,
  block_time bigint,
  net_lamports bigint,
  fee_lamports bigint,
  direction text not null default 'unknown'
    check (direction in ('incoming', 'outgoing', 'neutral', 'unknown')),
  accounting_status text not null default 'unavailable'
    check (accounting_status in ('confirmed', 'failed', 'unavailable')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint wallet_transactions_wallet_signature_key unique (wallet_id, signature),
  constraint wallet_transactions_signature_length check (length(signature) between 32 and 100),
  constraint wallet_transactions_fee_nonnegative check (fee_lamports is null or fee_lamports >= 0)
);

create index if not exists wallet_transactions_user_time_idx
  on sniper.wallet_transactions (telegram_id, block_time desc nulls last);

alter table sniper.wallet_transactions enable row level security;
revoke all on table sniper.wallet_transactions from public, anon, authenticated;
grant select, insert, update on table sniper.wallet_transactions to service_role;

create or replace function public.sniper_wallet_transaction_upsert(
  p_telegram_id bigint,
  p_wallet_id uuid,
  p_signature text,
  p_slot bigint,
  p_block_time bigint,
  p_net_lamports bigint,
  p_fee_lamports bigint,
  p_direction text,
  p_accounting_status text
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_telegram_id is null or p_telegram_id <= 0
     or p_wallet_id is null
     or p_signature is null or length(p_signature) < 32 or length(p_signature) > 100
     or p_direction not in ('incoming', 'outgoing', 'neutral', 'unknown')
     or p_accounting_status not in ('confirmed', 'failed', 'unavailable')
     or (p_fee_lamports is not null and p_fee_lamports < 0) then
    raise exception 'invalid_wallet_transaction';
  end if;

  if not exists (
    select 1 from sniper.wallets w
    where w.id = p_wallet_id and w.telegram_id = p_telegram_id
      and w.chain = 'solana' and w.status = 'active'
  ) then
    raise exception 'wallet_owner_mismatch';
  end if;

  insert into sniper.wallet_transactions (
    telegram_id, wallet_id, signature, slot, block_time,
    net_lamports, fee_lamports, direction, accounting_status, updated_at
  ) values (
    p_telegram_id, p_wallet_id, p_signature, p_slot, p_block_time,
    p_net_lamports, p_fee_lamports, p_direction, p_accounting_status, now()
  )
  on conflict (wallet_id, signature) do update set
    slot = excluded.slot,
    block_time = excluded.block_time,
    net_lamports = excluded.net_lamports,
    fee_lamports = excluded.fee_lamports,
    direction = excluded.direction,
    accounting_status = excluded.accounting_status,
    updated_at = now()
  where sniper.wallet_transactions.telegram_id = excluded.telegram_id;
end;
$$;

revoke all on function public.sniper_wallet_transaction_upsert(bigint, uuid, text, bigint, bigint, bigint, bigint, text, text)
  from public, anon, authenticated;
grant execute on function public.sniper_wallet_transaction_upsert(bigint, uuid, text, bigint, bigint, bigint, bigint, text, text)
  to service_role;
