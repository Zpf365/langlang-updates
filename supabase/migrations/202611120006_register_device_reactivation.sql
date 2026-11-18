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

    -- The unique constraint covers revoked rows too.  Load the existing row
    -- regardless of its state so a revoked device can be reactivated instead
    -- of attempting a duplicate insert.
    select * into v_device
      from public.devices
     where user_id = v_user_id
       and device_hash = p_device_hash
     for update;
    if found and v_device.revoked_at is null then
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

    if v_device.id is not null then
        update public.devices
           set device_name = left(coalesce(p_device_name, ''), 80),
               app_version = left(coalesce(p_app_version, ''), 30),
               bound_at = now(),
               last_seen_at = now(),
               revoked_at = null
         where id = v_device.id
        returning * into v_device;
    else
        insert into public.devices (
            user_id, device_hash, device_name, app_version,
            bound_at, last_seen_at
        )
        values (
            v_user_id,
            p_device_hash,
            left(coalesce(p_device_name, ''), 80),
            left(coalesce(p_app_version, ''), 30),
            now(),
            now()
        )
        returning * into v_device;
    end if;

    if v_penalty_applied then
        insert into public.license_events (
            user_id, device_id, event_type, detail
        )
        values (
            v_user_id,
            v_device.id,
            'device_rebind_penalty',
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

grant execute on function public.register_my_device(text, text, text)
to authenticated, service_role;
