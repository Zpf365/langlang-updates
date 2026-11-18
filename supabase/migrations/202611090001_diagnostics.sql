create table if not exists public.diagnostic_reports (
    id uuid primary key default extensions.gen_random_uuid(),
    report_id text not null unique,
    user_id uuid references public.profiles(id) on delete set null,
    username text not null default '',
    app_version text not null default '',
    device_hash text not null default '',
    category text not null default '',
    severity text not null default 'error',
    route text not null default '',
    page text not null default '',
    message text not null default '',
    detail jsonb not null default '{}'::jsonb,
    process_info jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_diagnostic_reports_created
    on public.diagnostic_reports(created_at desc);
create index if not exists idx_diagnostic_reports_category
    on public.diagnostic_reports(category, created_at desc);
create index if not exists idx_diagnostic_reports_user
    on public.diagnostic_reports(user_id, created_at desc);

alter table public.diagnostic_reports enable row level security;

drop policy if exists diagnostic_reports_select_admin
    on public.diagnostic_reports;
create policy diagnostic_reports_select_admin
on public.diagnostic_reports for select
to authenticated
using (public.is_admin());

revoke all on public.diagnostic_reports from anon, authenticated;
grant select on public.diagnostic_reports to authenticated;
