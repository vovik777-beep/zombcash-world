CREATE OR REPLACE FUNCTION public.sniper_open_paper_position_mints()
 RETURNS TABLE(mint text)
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
    SELECT DISTINCT p.mint
    FROM sniper.paper_positions p
    WHERE p.status = 'open'
    ORDER BY p.mint
    LIMIT 500;
$function$;

CREATE OR REPLACE FUNCTION public.sniper_open_live_position_mints()
 RETURNS TABLE(mint text)
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
    SELECT DISTINCT p.mint
    FROM sniper.positions p
    WHERE p.status = 'open'
    ORDER BY p.mint
    LIMIT 500;
$function$;
