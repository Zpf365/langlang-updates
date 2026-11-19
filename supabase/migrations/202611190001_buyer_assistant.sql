-- LangLang 1.8.49 buyer assistant cloud schema.
-- Additive only. Existing auth and merchant tables are not modified.

create table if not exists public.buyer_identities (
  buyer_identity_id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  mini_appid text not null,
  openid text not null,
  unionid text,
  display_name text not null default '',
  identity_status text not null default 'verified'
    check (identity_status in ('verified', 'revoked')),
  created_at timestamptz not null default now(),
  last_login_at timestamptz not null default now(),
  unique (mini_appid, openid)
);

create table if not exists public.buyer_device_bindings (
  binding_id uuid primary key default gen_random_uuid(),
  buyer_identity_id uuid not null references public.buyer_identities(buyer_identity_id)
    on delete cascade,
  device_label text not null default '',
  status text not null default 'active'
    check (status in ('active', 'revoked', 'expired')),
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  revoked_at timestamptz
);

create table if not exists public.buyer_pairing_tickets (
  pairing_id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  store_app_id text not null,
  ticket_hash text not null unique,
  status text not null default 'pending'
    check (status in ('pending', 'claimed', 'bound', 'expired', 'cancelled')),
  expires_at timestamptz not null,
  claimed_at timestamptz,
  bound_at timestamptz,
  buyer_identity_id uuid references public.buyer_identities(buyer_identity_id)
    on delete set null,
  mini_session_hash text,
  mini_appid text not null default '',
  mini_openid text not null default '',
  mini_unionid text,
  created_at timestamptz not null default now()
);

create index if not exists idx_buyer_pairing_owner
  on public.buyer_pairing_tickets(owner_user_id, created_at desc);

create table if not exists public.buyer_assistant_tasks (
  assistant_task_id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  buyer_identity_id uuid references public.buyer_identities(buyer_identity_id)
    on delete set null,
  store_app_id text not null,
  store_name text not null,
  local_run_id text not null,
  idempotency_key text not null,
  status text not null default 'queued'
    check (status in (
      'queued', 'running', 'paused', 'awaiting_order',
      'order_matched', 'cancelled', 'failed', 'unknown'
    )),
  requested_count integer not null check (requested_count between 1 and 10),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_user_id, idempotency_key)
);

create table if not exists public.buyer_assistant_task_items (
  assistant_item_id uuid primary key default gen_random_uuid(),
  assistant_task_id uuid not null references public.buyer_assistant_tasks(assistant_task_id)
    on delete cascade,
  sequence_no integer not null check (sequence_no > 0),
  product_id text not null,
  sku_id text not null,
  title text not null,
  image_url text not null default '',
  specs_json jsonb not null default '{}'::jsonb,
  unit_price_cents integer not null check (unit_price_cents > 0),
  quantity integer not null check (quantity > 0),
  stock integer not null check (stock >= 0),
  status text not null default 'pending'
    check (status in (
      'pending', 'opened', 'user_confirmed', 'skipped',
      'failed', 'unknown'
    )),
  user_confirmed_at timestamptz,
  unique (assistant_task_id, product_id, sku_id)
);

create table if not exists public.buyer_action_events (
  event_id uuid primary key default gen_random_uuid(),
  assistant_task_id uuid not null references public.buyer_assistant_tasks(assistant_task_id)
    on delete cascade,
  assistant_item_id uuid references public.buyer_assistant_task_items(assistant_item_id)
    on delete set null,
  buyer_identity_id uuid references public.buyer_identities(buyer_identity_id)
    on delete set null,
  action text not null,
  result text not null default 'info',
  detail_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_buyer_action_events_task
  on public.buyer_action_events(assistant_task_id, created_at);

create table if not exists public.buyer_order_matches (
  match_id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  assistant_task_id uuid not null references public.buyer_assistant_tasks(assistant_task_id)
    on delete cascade,
  buyer_identity_id uuid references public.buyer_identities(buyer_identity_id)
    on delete set null,
  store_app_id text not null,
  platform_order_id text not null,
  match_status text not null default 'unverified'
    check (match_status in (
      'unverified', 'pending_payment', 'mismatch', 'not_found', 'unknown'
    )),
  order_status text not null default '',
  total_amount_cents integer not null default 0 check (total_amount_cents >= 0),
  match_score numeric not null default 0,
  detail_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  verified_at timestamptz not null default now(),
  unique (assistant_task_id, platform_order_id)
);

create table if not exists public.buyer_integration_state (
  owner_user_id uuid primary key references auth.users(id) on delete cascade,
  store_app_id text not null default '',
  store_name text not null default '',
  open_platform_linked boolean not null default false,
  shop_partner_authorized boolean not null default false,
  product_component_ready boolean not null default false,
  order_component_ready boolean not null default false,
  status_reason text not null default '',
  updated_at timestamptz not null default now()
);

alter table public.buyer_identities enable row level security;
alter table public.buyer_device_bindings enable row level security;
alter table public.buyer_pairing_tickets enable row level security;
alter table public.buyer_assistant_tasks enable row level security;
alter table public.buyer_assistant_task_items enable row level security;
alter table public.buyer_action_events enable row level security;
alter table public.buyer_order_matches enable row level security;
alter table public.buyer_integration_state enable row level security;

drop policy if exists buyer_identity_owner_read on public.buyer_identities;
create policy buyer_identity_owner_read on public.buyer_identities
  for select using (owner_user_id = auth.uid());

drop policy if exists buyer_task_owner_read on public.buyer_assistant_tasks;
create policy buyer_task_owner_read on public.buyer_assistant_tasks
  for select using (owner_user_id = auth.uid());

drop policy if exists buyer_item_owner_read on public.buyer_assistant_task_items;
create policy buyer_item_owner_read on public.buyer_assistant_task_items
  for select using (
    exists (
      select 1 from public.buyer_assistant_tasks t
      where t.assistant_task_id = assistant_task_id
        and t.owner_user_id = auth.uid()
    )
  );

drop policy if exists buyer_event_owner_read on public.buyer_action_events;
create policy buyer_event_owner_read on public.buyer_action_events
  for select using (
    exists (
      select 1 from public.buyer_assistant_tasks t
      where t.assistant_task_id = assistant_task_id
        and t.owner_user_id = auth.uid()
    )
  );

drop policy if exists buyer_order_owner_read on public.buyer_order_matches;
create policy buyer_order_owner_read on public.buyer_order_matches
  for select using (owner_user_id = auth.uid());

drop policy if exists buyer_integration_owner_read on public.buyer_integration_state;
create policy buyer_integration_owner_read on public.buyer_integration_state
  for select using (owner_user_id = auth.uid());
