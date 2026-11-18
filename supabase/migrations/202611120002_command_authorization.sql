create table if not exists public.command_permissions (
    command text primary key,
    permission text not null,
    store_scoped boolean not null default true,
    label text not null default ''
);

insert into public.command_permissions(command, permission, store_scoped, label)
values
    ('stores.view', 'stores_view', false, '查看店铺'),
    ('stores.edit', 'stores_edit', true, '编辑店铺'),
    ('stores.delete', 'stores_delete', true, '删除店铺'),
    ('stores.login', 'stores_login', true, '扫码登录'),
    ('stores.export', 'stores_export', true, '导入导出店铺密钥'),
    ('products.view', 'products_view', true, '查看商品'),
    ('products.edit', 'products_edit', true, '编辑商品'),
    ('products.upload', 'products_upload', true, '上传商品'),
    ('products.delete', 'products_edit', true, '删除商品'),
    ('products.listing', 'products_edit', true, '上下架商品'),
    ('products.stock', 'products_edit', true, '修改库存'),
    ('orders.view', 'orders_view', true, '查看订单'),
    ('orders.edit', 'orders_edit', true, '处理订单'),
    ('orders.after_sales', 'after_sales', true, '处理售后'),
    ('procurement.manage', 'procurement', false, '采购管理'),
    ('activities.manage', 'activities', true, '活动与优惠券'),
    ('finance.view', 'orders_view', true, '查看资金'),
    ('team.manage', 'team_manage', false, '管理团队'),
    ('automation.manage', 'products_edit', true, '管理自动化')
on conflict (command) do update set
    permission = excluded.permission,
    store_scoped = excluded.store_scoped,
    label = excluded.label;

create table if not exists public.organization_command_requests (
    id uuid primary key,
    organization_id uuid not null references public.organizations(id) on delete cascade,
    actor_user_id uuid not null references public.profiles(id) on delete cascade,
    command text not null references public.command_permissions(command),
    store_app_id text not null default '',
    status text not null default 'authorized'
        check (status in ('authorized', 'success', 'failed')),
    result jsonb not null default '{}'::jsonb,
    error text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_command_requests_org_created
    on public.organization_command_requests(organization_id, created_at desc);
create index if not exists idx_command_requests_actor_created
    on public.organization_command_requests(actor_user_id, created_at desc);

alter table public.command_permissions enable row level security;
alter table public.organization_command_requests enable row level security;
revoke all on public.command_permissions from anon, authenticated;
revoke all on public.organization_command_requests from anon, authenticated;
grant all on public.command_permissions,
    public.organization_command_requests
to service_role;

create or replace function public.authorize_organization_command(
    p_request_id uuid,
    p_command text,
    p_store_app_id text default ''
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_user_id uuid := auth.uid();
    v_member public.organization_members;
    v_definition public.command_permissions;
    v_allowed boolean;
    v_existing public.organization_command_requests;
begin
    if v_user_id is null then
        raise exception '登录状态无效';
    end if;
    if p_request_id is null then
        raise exception '命令请求编号不能为空';
    end if;
    select * into v_member
      from public.organization_members
     where user_id = v_user_id
       and is_active
     limit 1;
    if not found then
        raise exception '当前账户没有组织权限';
    end if;
    select * into v_definition
      from public.command_permissions
     where command = p_command;
    if not found then
        raise exception '未知业务命令：%', p_command;
    end if;
    if v_definition.store_scoped
       and trim(coalesce(p_store_app_id, '')) = '' then
        raise exception '该业务命令必须指定店铺';
    end if;
    v_allowed := v_member.role in ('owner', 'admin')
        or coalesce(
            (v_member.permissions ->> v_definition.permission)::boolean,
            false
        );
    if v_definition.store_scoped then
        if v_member.role not in ('owner', 'admin') then
            v_allowed := v_allowed and exists (
                select 1
                  from public.organization_store_grants g
                 where g.member_id = v_member.id
                   and g.app_id = trim(p_store_app_id)
            );
        end if;
    end if;
    select * into v_existing
      from public.organization_command_requests
     where id = p_request_id;
    if found then
        if v_existing.organization_id <> v_member.organization_id
           or v_existing.actor_user_id <> v_user_id then
            raise exception '命令请求编号已被其他账户使用';
        end if;
        return jsonb_build_object(
            'request_id', v_existing.id,
            'authorized', true,
            'status', v_existing.status,
            'command', v_existing.command,
            'store_app_id', v_existing.store_app_id
        );
    end if;
    if not v_allowed then
        raise exception '没有执行该操作的权限';
    end if;
    insert into public.organization_command_requests(
        id, organization_id, actor_user_id, command, store_app_id
    )
    values (
        p_request_id,
        v_member.organization_id,
        v_user_id,
        p_command,
        trim(coalesce(p_store_app_id, ''))
    );
    insert into public.organization_audit_logs(
        organization_id, actor_user_id, action, target, detail
    )
    values (
        v_member.organization_id,
        v_user_id,
        'command_authorized',
        trim(coalesce(p_store_app_id, '')),
        jsonb_build_object('command', p_command, 'request_id', p_request_id)
    );
    return jsonb_build_object(
        'request_id', p_request_id,
        'authorized', true,
        'status', 'authorized',
        'command', p_command,
        'store_app_id', trim(coalesce(p_store_app_id, ''))
    );
end;
$$;

create or replace function public.complete_organization_command(
    p_request_id uuid,
    p_succeeded boolean,
    p_result jsonb default '{}'::jsonb,
    p_error text default ''
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_user_id uuid := auth.uid();
    v_request public.organization_command_requests;
begin
    if v_user_id is null then
        raise exception '登录状态无效';
    end if;
    select * into v_request
      from public.organization_command_requests
     where id = p_request_id
       and actor_user_id = v_user_id
     for update;
    if not found then
        raise exception '命令请求不存在';
    end if;
    if v_request.status <> 'authorized' then
        return jsonb_build_object(
            'request_id', v_request.id,
            'status', v_request.status
        );
    end if;
    update public.organization_command_requests
       set status = case when p_succeeded then 'success' else 'failed' end,
           result = coalesce(p_result, '{}'::jsonb),
           error = left(coalesce(p_error, ''), 2000),
           updated_at = now()
     where id = p_request_id
     returning * into v_request;
    insert into public.organization_audit_logs(
        organization_id, actor_user_id, action, target, detail
    )
    values (
        v_request.organization_id,
        v_user_id,
        case when p_succeeded then 'command_success' else 'command_failed' end,
        v_request.store_app_id,
        jsonb_build_object(
            'command', v_request.command,
            'request_id', v_request.id,
            'error', v_request.error
        )
    );
    return jsonb_build_object(
        'request_id', v_request.id,
        'status', v_request.status
    );
end;
$$;

grant execute on function public.authorize_organization_command(uuid, text, text)
to authenticated, service_role;
grant execute on function public.complete_organization_command(uuid, boolean, jsonb, text)
to authenticated, service_role;
