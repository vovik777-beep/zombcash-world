-- Expose confirmed orders whose actual chain fill has not yet been applied.
CREATE OR REPLACE FUNCTION public.sniper_live_unfilled_confirmed_orders()
RETURNS TABLE(
  order_id uuid,
  telegram_id bigint,
  mint text,
  side text,
  signature text,
  chain_slot bigint
)
LANGUAGE sql
SECURITY DEFINER
SET search_path TO ''
AS $function$
  select o.id,o.telegram_id,o.mint,o.side,o.signature,o.chain_slot
    from sniper.live_orders o
   where o.status = 'confirmed'
     and o.signature is not null
     and not exists (
       select 1 from sniper.live_order_fills f where f.order_id = o.id
     )
   order by o.confirmed_at asc
   limit 100;
$function$;

REVOKE ALL ON FUNCTION public.sniper_live_unfilled_confirmed_orders() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_unfilled_confirmed_orders() TO service_role;
