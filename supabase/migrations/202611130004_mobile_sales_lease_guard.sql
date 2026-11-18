create or replace function public.mobile_sales_heartbeat(
    p_device_id text,
    p_device_secret text,
    p_task_id text,
    p_lease_token text,
    p_lease_seconds integer default 120
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
    v_device public.sales_mobile_devices;
    v_task public.sales_mobile_tasks;
    v_expires timestamptz;
begin
    v_device := public._mobile_device(p_device_id, p_device_secret);
    v_expires := now() + make_interval(
        secs => greatest(30, least(600, coalesce(p_lease_seconds, 120)))
    );
    update public.sales_mobile_tasks
       set lease_expires_at = v_expires,
           updated_at = now()
     where organization_id = v_device.organization_id
       and task_id = trim(coalesce(p_task_id, ''))
       and assigned_device_id = v_device.device_id
       and lease_expires_at > now()
       and lease_token_hash = encode(
           extensions.digest(coalesce(p_lease_token, ''), 'sha256'),
           'hex'
       )
    returning * into v_task;
    if not found then
        raise exception '任务租约已过期或凭证不匹配，请重新领取';
    end if;
    update public.sales_mobile_devices
       set last_seen_at = now(), updated_at = now()
     where id = v_device.id;
    return jsonb_build_object(
        'task_id', v_task.task_id,
        'lease_expires_at', v_task.lease_expires_at
    );
end;
$$;

create or replace function public.mobile_sales_update_task(
    p_device_id text,
    p_device_secret text,
    p_task_id text,
    p_lease_token text,
    p_status text,
    p_reason text default '',
    p_result jsonb default '{}'::jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
    v_device public.sales_mobile_devices;
    v_task public.sales_mobile_tasks;
    v_status text := trim(coalesce(p_status, ''));
begin
    v_device := public._mobile_device(p_device_id, p_device_secret);
    if v_status not in (
        'product_identified', 'sku_confirmed', 'checkout_verified',
        'awaiting_user_confirmation',
        'submission_pending_verification', 'unknown', 'failed'
    ) then
        raise exception '手机端不能设置任务状态：%', v_status;
    end if;
    update public.sales_mobile_tasks
       set status = v_status,
           status_reason = left(coalesce(p_reason, ''), 1000),
           result = coalesce(
               public.sales_mobile_tasks.result, '{}'::jsonb
           ) || coalesce(p_result, '{}'::jsonb),
           updated_at = now()
     where organization_id = v_device.organization_id
       and task_id = trim(coalesce(p_task_id, ''))
       and assigned_device_id = v_device.device_id
       and lease_expires_at > now()
       and lease_token_hash = encode(
           extensions.digest(coalesce(p_lease_token, ''), 'sha256'),
           'hex'
       )
    returning * into v_task;
    if not found then
        raise exception '任务租约已过期或凭证不匹配，请重新领取';
    end if;
    insert into public.sales_mobile_events(
        organization_id, task_id, device_id, actor_user_id, action, detail
    )
    values (
        v_device.organization_id, v_task.task_id, v_device.device_id,
        auth.uid(), 'task_updated',
        jsonb_build_object(
            'status', v_status,
            'reason', p_reason,
            'result', coalesce(p_result, '{}'::jsonb)
        )
    );
    return to_jsonb(v_task);
end;
$$;

grant execute on function public.mobile_sales_heartbeat(
    text, text, text, text, integer
) to authenticated;
grant execute on function public.mobile_sales_update_task(
    text, text, text, text, text, text, jsonb
) to authenticated;
