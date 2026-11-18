create or replace function public.mobile_sales_list_devices()
returns jsonb
language plpgsql
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

grant execute on function public.mobile_sales_list_devices()
to authenticated;
