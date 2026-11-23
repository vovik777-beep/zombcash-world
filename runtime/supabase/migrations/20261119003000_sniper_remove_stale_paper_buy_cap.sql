-- The browser UI and API permit editable paper sizes up to the virtual-capital
-- limit. Remove the stale 0.05 SOL cap left inside the database RPC.
DO $migration$
DECLARE
  v_oid oid;
  v_before text;
  v_after text;
BEGIN
  SELECT p.oid
    INTO v_oid
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'public'
     AND p.proname = 'sniper_paper_save'
     AND pg_get_function_identity_arguments(p.oid) = 'p_telegram_id bigint, p_position jsonb'
   LIMIT 1;

  IF v_oid IS NULL THEN
    RAISE EXCEPTION 'public.sniper_paper_save(bigint,jsonb) not found';
  END IF;

  v_before := pg_get_functiondef(v_oid);
  v_after := replace(v_before, 'v_buy <= 0 or v_buy > 0.05 or ', 'v_buy <= 0 or ');
  IF v_after = v_before THEN
    IF position('v_buy > 0.05' in v_before) = 0 THEN
      RAISE NOTICE 'Legacy 0.05 SOL cap is already absent; no change needed';
      RETURN;
    END IF;
    RAISE EXCEPTION 'Expected legacy paper buy cap expression not found; refusing unsafe rewrite';
  END IF;

  EXECUTE v_after;
END
$migration$;
