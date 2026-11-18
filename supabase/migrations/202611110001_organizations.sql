create extension if not exists pgcrypto with schema extensions;

create table if not exists public.migration_backup_20261111_profiles
as table public.profiles;

create table if not exists public.organizations (
    id uuid primary key default extensions.gen_random_uuid(),
    name text not null,
    owner_id uuid not null references public.profiles(id) on delete restrict,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (owner_id)
);

create table if not exists public.organization_members (
    id uuid primary key default extensions.gen_random_uuid(),
    organization_id uuid not null references public.organizations(id) on delete cascade,
    user_id uuid not null references public.profiles(id) on delete cascade,
    role text not null default 'operator'
        check (role in ('owner', 'admin', 'manager', 'operator', 'viewer')),
    permissions jsonb not null default '{}'::jsonb,
    is_active boolean not null default true,
    created_by uuid references public.profiles(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (user_id),
    unique (organization_id, id)
);

create table if not exists public.organization_store_grants (
    id uuid primary key default extensions.gen_random_uuid(),
    organization_id uuid not null references public.organizations(id) on delete cascade,
    member_id uuid not null references public.organization_members(id) on delete cascade,
    app_id text not null,
    granted_by uuid references public.profiles(id) on delete set null,
    created_at timestamptz not null default now(),
    unique (member_id, app_id)
);

create table if not exists public.organization_vaults (
    organization_id uuid primary key references public.organizations(id) on delete cascade,
    payload_protected bytea not null,
    revision bigint not null default 1,
    updated_by uuid references public.profiles(id) on delete set null,
    updated_at timestamptz not null default now()
);

create table if not exists public.organization_audit_logs (
    id bigint generated always as identity primary key,
    organization_id uuid not null references public.organizations(id) on delete cascade,
    actor_user_id uuid references public.profiles(id) on delete set null,
    action text not null,
    target text not null default '',
    detail jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_org_members_org
    on public.organization_members(organization_id, created_at);
create index if not exists idx_org_grants_member
    on public.organization_store_grants(member_id, app_id);
create index if not exists idx_org_audit_org
    on public.organization_audit_logs(organization_id, created_at desc);

drop trigger if exists organizations_touch_updated_at on public.organizations;
create trigger organizations_touch_updated_at
before update on public.organizations
for each row execute function public.touch_updated_at();

drop trigger if exists organization_members_touch_updated_at
    on public.organization_members;
create trigger organization_members_touch_updated_at
before update on public.organization_members
for each row execute function public.touch_updated_at();

insert into public.app_secrets(secret_key, secret_value)
values (
    'organization_store_vault_key',
    encode(extensions.gen_random_bytes(32), 'hex')
)
on conflict (secret_key) do nothing;

alter table public.organizations enable row level security;
alter table public.organization_members enable row level security;
alter table public.organization_store_grants enable row level security;
alter table public.organization_vaults enable row level security;
alter table public.organization_audit_logs enable row level security;

drop policy if exists organizations_member_select on public.organizations;
create policy organizations_member_select
on public.organizations for select
to authenticated
using (
    exists (
        select 1
        from public.organization_members m
        where m.organization_id = organizations.id
          and m.user_id = auth.uid()
          and m.is_active
    )
);

drop policy if exists organization_members_self_select
    on public.organization_members;
create policy organization_members_self_select
on public.organization_members for select
to authenticated
using (
    user_id = auth.uid()
    or exists (
        select 1
        from public.organization_members manager
        where manager.organization_id = organization_members.organization_id
          and manager.user_id = auth.uid()
          and manager.is_active
          and manager.role in ('owner', 'admin')
    )
);

drop policy if exists organization_grants_member_select
    on public.organization_store_grants;
create policy organization_grants_member_select
on public.organization_store_grants for select
to authenticated
using (
    exists (
        select 1
        from public.organization_members current_member
        where current_member.id = organization_store_grants.member_id
          and current_member.user_id = auth.uid()
          and current_member.is_active
    )
    or exists (
        select 1
        from public.organization_members manager
        where manager.organization_id = organization_store_grants.organization_id
          and manager.user_id = auth.uid()
          and manager.is_active
          and manager.role in ('owner', 'admin')
    )
);

revoke all on public.organizations from anon, authenticated;
revoke all on public.organization_members from anon, authenticated;
revoke all on public.organization_store_grants from anon, authenticated;
revoke all on public.organization_vaults from anon, authenticated;
revoke all on public.organization_audit_logs from anon, authenticated;
grant select on public.organizations, public.organization_members,
    public.organization_store_grants
to authenticated;

create or replace function public.ensure_my_organization()
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
    v_user_id uuid := auth.uid();
    v_profile public.profiles;
    v_org public.organizations;
    v_member public.organization_members;
begin
    if v_user_id is null then
        raise exception '登录状态无效';
    end if;
    select * into v_profile
      from public.profiles
     where id = v_user_id;
    if not found or not v_profile.is_active then
        raise exception '账户不存在或已停用';
    end if;

    select m.* into v_member
      from public.organization_members m
     where m.user_id = v_user_id
       and m.is_active
     limit 1;
    if found then
        select * into v_org
          from public.organizations
         where id = v_member.organization_id;
        return jsonb_build_object(
            'organization_id', v_org.id,
            'organization_name', v_org.name,
            'member_id', v_member.id,
            'role', v_member.role,
            'permissions', v_member.permissions
        );
    end if;

    insert into public.organizations(name, owner_id)
    values (v_profile.username::text || ' 的团队', v_user_id)
    on conflict (owner_id) do update set name = organizations.name
    returning * into v_org;

    insert into public.organization_members(
        organization_id, user_id, role, permissions, created_by
    )
    values (
        v_org.id, v_user_id, 'owner',
        '{"all": true}'::jsonb, v_user_id
    )
    on conflict (user_id) do update
       set organization_id = excluded.organization_id,
           role = 'owner',
           permissions = '{"all": true}'::jsonb,
           is_active = true,
           updated_at = now()
    returning * into v_member;

    return jsonb_build_object(
        'organization_id', v_org.id,
        'organization_name', v_org.name,
        'member_id', v_member.id,
        'role', v_member.role,
        'permissions', v_member.permissions
    );
end;
$$;

grant execute on function public.ensure_my_organization()
to authenticated;

create or replace function public.organization_has_permission(
    p_permission text
)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select exists (
        select 1
        from public.organization_members m
        where m.user_id = auth.uid()
          and m.is_active
          and (
              m.role in ('owner', 'admin')
              or coalesce((m.permissions ->> p_permission)::boolean, false)
          )
    );
$$;

grant execute on function public.organization_has_permission(text)
to authenticated;

create or replace function public.push_organization_vault(
    p_snapshot jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
    v_user_id uuid := auth.uid();
    v_member public.organization_members;
    v_current jsonb;
    v_merged jsonb;
    v_store jsonb;
    v_app_id text;
    v_allowed boolean;
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

    insert into public.organization_vaults(
        organization_id, payload_protected, updated_by, updated_at
    )
    values (
        v_member.organization_id,
        extensions.pgp_sym_encrypt(
            '{"type":"langlang-store-accounts","version":1,"stores":[]}'::text,
            v_key
        ),
        v_user_id,
        now()
    )
    on conflict (organization_id) do nothing;

    select extensions.pgp_sym_decrypt(payload_protected, v_key)::text::jsonb
      into v_current
      from public.organization_vaults
     where organization_id = v_member.organization_id
     for update;
    v_merged := coalesce(v_current -> 'stores', '[]'::jsonb);

    for v_store in
        select value
          from jsonb_array_elements(coalesce(p_snapshot -> 'stores', '[]'::jsonb))
    loop
        v_app_id := trim(v_store ->> 'app_id');
        if v_app_id is null or v_app_id = '' then
            continue;
        end if;
        v_allowed := v_member.role in ('owner', 'admin')
            or exists (
                select 1
                  from public.organization_store_grants g
                 where g.member_id = v_member.id
                   and g.app_id = v_app_id
            );
        if not v_allowed then
            if coalesce(
                (v_member.permissions ->> 'stores_login')::boolean,
                false
            ) then
                insert into public.organization_store_grants(
                    organization_id, member_id, app_id, granted_by
                )
                values (
                    v_member.organization_id, v_member.id,
                    v_app_id, v_user_id
                )
                on conflict (member_id, app_id) do nothing;
                v_allowed := true;
            else
                continue;
            end if;
        end if;
        v_merged := (
            select coalesce(jsonb_agg(value), '[]'::jsonb)
              from jsonb_array_elements(v_merged)
             where value ->> 'app_id' <> v_app_id
        ) || jsonb_build_array(v_store);
    end loop;

    v_current := jsonb_build_object(
        'type', 'langlang-store-accounts',
        'version', 1,
        'exported_at', now(),
        'groups', coalesce(p_snapshot -> 'groups', '[]'::jsonb),
        'stores', v_merged
    );
    update public.organization_vaults
       set payload_protected = extensions.pgp_sym_encrypt(v_current::text, v_key),
           revision = revision + 1,
           updated_by = v_user_id,
           updated_at = now()
     where organization_id = v_member.organization_id;
    insert into public.organization_audit_logs(
        organization_id, actor_user_id, action, detail
    )
    values (
        v_member.organization_id, v_user_id, 'vault_push',
        jsonb_build_object('store_count', jsonb_array_length(v_merged))
    );
    return jsonb_build_object(
        'stored', true,
        'store_count', jsonb_array_length(v_merged)
    );
end;
$$;

grant execute on function public.push_organization_vault(jsonb)
to authenticated;

create or replace function public.pull_organization_vault()
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
    v_user_id uuid := auth.uid();
    v_member public.organization_members;
    v_snapshot jsonb;
    v_filtered jsonb;
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
    select extensions.pgp_sym_decrypt(payload_protected, v_key)::text::jsonb
      into v_snapshot
      from public.organization_vaults
     where organization_id = v_member.organization_id;
    if v_snapshot is null then
        return jsonb_build_object(
            'type', 'langlang-store-accounts',
            'version', 1,
            'groups', '[]'::jsonb,
            'stores', '[]'::jsonb
        );
    end if;
    if v_member.role in ('owner', 'admin') then
        return v_snapshot || jsonb_build_object(
            'type', 'langlang-store-accounts',
            'version', 1
        );
    end if;
    select coalesce(jsonb_agg(value), '[]'::jsonb)
      into v_filtered
      from jsonb_array_elements(coalesce(v_snapshot -> 'stores', '[]'::jsonb))
     where exists (
        select 1
          from public.organization_store_grants g
         where g.member_id = v_member.id
           and g.app_id = value ->> 'app_id'
     );
    return jsonb_build_object(
        'type', 'langlang-store-accounts',
        'version', 1,
        'groups', coalesce(v_snapshot -> 'groups', '[]'::jsonb),
        'stores', coalesce(v_filtered, '[]'::jsonb)
    );
end;
$$;

grant execute on function public.pull_organization_vault()
to authenticated;

create or replace function public.list_my_team_members()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
    v_member public.organization_members;
begin
    select * into v_member
      from public.organization_members
     where user_id = auth.uid()
       and is_active
     limit 1;
    if not found then
        return '[]'::jsonb;
    end if;
    return coalesce(
        (
            select jsonb_agg(
                jsonb_build_object(
                    'member_id', m.id,
                    'user_id', m.user_id,
                    'username', p.username,
                    'email', p.email,
                    'role', m.role,
                    'permissions', m.permissions,
                    'is_active', m.is_active,
                    'grants', coalesce(
                        (
                            select jsonb_agg(g.app_id order by g.app_id)
                              from public.organization_store_grants g
                             where g.member_id = m.id
                        ),
                        '[]'::jsonb
                    )
                )
                order by
                    case m.role
                        when 'owner' then 0
                        when 'admin' then 1
                        when 'manager' then 2
                        else 3
                    end,
                    p.username
            )
            from public.organization_members m
            join public.profiles p on p.id = m.user_id
            where m.organization_id = v_member.organization_id
        ),
        '[]'::jsonb
    );
end;
$$;

grant execute on function public.list_my_team_members()
to authenticated;

grant usage on schema public to service_role;
grant select, insert, update, delete on public.profiles
to service_role;
grant select, insert, update, delete on public.organizations,
    public.organization_members, public.organization_store_grants,
    public.organization_vaults, public.organization_audit_logs
to service_role;
grant usage, select on all sequences in schema public
to service_role;
grant execute on function public.ensure_my_organization(),
    public.organization_has_permission(text),
    public.push_organization_vault(jsonb),
    public.pull_organization_vault(),
    public.list_my_team_members()
to service_role;

insert into public.organizations(name, owner_id)
select
    p.username::text || ' 的团队',
    p.id
from public.profiles p
where p.is_active
on conflict (owner_id) do nothing;

insert into public.organization_members(
    organization_id, user_id, role, permissions, created_by
)
select
    o.id,
    o.owner_id,
    'owner',
    '{"all": true}'::jsonb,
    o.owner_id
from public.organizations o
on conflict (user_id) do nothing;
