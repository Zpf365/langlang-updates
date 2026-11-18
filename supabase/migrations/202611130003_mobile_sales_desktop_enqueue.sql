create or replace function public.mobile_sales_owner_enqueue_task(
    p_device_id text,
    p_task_id text,
    p_source_url text,
    p_title text default '',
    p_sku_selection_mode text default 'any',
    p_purchase_quantity integer default 1,
    p_price_cap_cents integer default 0,
    p_specified_sku_id text default '',
    p_specified_specs jsonb default '{}'::jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
    v_member public.organization_members;
    v_device public.sales_mobile_devices;
    v_task public.sales_mobile_tasks;
    v_url text := trim(coalesce(p_source_url, ''));
begin
    v_member := public._mobile_member();
    if not (
        v_member.role in ('owner', 'admin', 'manager')
        or public.organization_has_permission('procurement')
    ) then
        raise exception '当前账号没有创建采购任务的权限';
    end if;
    select * into v_device
      from public.sales_mobile_devices
     where organization_id = v_member.organization_id
       and device_id = trim(coalesce(p_device_id, ''))
       and status <> 'disabled'
     limit 1;
    if not found then
        raise exception '目标手机设备不存在或已停用';
    end if;
    if v_url not like 'https://%' then
        raise exception '任务链接必须使用 HTTPS';
    end if;
    if p_purchase_quantity is null or p_purchase_quantity < 1 then
        raise exception '购买数量必须大于 0';
    end if;
    insert into public.sales_mobile_tasks(
        organization_id, owner_id, task_id, assigned_device_id,
        source_url, canonical_url, title, sku_selection_mode,
        specified_sku_id, specified_specs, purchase_quantity,
        price_cap_cents, status, created_by
    )
    values (
        v_member.organization_id,
        v_device.owner_id,
        coalesce(nullif(trim(p_task_id), ''), extensions.gen_random_uuid()::text),
        v_device.device_id,
        v_url, v_url,
        trim(coalesce(p_title, '')),
        coalesce(nullif(trim(p_sku_selection_mode), ''), 'any'),
        trim(coalesce(p_specified_sku_id, '')),
        coalesce(p_specified_specs, '{}'::jsonb),
        p_purchase_quantity,
        greatest(0, coalesce(p_price_cap_cents, 0)),
        'pending',
        auth.uid()
    )
    on conflict (organization_id, task_id) do update set
        assigned_device_id = excluded.assigned_device_id,
        updated_at = now()
    returning * into v_task;
    insert into public.sales_mobile_events(
        organization_id, task_id, device_id, actor_user_id, action, detail
    )
    values (
        v_member.organization_id, v_task.task_id, v_device.device_id,
        auth.uid(), 'desktop_task_enqueued',
        jsonb_build_object(
            'source_url', v_task.canonical_url,
            'quantity', v_task.purchase_quantity
        )
    );
    return to_jsonb(v_task);
end;
$$;

grant execute on function public.mobile_sales_owner_enqueue_task(
    text, text, text, text, text, integer, integer, text, jsonb
) to authenticated;
