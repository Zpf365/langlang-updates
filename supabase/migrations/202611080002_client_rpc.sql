create table if not exists public.app_secrets (
    secret_key text primary key,
    secret_value text not null,
    updated_at timestamptz not null default now()
);

alter table public.app_secrets enable row level security;
revoke all on public.app_secrets from anon, authenticated;

create or replace function public.check_username_available(
    p_username text
)
returns boolean
language sql
stable
security definer
set search_path = public, extensions
as $$
    select not exists (
        select 1
        from public.profiles
        where username = trim(p_username)::extensions.citext
    );
$$;

create or replace function public.lookup_login_email(
    p_username text
)
returns text
language sql
stable
security definer
set search_path = public
as $$
    select email::text
    from public.profiles
    where username = trim(p_username)::extensions.citext
      and is_active = true
    limit 1;
$$;

create or replace function public.complete_registration(
    p_username text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_user_id uuid := auth.uid();
    v_email text;
    v_profile public.profiles;
begin
    if v_user_id is null then
        raise exception '登录状态无效';
    end if;
    if length(trim(p_username)) < 2 then
        raise exception '账户名至少需要 2 个字符';
    end if;
    select email into v_email
      from auth.users
     where id = v_user_id;
    if v_email is null then
        raise exception '邮箱账户不存在';
    end if;

    insert into public.profiles (
        id, email, username, trial_started_at, license_expires_at
    )
    values (
        v_user_id,
        v_email::extensions.citext,
        trim(p_username)::extensions.citext,
        now(),
        now() + interval '3 days'
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
    returning * into v_profile;

    return jsonb_build_object(
        'username', v_profile.username,
        'email', v_profile.email,
        'active', true,
        'permanent', v_profile.permanent_license,
        'expires_at', v_profile.license_expires_at,
        'status_text', '3 天体验已激活',
        'remaining_days', 3,
        'max_devices', v_profile.max_devices,
        'device_bound', false,
        'server_time', now()
    );
exception
    when unique_violation then
        raise exception '账户名或邮箱已存在';
end;
$$;

create or replace function public.register_my_device(
    p_device_hash text,
    p_device_name text default '',
    p_app_version text default ''
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_user_id uuid := auth.uid();
    v_profile public.profiles;
    v_device public.devices;
    v_active_count integer;
    v_penalty_applied boolean := false;
begin
    if v_user_id is null then
        raise exception '登录状态无效';
    end if;
    if p_device_hash !~ '^[0-9a-f]{64}$' then
        raise exception '设备标识无效';
    end if;

    select * into v_profile
      from public.profiles
     where id = v_user_id
     for update;
    if not found then
        raise exception '请先完成账户资料';
    end if;
    if v_profile.is_active = false then
        raise exception '账户已被停用';
    end if;

    select * into v_device
      from public.devices
     where user_id = v_user_id
       and device_hash = p_device_hash
       and revoked_at is null;
    if found then
        update public.devices
           set device_name = left(coalesce(p_device_name, ''), 80),
               app_version = left(coalesce(p_app_version, ''), 30),
               last_seen_at = now()
         where id = v_device.id;
        return jsonb_build_object(
            'ok', true,
            'device_id', v_device.id,
            'rebind_penalty_applied', false
        );
    end if;

    select count(*) into v_active_count
      from public.devices
     where user_id = v_user_id
       and revoked_at is null;

    if v_active_count >= v_profile.max_devices then
        update public.devices
           set revoked_at = now()
         where user_id = v_user_id
           and revoked_at is null;
        if not v_profile.permanent_license
           and v_profile.license_expires_at is not null then
            update public.profiles
               set license_expires_at = greatest(
                   now(),
                   license_expires_at
                   - make_interval(hours => v_profile.rebind_penalty_hours)
               )
             where id = v_user_id;
            v_penalty_applied := v_profile.rebind_penalty_hours > 0;
        end if;
    end if;

    insert into public.devices (
        user_id, device_hash, device_name, app_version,
        bound_at, last_seen_at
    )
    values (
        v_user_id, p_device_hash, left(coalesce(p_device_name, ''), 80),
        left(coalesce(p_app_version, ''), 30), now(), now()
    )
    returning * into v_device;

    if v_penalty_applied then
        insert into public.license_events (
            user_id, device_id, event_type, detail
        )
        values (
            v_user_id, v_device.id, 'device_rebind_penalty',
            jsonb_build_object(
                'penalty_hours', v_profile.rebind_penalty_hours
            )
        );
    end if;

    return jsonb_build_object(
        'ok', true,
        'device_id', v_device.id,
        'rebind_penalty_applied', v_penalty_applied
    );
end;
$$;

create or replace function public.my_license_status()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
    v_user_id uuid := auth.uid();
    v_profile public.profiles;
    v_active boolean;
    v_days integer;
begin
    if v_user_id is null then
        raise exception '登录状态无效';
    end if;
    select * into v_profile
      from public.profiles
     where id = v_user_id;
    if not found then
        raise exception '账户资料不存在';
    end if;
    if v_profile.is_active = false then
        raise exception '账户已被停用';
    end if;

    v_active := v_profile.permanent_license or
        coalesce(v_profile.license_expires_at > now(), false);
    v_days := case
        when v_profile.permanent_license then 999999
        when v_profile.license_expires_at is null then 0
        else greatest(
            0,
            ceil(
                extract(epoch from (
                    v_profile.license_expires_at - now()
                )) / 86400.0
            )::integer
        )
    end;
    return jsonb_build_object(
        'active', v_active,
        'permanent', v_profile.permanent_license,
        'expires_at', v_profile.license_expires_at,
        'status_text', case
            when v_profile.permanent_license then '永久授权'
            when v_active then '授权有效'
            else '授权已到期'
        end,
        'remaining_days', v_days,
        'max_devices', v_profile.max_devices,
        'device_bound', exists (
            select 1 from public.devices
            where user_id = v_user_id and revoked_at is null
        ),
        'server_time', now()
    );
end;
$$;

create or replace function public.redeem_my_license_card(
    p_code text,
    p_device_hash text
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
    v_user_id uuid := auth.uid();
    v_pepper text;
    v_hash text;
    v_card public.license_cards;
    v_profile public.profiles;
    v_device public.devices;
begin
    if v_user_id is null then
        raise exception '登录状态无效';
    end if;
    select secret_value into v_pepper
      from public.app_secrets
     where secret_key = 'card_hash_pepper';
    if v_pepper is null then
        raise exception '卡密服务未配置';
    end if;
    v_hash := encode(
        extensions.hmac(
            convert_to(upper(trim(p_code)), 'UTF8'),
            convert_to(v_pepper, 'UTF8'),
            'sha256'
        ),
        'hex'
    );

    select * into v_profile
      from public.profiles
     where id = v_user_id
     for update;
    if not found then
        raise exception '账户资料不存在';
    end if;
    select * into v_card
      from public.license_cards
     where code_hash = v_hash
     for update;
    if not found then
        raise exception '卡密不存在';
    end if;
    if v_card.status <> 'unused' then
        raise exception '卡密已被使用或作废';
    end if;
    if v_card.available_until is not null
       and v_card.available_until < now() then
        update public.license_cards
           set status = 'expired'
         where id = v_card.id;
        raise exception '卡密已过期';
    end if;
    select * into v_device
      from public.devices
     where user_id = v_user_id
       and device_hash = p_device_hash
       and revoked_at is null;

    if v_card.card_type = 'permanent' then
        update public.profiles
           set permanent_license = true,
               max_devices = greatest(max_devices, v_card.max_devices),
               rebind_penalty_hours = v_card.rebind_penalty_hours
         where id = v_user_id
        returning * into v_profile;
    else
        update public.profiles
           set license_expires_at = greatest(
                   coalesce(license_expires_at, now()),
                   now()
               ) + make_interval(days => v_card.duration_days),
               max_devices = greatest(max_devices, v_card.max_devices),
               rebind_penalty_hours = v_card.rebind_penalty_hours
         where id = v_user_id
        returning * into v_profile;
    end if;

    update public.license_cards
       set status = 'redeemed',
           redeemed_by = v_user_id,
           redeemed_at = now()
     where id = v_card.id;
    insert into public.license_events (
        user_id, device_id, event_type, card_id,
        days_delta, permanent_delta, detail
    )
    values (
        v_user_id,
        v_device.id,
        'card_redeemed',
        v_card.id,
        coalesce(v_card.duration_days, 0),
        v_card.card_type = 'permanent',
        jsonb_build_object('card_hint', v_card.code_hint)
    );

    return public.my_license_status();
end;
$$;

revoke all on function public.check_username_available(text)
from public;
revoke all on function public.lookup_login_email(text)
from public;
revoke all on function public.complete_registration(text)
from public;
revoke all on function public.register_my_device(text, text, text)
from public;
revoke all on function public.my_license_status()
from public;
revoke all on function public.redeem_my_license_card(text, text)
from public;

grant execute on function public.check_username_available(text)
to anon, authenticated;
grant execute on function public.lookup_login_email(text)
to anon, authenticated;
grant execute on function public.complete_registration(text)
to authenticated;
grant execute on function public.register_my_device(text, text, text)
to authenticated;
grant execute on function public.my_license_status()
to authenticated;
grant execute on function public.redeem_my_license_card(text, text)
to authenticated;
