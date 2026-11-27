-- Read-only list of signed Live orders whose submission outcome is still unknown.
CREATE OR REPLACE FUNCTION public.sniper_live_unknown_orders()
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
   where o.status = 'unknown'
     and o.signature is not null
   order by o.updated_at asc
   limit 5;
$function$;

CREATE INDEX IF NOT EXISTS live_orders_unknown_signature_idx
  ON sniper.live_orders (updated_at)
  WHERE status = 'unknown' AND signature IS NOT NULL;

REVOKE ALL ON FUNCTION public.sniper_live_unknown_orders() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_unknown_orders() TO service_role;
