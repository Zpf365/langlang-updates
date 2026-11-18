insert into public.command_permissions(
    command,
    permission,
    store_scoped,
    label
)
values (
    'stores.organize',
    'stores_edit',
    false,
    '管理店铺分组'
)
on conflict (command) do update set
    permission = excluded.permission,
    store_scoped = excluded.store_scoped,
    label = excluded.label;

insert into public.command_permissions(
    command,
    permission,
    store_scoped,
    label
)
values (
    'automation.manage',
    'products_edit',
    false,
    '管理自动化任务'
)
on conflict (command) do update set
    permission = excluded.permission,
    store_scoped = excluded.store_scoped,
    label = excluded.label;

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
    v_allowed boolean;
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
        v_allowed := v_member.role in ('owner', 'admin')
            or (
                coalesce(
                    (v_member.permissions ->> 'stores_delete')::boolean,
                    false
                )
                and exists (
                    select 1
                      from public.organization_store_grants g
                     where g.member_id = v_member.id
                       and g.app_id = v_app_id
                )
            );
        if not v_allowed then
            raise exception '没有删除该店铺的权限：%', v_app_id;
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
