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
    v_deleted jsonb;
    v_store jsonb;
    v_app_id text;
    v_created_at timestamptz;
    v_deleted_at timestamptz;
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
            '{"type":"langlang-store-accounts","version":1,"stores":[],"deleted_stores":{}}'::text,
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
    v_deleted := coalesce(v_current -> 'deleted_stores', '{}'::jsonb);

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

        if v_deleted ? v_app_id then
            v_deleted_at := (v_deleted ->> v_app_id)::timestamptz;
            v_created_at := nullif(v_store ->> 'created_at', '')::timestamptz;
            if v_created_at is null or v_created_at <= v_deleted_at then
                continue;
            end if;
            v_deleted := v_deleted - v_app_id;
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
        'stores', v_merged,
        'deleted_stores', v_deleted
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

create or replace function public.delete_organization_stores(
    p_app_ids text[]
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
    v_user_id uuid := auth.uid();
    v_member public.organization_members;
    v_snapshot jsonb;
    v_stores jsonb;
    v_deleted jsonb;
    v_app_id text;
    v_key text;
    v_removed integer;
begin
    if v_user_id is null then
        raise exception '登录状态无效';
    end if;
    select * into v_member
      from public.organization_members
     where user_id = v_user_id
       and is_active
     limit 1;
    if not found or v_member.role not in ('owner', 'admin') then
        raise exception '只有主账号或共同主账号可以删除云端店铺';
    end if;
    select secret_value into v_key
      from public.app_secrets
     where secret_key = 'organization_store_vault_key';
    if v_key is null then
        raise exception '组织保险箱密钥未配置';
    end if;

    select extensions.pgp_sym_decrypt(payload_protected, v_key)::text::jsonb
      into v_snapshot
      from public.organization_vaults
     where organization_id = v_member.organization_id
     for update;
    if v_snapshot is null then
        return jsonb_build_object('stored', true, 'removed', 0);
    end if;

    v_deleted := coalesce(v_snapshot -> 'deleted_stores', '{}'::jsonb);
    foreach v_app_id in array coalesce(p_app_ids, '{}'::text[])
    loop
        v_app_id := trim(v_app_id);
        if v_app_id is null or v_app_id = '' then
            continue;
        end if;
        v_deleted := jsonb_set(
            v_deleted,
            array[v_app_id],
            to_jsonb(now()),
            true
        );
    end loop;

    select coalesce(jsonb_agg(value), '[]'::jsonb)
      into v_stores
      from jsonb_array_elements(
          coalesce(v_snapshot -> 'stores', '[]'::jsonb)
      )
     where coalesce(value ->> 'app_id', '') <> all(
         coalesce(p_app_ids, '{}'::text[])
     );
    v_removed := jsonb_array_length(
        coalesce(v_snapshot -> 'stores', '[]'::jsonb)
    ) - jsonb_array_length(v_stores);

    v_snapshot := jsonb_build_object(
        'type', 'langlang-store-accounts',
        'version', 1,
        'exported_at', now(),
        'groups', coalesce(v_snapshot -> 'groups', '[]'::jsonb),
        'stores', v_stores,
        'deleted_stores', v_deleted
    );
    update public.organization_vaults
       set payload_protected = extensions.pgp_sym_encrypt(v_snapshot::text, v_key),
           revision = revision + 1,
           updated_by = v_user_id,
           updated_at = now()
     where organization_id = v_member.organization_id;
    delete from public.organization_store_grants
     where organization_id = v_member.organization_id
       and app_id = any(coalesce(p_app_ids, '{}'::text[]));
    insert into public.organization_audit_logs(
        organization_id, actor_user_id, action, detail
    )
    values (
        v_member.organization_id,
        v_user_id,
        'vault_store_delete',
        jsonb_build_object('app_ids', p_app_ids, 'removed', v_removed)
    );
    return jsonb_build_object('stored', true, 'removed', v_removed);
end;
$$;

grant execute on function public.delete_organization_stores(text[])
to authenticated, service_role;
