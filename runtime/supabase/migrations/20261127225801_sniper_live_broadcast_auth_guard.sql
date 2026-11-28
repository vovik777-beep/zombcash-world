-- Re-check per-user Live authorization at the last safe point before broadcast.
CREATE OR REPLACE FUNCTION public.sniper_live_order_transition(
  p_order_id uuid,
  p_status text,
  p_signature text DEFAULT NULL,
  p_error_code text DEFAULT NULL,
  p_chain_slot bigint DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO ''
AS $function$
declare
  v_order sniper.live_orders%rowtype;
begin
  if p_order_id is null or p_status is null or p_status not in ('broadcasting','submitted','confirmed','failed','unknown') then
    raise exception 'invalid_live_order_transition';
  end if;

  select * into v_order
    from sniper.live_orders
   where id = p_order_id
   for update;
  if not found then raise exception 'live_order_not_found'; end if;

  if p_status = 'broadcasting' then
    perform 1
      from sniper.user_settings settings
     where settings.telegram_id = v_order.telegram_id
       and settings.enabled = true
       and settings.mode = 'live'
       and settings.live_armed = true
       and settings.live_confirmed_at is not null
     for update;
    if not found then raise exception 'live_trading_not_armed'; end if;
  end if;

  if not (
    (v_order.status = 'prepared' and p_status in ('broadcasting','failed','unknown')) or
    (v_order.status = 'broadcasting' and p_status in ('submitted','failed','unknown')) or
    (v_order.status = 'submitted' and p_status in ('confirmed','failed','unknown')) or
    (v_order.status = 'unknown' and p_status in ('confirmed','failed')) or
    (v_order.status = 'unknown' and p_status = 'unknown'
      and v_order.signature is null and p_signature is not null)
  ) then
    raise exception 'live_order_transition_not_allowed';
  end if;

  if p_status in ('submitted','confirmed') and
     coalesce(p_signature, v_order.signature) is null then
    raise exception 'live_order_signature_required';
  end if;

  if v_order.signature is not null and p_signature is not null
     and v_order.signature <> p_signature then
    raise exception 'live_order_signature_immutable';
  end if;

  update sniper.live_orders
     set status = p_status,
         signature = coalesce(signature, p_signature),
         chain_slot = coalesce(p_chain_slot, chain_slot),
         error_code = case when p_status in ('failed','unknown')
                           then nullif(pg_catalog.left(p_error_code,100),'')
                           else null end,
         broadcast_started_at = case when p_status = 'broadcasting'
                           then coalesce(broadcast_started_at, pg_catalog.now())
                           else broadcast_started_at end,
         submitted_at = case when p_status = 'submitted'
                             then coalesce(submitted_at, pg_catalog.now()) else submitted_at end,
         confirmed_at = case when p_status = 'confirmed'
                             then pg_catalog.now() else confirmed_at end,
         updated_at = pg_catalog.now()
   where id = p_order_id
   returning * into v_order;

  return pg_catalog.jsonb_build_object(
    'orderId', v_order.id,
    'status', v_order.status,
    'signature', v_order.signature,
    'chainSlot', v_order.chain_slot,
    'errorCode', v_order.error_code,
    'broadcastStartedAt', v_order.broadcast_started_at,
    'submittedAt', v_order.submitted_at,
    'confirmedAt', v_order.confirmed_at
  );
end;
$function$;

REVOKE ALL ON FUNCTION public.sniper_live_order_transition(uuid,text,text,text,bigint) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_order_transition(uuid,text,text,text,bigint) TO service_role;
