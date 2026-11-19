-- The diagnostic Edge Function uses the service role to persist reports.
-- Existing submitted reports are kept; this migration only restores grants.
grant select, insert, update
on public.diagnostic_reports
to service_role;
