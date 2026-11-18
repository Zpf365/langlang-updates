create or replace function public.mobile_sales_owner_list_tasks(
    p_device_id text
)
returns jsonb
language plpgsql
stable
security definer
set search_path = public, extensions
as $$
declare
    v_member public.organization_members;
begin
    v_member := public._mobile_member();
    if not (
        v_member.role in ('owner', 'admin', 'manager')
        or public.organization_has_permission('procurement')
    ) then
        raise exception '当前账号没有查看采购任务的权限';
    end if;
    return coalesce((
        select jsonb_agg(
            to_jsonb(t)
            order by t.created_at asc, t.id asc
        )
          from public.sales_mobile_tasks t
         where t.organization_id = v_member.organization_id
           and t.assigned_device_id = trim(coalesce(p_device_id, ''))
    ), '[]'::jsonb);
end;
$$;

grant execute on function public.mobile_sales_owner_list_tasks(text)
to authenticated;
