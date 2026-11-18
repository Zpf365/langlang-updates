insert into public.command_permissions(
    command, permission, store_scoped, label
)
values (
    'settings.manage',
    'stores_view',
    false,
    '修改共享页面设置'
)
on conflict (command) do update set
    permission = excluded.permission,
    store_scoped = excluded.store_scoped,
    label = excluded.label;

create table if not exists public.organization_settings (
    organization_id uuid not null references public.organizations(id) on delete cascade,
    setting_key text not null,
    value_protected bytea not null,
    revision bigint not null default 1,
    updated_by uuid references public.profiles(id) on delete set null,
    updated_at timestamptz not null default now(),
    primary key (organization_id, setting_key)
);

alter table public.organization_settings enable row level security;
revoke all on public.organization_settings from anon, authenticated;
grant all on public.organization_settings to service_role;

create or replace function public.upsert_organization_settings(
    p_items jsonb
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
    v_item jsonb;
    v_setting_key text;
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
           (v_member.permissions ->> 'stores_view')::boolean,
           false
       ) then
        raise exception '没有修改共享设置的权限';
    end if;
    select secret_value into v_key
      from public.app_secrets
     where secret_key = 'organization_store_vault_key';
    if v_key is null then
        raise exception '组织保险箱密钥未配置';
    end if;

    for v_item in
        select value
          from jsonb_array_elements(coalesce(p_items, '[]'::jsonb))
    loop
        v_setting_key := trim(coalesce(v_item ->> 'key', ''));
        if v_setting_key = '' then
            continue;
        end if;
        insert into public.organization_settings(
            organization_id,
            setting_key,
            value_protected,
            revision,
            updated_by,
            updated_at
        )
        values (
            v_member.organization_id,
            v_setting_key,
            extensions.pgp_sym_encrypt(
                coalesce(v_item ->> 'value', ''),
                v_key
            ),
            1,
            v_user_id,
            now()
        )
        on conflict (organization_id, setting_key)
        do update set
            value_protected = excluded.value_protected,
            revision = organization_settings.revision + 1,
            updated_by = v_user_id,
            updated_at = now();
        v_saved := v_saved + 1;
    end loop;
    return jsonb_build_object('saved', v_saved);
end;
$$;

create or replace function public.pull_organization_settings()
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
    v_user_id uuid := auth.uid();
    v_member public.organization_members;
    v_key text;
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
    select secret_value into v_key
      from public.app_secrets
     where secret_key = 'organization_store_vault_key';
    if v_key is null then
        raise exception '组织保险箱密钥未配置';
    end if;
    return coalesce(
        (
            select jsonb_agg(
                jsonb_build_object(
                    'key', s.setting_key,
                    'value', extensions.pgp_sym_decrypt(
                        s.value_protected,
                        v_key
                    ),
                    'revision', s.revision,
                    'updated_at', s.updated_at
                )
                order by s.setting_key
            )
            from public.organization_settings s
            where s.organization_id = v_member.organization_id
              and (
                  v_member.role in ('owner', 'admin')
                  or coalesce(
                      (v_member.permissions ->> 'stores_view')::boolean,
                      false
                  )
              )
        ),
        '[]'::jsonb
    );
end;
$$;

grant execute on function public.upsert_organization_settings(jsonb)
to authenticated, service_role;
grant execute on function public.pull_organization_settings()
to authenticated, service_role;
