create extension if not exists citext with schema extensions;
create extension if not exists pgcrypto with schema extensions;

create table if not exists public.profiles (
    id uuid primary key references auth.users(id) on delete restrict,
    email extensions.citext not null unique,
    username extensions.citext not null unique,
    role text not null default 'user' check (role in ('user', 'admin')),
    is_active boolean not null default true,
    trial_started_at timestamptz,
    license_expires_at timestamptz,
    permanent_license boolean not null default false,
    max_devices integer not null default 1 check (max_devices >= 1),
    rebind_penalty_hours integer not null default 24
        check (rebind_penalty_hours >= 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.registration_reservations (
    email extensions.citext primary key,
    username extensions.citext not null unique,
    expires_at timestamptz not null,
    last_sent_at timestamptz not null default now(),
    created_at timestamptz not null default now()
);

create table if not exists public.devices (
    id uuid primary key default extensions.gen_random_uuid(),
    user_id uuid not null references public.profiles(id) on delete cascade,
    device_hash text not null,
    device_name text not null default '',
    app_version text not null default '',
    bound_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    revoked_at timestamptz,
    unique (user_id, device_hash)
);

create table if not exists public.license_cards (
    id uuid primary key default extensions.gen_random_uuid(),
    code_hash text not null unique,
    code_hint text not null default '',
    card_type text not null check (card_type in ('temporary', 'permanent')),
    duration_days integer,
    max_devices integer not null default 1 check (max_devices >= 1),
    rebind_penalty_hours integer not null default 24
        check (rebind_penalty_hours >= 0),
    status text not null default 'unused'
        check (status in ('unused', 'redeemed', 'revoked', 'expired')),
    batch_id text not null default '',
    notes text not null default '',
    available_until timestamptz,
    redeemed_by uuid references public.profiles(id) on delete restrict,
    redeemed_at timestamptz,
    created_at timestamptz not null default now(),
    check (
        (card_type = 'permanent' and duration_days is null)
        or (card_type = 'temporary' and duration_days > 0)
    )
);

create table if not exists public.license_events (
    id bigint generated always as identity primary key,
    user_id uuid not null references public.profiles(id) on delete restrict,
    device_id uuid references public.devices(id) on delete set null,
    event_type text not null,
    card_id uuid references public.license_cards(id) on delete set null,
    days_delta integer not null default 0,
    permanent_delta boolean not null default false,
    detail jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_devices_user_seen
    on public.devices(user_id, last_seen_at desc);
create index if not exists idx_license_cards_status
    on public.license_cards(status, created_at desc);
create index if not exists idx_license_events_user
    on public.license_events(user_id, created_at desc);

create or replace function public.touch_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists profiles_touch_updated_at on public.profiles;
create trigger profiles_touch_updated_at
before update on public.profiles
for each row execute function public.touch_updated_at();

create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select exists (
        select 1
        from public.profiles
        where id = auth.uid()
          and role = 'admin'
          and is_active = true
    );
$$;

alter table public.profiles enable row level security;
alter table public.registration_reservations enable row level security;
alter table public.devices enable row level security;
alter table public.license_cards enable row level security;
alter table public.license_events enable row level security;

drop policy if exists profiles_select_own on public.profiles;
create policy profiles_select_own
on public.profiles for select
to authenticated
using (id = auth.uid() or public.is_admin());

drop policy if exists devices_select_own on public.devices;
create policy devices_select_own
on public.devices for select
to authenticated
using (user_id = auth.uid() or public.is_admin());

drop policy if exists license_events_select_own on public.license_events;
create policy license_events_select_own
on public.license_events for select
to authenticated
using (user_id = auth.uid() or public.is_admin());

revoke all on public.registration_reservations from anon, authenticated;
revoke all on public.license_cards from anon, authenticated;
revoke insert, update, delete on public.profiles from anon, authenticated;
revoke insert, update, delete on public.devices from anon, authenticated;
revoke insert, update, delete on public.license_events from anon, authenticated;
grant select on public.profiles, public.devices, public.license_events
to authenticated;
grant execute on function public.is_admin() to authenticated;

create or replace function public.complete_registration(
    p_user_id uuid,
    p_email extensions.citext,
    p_username extensions.citext
)
returns public.profiles
language plpgsql
security definer
set search_path = public
as $$
declare
    reservation public.registration_reservations;
    profile public.profiles;
begin
    select *
      into reservation
      from public.registration_reservations
     where email = p_email
       and username = p_username
       and expires_at > now()
     for update;

    if not found then
        raise exception '注册信息已过期，请重新发送验证码';
    end if;

    insert into public.profiles (
        id, email, username, trial_started_at, license_expires_at
    )
    values (
        p_user_id, p_email, p_username, now(), now() + interval '3 days'
    )
    on conflict (id) do update
       set email = excluded.email,
           username = excluded.username,
           trial_started_at = coalesce(
               public.profiles.trial_started_at,
               excluded.trial_started_at
           ),
           license_expires_at = case
               when public.profiles.license_expires_at is null
                   then excluded.license_expires_at
               else public.profiles.license_expires_at
           end
    returning * into profile;

    delete from public.registration_reservations where email = p_email;
    return profile;
exception
    when unique_violation then
        raise exception '账户名或邮箱已存在';
end;
$$;

revoke all on function public.complete_registration(uuid, extensions.citext, extensions.citext)
from public, anon, authenticated;
grant execute on function public.complete_registration(uuid, extensions.citext, extensions.citext)
to service_role;

create or replace function public.redeem_license_card(
    p_user_id uuid,
    p_code_hash text,
    p_device_hash text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    card public.license_cards;
    profile public.profiles;
    device public.devices;
    base_time timestamptz;
    new_expiry timestamptz;
begin
    select * into profile
      from public.profiles
     where id = p_user_id
     for update;
    if not found then
        raise exception '账户不存在';
    end if;
    if profile.is_active = false then
        raise exception '账户已被停用';
    end if;

    select * into card
      from public.license_cards
     where code_hash = p_code_hash
     for update;
    if not found then
        raise exception '卡密不存在';
    end if;
    if card.status <> 'unused' then
        raise exception '卡密已被使用或作废';
    end if;
    if card.available_until is not null and card.available_until < now() then
        update public.license_cards
           set status = 'expired'
         where id = card.id;
        raise exception '卡密已过期';
    end if;

    select * into device
      from public.devices
     where user_id = p_user_id
       and device_hash = p_device_hash
       and revoked_at is null;

    if card.card_type = 'permanent' then
        update public.profiles
           set permanent_license = true,
               max_devices = greatest(profile.max_devices, card.max_devices),
               rebind_penalty_hours = card.rebind_penalty_hours
         where id = p_user_id
        returning * into profile;
        new_expiry := null;
    else
        base_time := greatest(coalesce(profile.license_expires_at, now()), now());
        new_expiry := base_time + make_interval(days => card.duration_days);
        update public.profiles
           set license_expires_at = new_expiry,
               max_devices = greatest(profile.max_devices, card.max_devices),
               rebind_penalty_hours = card.rebind_penalty_hours
         where id = p_user_id
        returning * into profile;
    end if;

    update public.license_cards
       set status = 'redeemed',
           redeemed_by = p_user_id,
           redeemed_at = now()
     where id = card.id;

    insert into public.license_events (
        user_id, device_id, event_type, card_id, days_delta,
        permanent_delta, detail
    )
    values (
        p_user_id,
        device.id,
        'card_redeemed',
        card.id,
        coalesce(card.duration_days, 0),
        card.card_type = 'permanent',
        jsonb_build_object('card_hint', card.code_hint)
    );

    return jsonb_build_object(
        'active', profile.permanent_license
            or coalesce(profile.license_expires_at > now(), false),
        'permanent', profile.permanent_license,
        'expires_at', profile.license_expires_at,
        'status_text', case
            when profile.permanent_license then '永久授权'
            when profile.license_expires_at > now() then '授权有效'
            else '授权已到期'
        end,
        'remaining_days', case
            when profile.permanent_license then 999999
            else greatest(
                0,
                ceil(extract(epoch from (profile.license_expires_at - now())) / 86400.0)::integer
            )
        end,
        'max_devices', profile.max_devices,
        'device_bound', device.id is not null
    );
end;
$$;

revoke all on function public.redeem_license_card(uuid, text, text)
from public, anon, authenticated;
grant execute on function public.redeem_license_card(uuid, text, text)
to service_role;
