create table if not exists public.organization_order_workbench (
    organization_id uuid not null references public.organizations(id) on delete cascade,
    store_app_id text not null,
    order_id text not null,
    payload_protected bytea not null,
    revision bigint not null default 1,
    updated_by uuid references public.profiles(id) on delete set null,
    updated_at timestamptz not null default now(),
    primary key (organization_id, store_app_id, order_id)
);

create index if not exists idx_order_workbench_org_updated
    on public.organization_order_workbench(organization_id, updated_at desc);

alter table public.organization_order_workbench enable row level security;
revoke all on public.organization_order_workbench from anon, authenticated;
grant all on public.organization_order_workbench to service_role;

create or replace function public.upsert_organization_order_workbench(
    p_rows jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
    v_user_id uuid := auth.uid();
    v_member public.organization_members;
    v_key text;
    v_row jsonb;
    v_store_app_id text;
    v_order_id text;
    v_allowed boolean;
    v_saved integer := 0;
begin
    if v_user_id is null then
        raise exception '登录状态无效';
    end if;
    select * into v_member
      from public.organization_members
     where user_id = v_user_id
       and is_active
     limit 1;
    if not found then
        raise exception '当前账户没有组织权限';
    end if;
    if v_member.role not in ('owner', 'admin')
       and not coalesce(
           (v_member.permissions ->> 'orders_edit')::boolean,
           false
       ) then
        raise exception '没有修改订单工作台数据的权限';
    end if;
    select secret_value into v_key
      from public.app_secrets
     where secret_key = 'organization_store_vault_key';
    if v_key is null then
        raise exception '组织保险箱密钥未配置';
    end if;

    for v_row in
        select value
          from jsonb_array_elements(coalesce(p_rows, '[]'::jsonb))
    loop
        v_store_app_id := trim(coalesce(v_row ->> 'store_app_id', ''));
        v_order_id := trim(coalesce(v_row ->> 'order_id', ''));
        if v_store_app_id = '' or v_order_id = '' then
            continue;
        end if;
        if v_member.role not in ('owner', 'admin') then
            v_allowed := exists (
                select 1
                  from public.organization_store_grants g
                 where g.member_id = v_member.id
                   and g.app_id = v_store_app_id
            );
            if not v_allowed then
                raise exception '没有该店铺的工作台权限：%', v_store_app_id;
            end if;
        end if;
        insert into public.organization_order_workbench(
            organization_id,
            store_app_id,
            order_id,
            payload_protected,
            revision,
            updated_by,
            updated_at
        )
        values (
            v_member.organization_id,
            v_store_app_id,
            v_order_id,
            extensions.pgp_sym_encrypt(
                coalesce(v_row -> 'payload', '{}'::jsonb)::text,
                v_key
            ),
            1,
            v_user_id,
            now()
        )
        on conflict (organization_id, store_app_id, order_id)
        do update set
            payload_protected = excluded.payload_protected,
            revision = organization_order_workbench.revision + 1,
            updated_by = v_user_id,
            updated_at = now();
        v_saved := v_saved + 1;
    end loop;
    insert into public.organization_audit_logs(
        organization_id, actor_user_id, action, detail
    )
    values (
        v_member.organization_id,
        v_user_id,
        'order_workbench_push',
        jsonb_build_object('rows', v_saved)
    );
    return jsonb_build_object('saved', v_saved);
end;
$$;

create or replace function public.pull_organization_order_workbench(
    p_store_app_ids text[]
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
    v_user_id uuid := auth.uid();
    v_member public.organization_members;
    v_key text;
    v_rows jsonb;
begin
    if v_user_id is null then
        raise exception '登录状态无效';
    end if;
    select * into v_member
      from public.organization_members
     where user_id = v_user_id
       and is_active
     limit 1;
    if not found then
        raise exception '当前账户没有组织权限';
    end if;
    if v_member.role not in ('owner', 'admin')
       and not coalesce(
           (v_member.permissions ->> 'orders_view')::boolean,
           false
       ) then
        raise exception '没有查看订单工作台数据的权限';
    end if;
    select secret_value into v_key
      from public.app_secrets
     where secret_key = 'organization_store_vault_key';
    if v_key is null then
        raise exception '组织保险箱密钥未配置';
    end if;
    select coalesce(
        jsonb_agg(
            jsonb_build_object(
                'store_app_id', w.store_app_id,
                'order_id', w.order_id,
                'revision', w.revision,
                'updated_at', w.updated_at,
                'payload', extensions.pgp_sym_decrypt(
                    w.payload_protected,
                    v_key
                )::text::jsonb
            )
            order by w.updated_at
        ),
        '[]'::jsonb
    )
      into v_rows
      from public.organization_order_workbench w
     where w.organization_id = v_member.organization_id
       and (
           p_store_app_ids is null
           or w.store_app_id = any(p_store_app_ids)
       )
       and (
           v_member.role in ('owner', 'admin')
           or exists (
               select 1
                 from public.organization_store_grants g
                where g.member_id = v_member.id
                  and g.app_id = w.store_app_id
           )
       );
    return coalesce(v_rows, '[]'::jsonb);
end;
$$;

grant execute on function public.upsert_organization_order_workbench(jsonb)
to authenticated, service_role;
grant execute on function public.pull_organization_order_workbench(text[])
to authenticated, service_role;
