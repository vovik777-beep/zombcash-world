-- ZombieCash Telegram registration and rewards foundation
-- Run this in Supabase SQL Editor for the new Free project.
-- The API server uses SUPABASE_SERVICE_ROLE_KEY; never expose that key in website JavaScript.

create table if not exists public.survivor_profiles (
  telegram_id bigint primary key,
  username text,
  first_name text not null default 'Survivor',
  last_name text,
  photo_url text,
  preferred_language text not null default 'en'
    check (preferred_language in ('en','de','fr','it','ja','zh-CN','ru','tr','hi')),
  points integer not null default 0 check (points >= 0),
  created_at timestamptz not null default now(),
  last_login_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists survivor_profiles_points_idx
  on public.survivor_profiles (points desc, created_at asc);

create table if not exists public.survivor_activity (
  id bigint generated always as identity primary key,
  telegram_id bigint not null references public.survivor_profiles(telegram_id) on delete cascade,
  event_type text not null,
  points_delta integer not null default 0,
  event_key text,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists survivor_activity_user_date_idx
  on public.survivor_activity (telegram_id, created_at desc);

create unique index if not exists survivor_activity_event_key_unique
  on public.survivor_activity (telegram_id, event_key)
  where event_key is not null;

-- All reads/writes go through the API server with the service-role key.
-- Browser clients receive no direct table access.
alter table public.survivor_profiles enable row level security;
alter table public.survivor_activity enable row level security;

revoke all on public.survivor_profiles from anon, authenticated;
revoke all on public.survivor_activity from anon, authenticated;

grant all privileges on table public.survivor_profiles to service_role;
grant all privileges on table public.survivor_activity to service_role;
grant usage, select on sequence public.survivor_activity_id_seq to service_role;



-- Gallery likes: all access is mediated by the API server after validating
-- the signed Telegram Survivor session. Do not expose this table to browsers.
create table if not exists public.gallery_likes (
  telegram_id bigint not null references public.survivor_profiles(telegram_id) on delete cascade,
  image_id text not null check (image_id ~ '^ZOMBCASH/[0-9]{1,10}:[0-9]{1,3}$'),
  created_at timestamptz not null default now(),
  primary key (telegram_id, image_id)
);

create index if not exists gallery_likes_image_created_idx
  on public.gallery_likes (image_id, created_at desc);

alter table public.gallery_likes enable row level security;
revoke all on table public.gallery_likes from public, anon, authenticated;
grant select, insert, delete on table public.gallery_likes to service_role;

create or replace function public.get_gallery_like_stats(
  p_image_ids text[],
  p_telegram_id bigint default null
)
returns table(image_id text, like_count bigint, liked boolean)
language sql
security invoker
set search_path = public
as $function$
  select requested.image_id,
         count(l.telegram_id)::bigint as like_count,
         coalesce(bool_or(l.telegram_id = p_telegram_id), false) as liked
    from unnest(coalesce(p_image_ids, '{}'::text[])) as requested(image_id)
    left join public.gallery_likes l on l.image_id = requested.image_id
   group by requested.image_id
   order by requested.image_id;
$function$;

revoke all on function public.get_gallery_like_stats(text[], bigint) from public, anon, authenticated;
grant execute on function public.get_gallery_like_stats(text[], bigint) to service_role;

create or replace function public.toggle_gallery_like(
  p_telegram_id bigint,
  p_image_id text,
  p_liked boolean
)
returns table(image_id text, like_count bigint, liked boolean)
language plpgsql
security invoker
set search_path = public
as $function$
begin
  if p_telegram_id is null or p_telegram_id <= 0
     or p_image_id is null
     or p_image_id !~ '^ZOMBCASH/[0-9]{1,10}:[0-9]{1,3}$'
     or p_liked is null then
    raise exception 'Invalid gallery like payload';
  end if;

  if p_liked then
    insert into public.gallery_likes (telegram_id, image_id)
    values (p_telegram_id, p_image_id)
    on conflict do nothing;
  else
    delete from public.gallery_likes as existing_like
     where existing_like.telegram_id = p_telegram_id
       and existing_like.image_id = p_image_id;
  end if;

  -- Start from the requested image so an unlike that removes the final row
  -- still returns a stable result with like_count = 0 and liked = false.
  return query
    select requested.image_id,
           count(l.telegram_id)::bigint,
           coalesce(bool_or(l.telegram_id = p_telegram_id), false)
      from (select p_image_id as image_id) requested
      left join public.gallery_likes l on l.image_id = requested.image_id
     group by requested.image_id;
end;
$function$;

revoke all on function public.toggle_gallery_like(bigint, text, boolean) from public, anon, authenticated;
grant execute on function public.toggle_gallery_like(bigint, text, boolean) to service_role;
