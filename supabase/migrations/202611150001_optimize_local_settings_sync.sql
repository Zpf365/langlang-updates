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
        if v_setting_key in (
            'account_store_sync_error',
            'auto_refresh',
            'auto_refresh_batch_size',
            'auto_refresh_cursor',
            'automatic_backup_at',
            'automatic_backup_date',
            'last_update_check',
            'skipped_update_version',
            'tester_update_source',
            'upload_batch',
            'upload_last_source',
            'upload_tasks',
            'update_auto_check',
            'update_source',
            'violation_daily_attempt_at',
            'violation_daily_last_success'
        )
        or v_setting_key like 'organization_synced_appids:%'
        or v_setting_key like 'pending_cloud_store_deletions:%'
        or v_setting_key like 'analytics_product_backfill_at_%'
        or v_setting_key like 'orders_day_backfill_%'
        or v_setting_key like 'product_quota_%'
        or v_setting_key like 'store_metrics_%' then
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
              and s.setting_key not in (
                  'account_store_sync_error',
                  'auto_refresh',
                  'auto_refresh_batch_size',
                  'auto_refresh_cursor',
                  'automatic_backup_at',
                  'automatic_backup_date',
                  'last_update_check',
                  'skipped_update_version',
                  'tester_update_source',
                  'upload_batch',
                  'upload_last_source',
                  'upload_tasks',
                  'update_auto_check',
                  'update_source',
                  'violation_daily_attempt_at',
                  'violation_daily_last_success'
              )
              and s.setting_key not like 'organization_synced_appids:%'
              and s.setting_key not like 'pending_cloud_store_deletions:%'
              and s.setting_key not like 'analytics_product_backfill_at_%'
              and s.setting_key not like 'orders_day_backfill_%'
              and s.setting_key not like 'product_quota_%'
              and s.setting_key not like 'store_metrics_%'
        ),
        '[]'::jsonb
    );
end;
$$;

grant execute on function public.upsert_organization_settings(jsonb)
to authenticated, service_role;
grant execute on function public.pull_organization_settings()
to authenticated, service_role;
