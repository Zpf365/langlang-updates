create extension if not exists pgcrypto with schema extensions;

create table if not exists public.sales_mobile_devices (
    id uuid primary key default extensions.gen_random_uuid(),
    owner_id uuid not null references public.profiles(id) on delete cascade,
    organization_id uuid not null references public.organizations(id) on delete cascade,
    device_id text not null,
    secret_hash text not null,
    display_name text not null default '',
    status text not null default 'online'
        check (status in ('online', 'busy', 'offline', 'disabled')),
    app_version text not null default '',
    capabilities jsonb not null default '{}'::jsonb,
    last_seen_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (owner_id, device_id)
);

create table if not exists public.sales_mobile_tasks (
    id uuid primary key default extensions.gen_random_uuid(),
    organization_id uuid not null references public.organizations(id) on delete cascade,
    owner_id uuid not null references public.profiles(id) on delete cascade,
    task_id text not null,
    assigned_device_id text,
    source_url text not null,
    canonical_url text not null,
    title text not null default '',
    sku_selection_mode text not null default 'any',
    specified_sku_id text not null default '',
    specified_specs jsonb not null default '{}'::jsonb,
    purchase_quantity integer not null default 1
        check (purchase_quantity > 0),
    price_cap_cents integer not null default 0
        check (price_cap_cents >= 0),
    status text not null default 'pending',
    status_reason text not null default '',
    lease_token_hash text,
    lease_expires_at timestamptz,
    result jsonb not null default '{}'::jsonb,
    created_by uuid references public.profiles(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (organization_id, task_id)
);

create table if not exists public.sales_mobile_events (
    id bigint generated always as identity primary key,
    organization_id uuid not null references public.organizations(id) on delete cascade,
    task_id text not null default '',
    device_id text not null default '',
    actor_user_id uuid references public.profiles(id) on delete set null,
    action text not null,
    detail jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_sales_mobile_devices_org
    on public.sales_mobile_devices(organization_id, last_seen_at desc);
create index if not exists idx_sales_mobile_tasks_device
    on public.sales_mobile_tasks(organization_id, assigned_device_id, updated_at desc);
create index if not exists idx_sales_mobile_events_task
    on public.sales_mobile_events(organization_id, task_id, created_at desc);

alter table public.sales_mobile_devices enable row level security;
alter table public.sales_mobile_tasks enable row level security;
alter table public.sales_mobile_events enable row level security;

revoke all on public.sales_mobile_devices from anon, authenticated;
revoke all on public.sales_mobile_tasks from anon, authenticated;
revoke all on public.sales_mobile_events from anon, authenticated;

create or replace function public._mobile_member()
returns public.organization_members
language plpgsql
security definer
set search_path = public
as $$
declare
    v_member public.organization_members;
begin
    if auth.uid() is null then
        raise exception '登录状态无效';
    end if;
    perform public.ensure_my_organization();
    select * into v_member
      from public.organization_members
     where user_id = auth.uid()
       and is_active
     limit 1;
    if not found then
        raise exception '当前账户没有组织权限';
    end if;
    return v_member;
end;
$$;

revoke all on function public._mobile_member() from public, anon, authenticated;

create or replace function public._mobile_device(
    p_device_id text,
    p_device_secret text
)
returns public.sales_mobile_devices
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
    v_device public.sales_mobile_devices;
    v_hash text := encode(
        extensions.digest(coalesce(p_device_secret, ''), 'sha256'),
        'hex'
    );
begin
    if auth.uid() is null then
        raise exception '登录状态无效';
    end if;
    select * into v_device
      from public.sales_mobile_devices
     where owner_id = auth.uid()
       and device_id = trim(coalesce(p_device_id, ''))
       and secret_hash = v_hash
       and status <> 'disabled'
     limit 1;
    if not found then
        raise exception '设备认证失败，请重新绑定设备';
    end if;
    update public.sales_mobile_devices
       set status = case when status = 'offline' then 'online' else status end,
           last_seen_at = now(),
           updated_at = now()
     where id = v_device.id;
    return v_device;
end;
$$;

revoke all on function public._mobile_device(text, text)
from public, anon, authenticated;

create or replace function public.mobile_sales_register_device(
    p_device_id text,
    p_device_secret text,
    p_display_name text,
    p_app_version text,
    p_capabilities jsonb default '{}'::jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
    v_member public.organization_members;
    v_device public.sales_mobile_devices;
    v_device_id text := nullif(trim(coalesce(p_device_id, '')), '');
begin
    if v_device_id is null or length(coalesce(p_device_secret, '')) < 24 then
        raise exception '设备编号或认证密钥无效';
    end if;
    v_member := public._mobile_member();
    insert into public.sales_mobile_devices(
        owner_id, organization_id, device_id, secret_hash,
        display_name, status, app_version, capabilities,
        last_seen_at, created_at, updated_at
    )
    values (
        auth.uid(), v_member.organization_id, v_device_id,
        encode(extensions.digest(p_device_secret, 'sha256'), 'hex'),
        trim(coalesce(p_display_name, '')),
        'online',
        trim(coalesce(p_app_version, '')),
        coalesce(p_capabilities, '{}'::jsonb),
        now(), now(), now()
    )
    on conflict (owner_id, device_id) do update set
        organization_id = excluded.organization_id,
        secret_hash = excluded.secret_hash,
        display_name = excluded.display_name,
        status = 'online',
        app_version = excluded.app_version,
        capabilities = excluded.capabilities,
        last_seen_at = excluded.last_seen_at,
        updated_at = excluded.updated_at
    returning * into v_device;

    insert into public.sales_mobile_events(
        organization_id, device_id, actor_user_id, action, detail
    )
    values (
        v_member.organization_id, v_device_id, auth.uid(),
        'device_registered',
        jsonb_build_object(
            'display_name', v_device.display_name,
            'app_version', v_device.app_version
        )
    );
    return jsonb_build_object(
        'device_id', v_device.device_id,
        'display_name', v_device.display_name,
        'status', v_device.status,
        'organization_id', v_device.organization_id,
        'last_seen_at', v_device.last_seen_at
    );
end;
$$;

create or replace function public.mobile_sales_enqueue_task(
    p_device_id text,
    p_device_secret text,
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
set search_path = public
as $$
declare
    v_device public.sales_mobile_devices;
    v_task public.sales_mobile_tasks;
    v_url text := trim(coalesce(p_source_url, ''));
begin
    v_device := public._mobile_device(p_device_id, p_device_secret);
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
        v_device.organization_id, auth.uid(),
        coalesce(nullif(trim(p_task_id), ''), extensions.gen_random_uuid()::text),
        v_device.device_id, v_url, v_url,
        trim(coalesce(p_title, '')),
        coalesce(nullif(trim(p_sku_selection_mode), ''), 'any'),
        trim(coalesce(p_specified_sku_id, '')),
        coalesce(p_specified_specs, '{}'::jsonb),
        p_purchase_quantity,
        greatest(0, coalesce(p_price_cap_cents, 0)),
        'pending', auth.uid()
    )
    on conflict (organization_id, task_id) do update set
        assigned_device_id = excluded.assigned_device_id,
        updated_at = now()
    returning * into v_task;
    insert into public.sales_mobile_events(
        organization_id, task_id, device_id, actor_user_id, action, detail
    )
    values (
        v_device.organization_id, v_task.task_id, v_device.device_id,
        auth.uid(), 'task_enqueued',
        jsonb_build_object(
            'source_url', v_task.canonical_url,
            'quantity', v_task.purchase_quantity
        )
    );
    return to_jsonb(v_task);
end;
$$;

create or replace function public.mobile_sales_list_tasks(
    p_device_id text,
    p_device_secret text
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
    v_device public.sales_mobile_devices;
begin
    v_device := public._mobile_device(p_device_id, p_device_secret);
    return coalesce((
        select jsonb_agg(to_jsonb(t) order by t.updated_at desc)
          from public.sales_mobile_tasks t
         where t.organization_id = v_device.organization_id
           and (
               t.assigned_device_id is null
               or t.assigned_device_id = v_device.device_id
           )
    ), '[]'::jsonb);
end;
$$;

create or replace function public.mobile_sales_claim_task(
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
    if length(coalesce(p_lease_token, '')) < 24 then
        raise exception '任务租约凭证无效';
    end if;
    select * into v_task
      from public.sales_mobile_tasks
     where organization_id = v_device.organization_id
       and task_id = trim(coalesce(p_task_id, ''))
     for update;
    if not found then
        raise exception '任务不存在';
    end if;
    if v_task.status in ('completed', 'closed', 'failed') then
        raise exception '任务已结束，不能领取';
    end if;
    if v_task.assigned_device_id is not null
       and v_task.assigned_device_id <> v_device.device_id
       and coalesce(v_task.lease_expires_at, now() - interval '1 second') > now()
    then
        raise exception '任务正由其他设备执行';
    end if;
    v_expires := now() + make_interval(
        secs => greatest(30, least(600, coalesce(p_lease_seconds, 120)))
    );
    update public.sales_mobile_tasks
       set assigned_device_id = v_device.device_id,
           lease_token_hash = encode(
               extensions.digest(p_lease_token, 'sha256'), 'hex'
           ),
           lease_expires_at = v_expires,
           updated_at = now()
     where id = v_task.id
    returning * into v_task;
    update public.sales_mobile_devices
       set status = 'busy', last_seen_at = now(), updated_at = now()
     where id = v_device.id;
    insert into public.sales_mobile_events(
        organization_id, task_id, device_id, actor_user_id, action, detail
    )
    values (
        v_device.organization_id, v_task.task_id, v_device.device_id,
        auth.uid(), 'task_claimed',
        jsonb_build_object('lease_expires_at', v_expires)
    );
    return to_jsonb(v_task);
end;
$$;

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
       and lease_token_hash = encode(
           extensions.digest(coalesce(p_lease_token, ''), 'sha256'),
           'hex'
       )
    returning * into v_task;
    if not found then
        raise exception '任务租约不存在或凭证不匹配';
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
       and lease_token_hash = encode(
           extensions.digest(coalesce(p_lease_token, ''), 'sha256'),
           'hex'
       )
    returning * into v_task;
    if not found then
        raise exception '任务租约不存在或凭证不匹配';
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

create or replace function public.mobile_sales_list_devices()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
    v_member public.organization_members;
begin
    v_member := public._mobile_member();
    if not (
        v_member.role in ('owner', 'admin', 'manager')
        or public.organization_has_permission('procurement')
    ) then
        raise exception '当前账号没有查看采购设备的权限';
    end if;
    return coalesce((
        select jsonb_agg(
            jsonb_build_object(
                'device_id', d.device_id,
                'display_name', d.display_name,
                'status', d.status,
                'app_version', d.app_version,
                'capabilities', d.capabilities,
                'last_seen_at', d.last_seen_at
            )
            order by d.last_seen_at desc
        )
          from public.sales_mobile_devices d
         where d.organization_id = v_member.organization_id
    ), '[]'::jsonb);
end;
$$;

grant execute on function public.mobile_sales_register_device(
    text, text, text, text, jsonb
) to authenticated;
grant execute on function public.mobile_sales_enqueue_task(
    text, text, text, text, text, text, integer, integer, text, jsonb
) to authenticated;
grant execute on function public.mobile_sales_list_tasks(text, text)
to authenticated;
grant execute on function public.mobile_sales_claim_task(
    text, text, text, text, integer
) to authenticated;
grant execute on function public.mobile_sales_heartbeat(
    text, text, text, text, integer
) to authenticated;
grant execute on function public.mobile_sales_update_task(
    text, text, text, text, text, text, jsonb
) to authenticated;
grant execute on function public.mobile_sales_list_devices()
to authenticated;
