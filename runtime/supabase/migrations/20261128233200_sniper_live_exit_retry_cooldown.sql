-- Allow safe retries of definitively failed Live exits without ever
-- rebroadcasting prepared, broadcasting, submitted, or unknown orders.

CREATE OR REPLACE FUNCTION public.sniper_live_exit_retry_allowed(
  p_order_id uuid,
  p_cooldown_seconds integer DEFAULT 30
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO ''
AS $function$
DECLARE
  v_status text;
  v_updated_at timestamptz;
  v_cooldown integer := greatest(5, least(coalesce(p_cooldown_seconds,30),300));
BEGIN
  IF p_order_id IS NULL THEN
    RETURN true;
  END IF;

  SELECT o.status,o.updated_at
    INTO v_status,v_updated_at
    FROM sniper.live_orders o
   WHERE o.id=p_order_id;

  IF NOT FOUND THEN
    RETURN false;
  END IF;

  IF v_status='confirmed' THEN
    RETURN true;
  END IF;

  IF v_status='failed' THEN
    RETURN v_updated_at <= pg_catalog.now()-pg_catalog.make_interval(secs=>v_cooldown);
  END IF;

  -- Unknown and in-flight outcomes are never retried automatically.
  RETURN false;
END;
$function$;

CREATE OR REPLACE FUNCTION public.sniper_live_position_note_exit_attempt(
  p_position_id uuid,
  p_order_id uuid
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO ''
AS $function$
DECLARE
  v_count integer;
BEGIN
  IF p_position_id IS NULL OR p_order_id IS NULL THEN
    RETURN false;
  END IF;

  UPDATE sniper.positions p
     SET last_exit_order_id=o.id,
         updated_at=pg_catalog.now()
    FROM sniper.live_orders o
   WHERE p.id=p_position_id
     AND p.status='open'
     AND o.id=p_order_id
     AND o.telegram_id=p.telegram_id
     AND o.wallet_id=p.wallet_id
     AND o.mint=p.mint
     AND o.side='sell'
     AND o.status='failed';

  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count=1;
END;
$function$;

REVOKE ALL ON FUNCTION public.sniper_live_exit_retry_allowed(uuid,integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_exit_retry_allowed(uuid,integer) TO service_role;
REVOKE ALL ON FUNCTION public.sniper_live_position_note_exit_attempt(uuid,uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_live_position_note_exit_attempt(uuid,uuid) TO service_role;
