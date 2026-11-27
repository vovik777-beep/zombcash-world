-- Expose only distinct open Paper-position mints to the service-role price worker.
CREATE OR REPLACE FUNCTION public.sniper_open_paper_position_mints()
RETURNS TABLE(mint text)
LANGUAGE sql
SECURITY DEFINER
SET search_path = ''
AS $function$
    SELECT DISTINCT p.mint
    FROM sniper.paper_positions p
    WHERE p.status = 'open'
    ORDER BY p.mint
    LIMIT 20;
$function$;

REVOKE ALL ON FUNCTION public.sniper_open_paper_position_mints() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.sniper_open_paper_position_mints() TO service_role;
