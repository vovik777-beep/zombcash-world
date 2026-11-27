-- Restore the global launch/trade feed from persisted user opt-in after API restarts.
-- This only checks Paper-mode settings; it never arms live execution.

create or replace function public.sniper_any_paper_monitoring_enabled()
returns boolean
language sql
stable
security definer
set search_path = ''
as $function$
  select exists (
    select 1
    from sniper.user_settings s
    where s.enabled is true
      and s.mode = 'paper'
      and s.live_armed is false
  );
$function$;

revoke all on function public.sniper_any_paper_monitoring_enabled() from public, anon, authenticated;
grant execute on function public.sniper_any_paper_monitoring_enabled() to service_role;
