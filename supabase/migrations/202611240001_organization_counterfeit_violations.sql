create table if not exists public.organization_counterfeit_violations (
    organization_id uuid not null
        references public.organizations(id) on delete cascade,
    dedupe_key text not null,
    normalized_title text not null,
    title text not null,
    reason_category text not null
        check (reason_category in ('出售假冒商品', '假冒商品', '售假')),
    product_id text not null default '',
    store_app_id text not null,
    revision bigint not null default 1,
    updated_by uuid references public.profiles(id) on delete set null,
    updated_at timestamptz not null default now(),
    primary key (organization_id, dedupe_key)
);

create index if not exists idx_organization_counterfeit_violations_updated
on public.organization_counterfeit_violations(organization_id, updated_at desc);

alter table public.organization_counterfeit_violations
enable row level security;
revoke all on public.organization_counterfeit_violations
from anon, authenticated;
grant all on public.organization_counterfeit_violations to service_role;

create or replace function public.upsert_organization_counterfeit_violations(
    p_rows jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_user_id uuid := auth.uid();
    v_member public.organization_members;
    v_item jsonb;
    v_reason text;
    v_normalized text;
    v_title text;
    v_dedupe_key text;
    v_store_app_id text;
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
        raise exception '没有共享云售假索引的权限';
    end if;

    for v_item in
        select value
          from jsonb_array_elements(coalesce(p_rows, '[]'::jsonb))
    loop
        v_reason := trim(coalesce(v_item ->> 'reason_category', ''));
        v_normalized := trim(coalesce(v_item ->> 'normalized_title', ''));
        v_title := trim(coalesce(v_item ->> 'title', ''));
        v_dedupe_key := trim(coalesce(v_item ->> 'dedupe_key', ''));
        v_store_app_id := trim(coalesce(v_item ->> 'store_app_id', ''));
        if v_reason not in ('出售假冒商品', '假冒商品', '售假')
           or v_normalized = ''
           or v_title = ''
           or v_dedupe_key = ''
           or v_store_app_id = '' then
            continue;
        end if;
        insert into public.organization_counterfeit_violations(
            organization_id,
            dedupe_key,
            normalized_title,
            title,
            reason_category,
            product_id,
            store_app_id,
            revision,
            updated_by,
            updated_at
        )
        values (
            v_member.organization_id,
            v_dedupe_key,
            v_normalized,
            left(v_title, 500),
            v_reason,
            left(trim(coalesce(v_item ->> 'product_id', '')), 200),
            left(v_store_app_id, 200),
            1,
            v_user_id,
            now()
        )
        on conflict (organization_id, dedupe_key)
        do update set
            normalized_title =
                excluded.normalized_title,
            title = excluded.title,
            reason_category = excluded.reason_category,
            product_id = excluded.product_id,
            store_app_id = excluded.store_app_id,
            revision =
                organization_counterfeit_violations.revision + 1,
            updated_by = v_user_id,
            updated_at = now();
        v_saved := v_saved + 1;
    end loop;
    return jsonb_build_object('saved', v_saved);
end;
$$;

create or replace function public.pull_organization_counterfeit_violations()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_user_id uuid := auth.uid();
    v_member public.organization_members;
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
    return coalesce(
        (
            select jsonb_agg(
                jsonb_build_object(
                    'dedupe_key', r.dedupe_key,
                    'normalized_title', r.normalized_title,
                    'title', r.title,
                    'reason_category', r.reason_category,
                    'product_id', r.product_id,
                    'store_app_id', r.store_app_id,
                    'revision', r.revision,
                    'updated_at', r.updated_at
                )
                order by r.updated_at desc, r.dedupe_key
            )
            from public.organization_counterfeit_violations r
            where r.organization_id = v_member.organization_id
        ),
        '[]'::jsonb
    );
end;
$$;

grant execute on function public.upsert_organization_counterfeit_violations(jsonb)
to authenticated, service_role;
grant execute on function public.pull_organization_counterfeit_violations()
to authenticated, service_role;
