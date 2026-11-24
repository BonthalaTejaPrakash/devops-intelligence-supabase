-- DevOps Intelligence — Supabase / Postgres Schema
-- Run in: Supabase Dashboard → SQL Editor, or: supabase db push
-- All tables use RLS. Users only see their org's data.

create extension if not exists "pgcrypto";
create extension if not exists "pg_stat_statements";

-- Organizations
create table if not exists public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text unique not null,
  plan text not null default 'free' check (plan in ('free','pro','team')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Profiles (linked to Supabase Auth users)
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  organization_id uuid references public.organizations(id) on delete cascade,
  email text not null,
  full_name text,
  avatar_url text,
  role text not null default 'developer' check (role in ('owner','admin','developer','viewer')),
  mfa_enabled boolean not null default false,
  email_verified boolean not null default false,
  timezone text not null default 'Asia/Kolkata',
  created_at timestamptz not null default now()
);

-- Projects
create table if not exists public.projects (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  name text not null,
  repository text,
  description text,
  status text not null default 'active' check (status in ('active','paused','rollback_required','archived')),
  priority text not null default 'medium' check (priority in ('critical','high','medium','low')),
  is_pinned boolean not null default false,
  sort_order int not null default 0,
  last_reviewed_at timestamptz,
  next_review_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Integrations — NEVER store plaintext tokens. Store encrypted reference only.
-- Production: encrypt with Vault/KMS/Fernet, rotate keys. See docs/SECURITY.
create table if not exists public.integrations (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  provider text not null check (provider in ('github','jenkins','circleci','gitlab','azure_devops','bitbucket','telegram','email_provider')),
  -- GitHub = GitHub App, Jenkins = API Token only, CircleCI = PAT, Azure = Entra ID
  auth_method text not null,
  status text not null default 'disconnected' check (status in ('connected','disconnected','error')),
  encrypted_credential_ref text, -- reference to Vault/KMS, NOT the token itself
  last_sync_at timestamptz,
  last_error text,
  sync_method text not null default 'incremental-etag-webhook',
  created_at timestamptz not null default now()
);

-- Pipelines
create table if not exists public.pipelines (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid references public.projects(id) on delete cascade,
  integration_id uuid references public.integrations(id) on delete set null,
  name text not null,
  provider text not null,
  repo text,
  branch text,
  workflow_id text,
  has_workflow_dispatch boolean not null default false, -- GitHub Run requires this
  is_selected boolean not null default false,
  priority text not null default 'medium',
  sort_order int not null default 0,
  created_at timestamptz not null default now()
);

-- Pipeline Runs — Retry = NEW row, original keeps status
create table if not exists public.pipeline_runs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  pipeline_id uuid not null references public.pipelines(id) on delete cascade,
  run_number int,
  provider_run_id text,
  status text not null check (status in ('success','failed','running','cancelled')),
  branch text, commit_sha text, triggered_by text, trigger_type text, environment text,
  started_at timestamptz, ended_at timestamptz,
  duration_sec int generated always as (extract(epoch from (ended_at - started_at))::int) stored,
  is_retry boolean not null default false,
  parent_run_id uuid references public.pipeline_runs(id),
  created_at timestamptz not null default now()
);
create index if not exists idx_runs_org_started on public.pipeline_runs(organization_id, started_at desc);
create index if not exists idx_runs_pipeline on public.pipeline_runs(pipeline_id, started_at desc);
create unique index if not exists idx_runs_provider_id on public.pipeline_runs(pipeline_id, provider_run_id);

-- Stages + Logs (metadata only — large logs in Storage bucket pipeline-logs)
create table if not exists public.pipeline_stages (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.pipeline_runs(id) on delete cascade,
  name text not null, status text not null, started_at timestamptz, ended_at timestamptz, log_path text
);
create table if not exists public.pipeline_logs (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.pipeline_runs(id) on delete cascade,
  stage_id uuid references public.pipeline_stages(id) on delete cascade,
  level text check (level in ('INFO','WARN','ERROR')),
  message text not null, -- MUST be redacted before insert (see backend/main.py redact_logs)
  redaction_count int not null default 0,
  copied_to_storage boolean not null default false, -- GitHub logs expire 90 days → copy to storage
  created_at timestamptz not null default now()
);

-- Custom selections, reminders, notifications, audit
create table if not exists public.custom_selections (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  profile_id uuid references public.profiles(id),
  name text not null, pipeline_ids uuid[] not null default '{}', created_at timestamptz default now()
);
create table if not exists public.reminders (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  project_id uuid references public.projects(id) on delete cascade,
  remind_at timestamptz not null, channel text not null default 'in_app',
  is_reviewed boolean not null default false, -- Genuine review = explicit Mark as reviewed click only
  reviewed_by uuid references public.profiles(id), reviewed_at timestamptz,
  unique (project_id, remind_at) -- prevent duplicate reminders
);
create table if not exists public.notification_preferences (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  event text not null, channel text not null, -- in_app / email_transactional / telegram
  severity text not null default 'all',
  quiet_hours_start time, quiet_hours_end time,
  critical_bypass_quiet_hours boolean not null default true, -- Critical BYPASSES quiet hours
  timezone text not null default 'Asia/Kolkata'
);
create table if not exists public.notification_deliveries (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  event text, channel text, status text, provider text, -- SES/SendGrid/Postmark/SMTP, NOT per-user Gmail
  dedupe_key text unique, created_at timestamptz default now()
);
create table if not exists public.audit_logs (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid references public.organizations(id),
  actor_id uuid references public.profiles(id),
  action text not null, entity text, details jsonb, created_at timestamptz default now()
);

-- Enable RLS on every application table. Tables without policies remain inaccessible
-- to browser roles until a feature-specific, organization-scoped policy is added.
alter table public.organizations enable row level security;
alter table public.profiles enable row level security;
alter table public.projects enable row level security;
alter table public.integrations enable row level security;
alter table public.pipelines enable row level security;
alter table public.pipeline_runs enable row level security;
alter table public.pipeline_stages enable row level security;
alter table public.pipeline_logs enable row level security;
alter table public.custom_selections enable row level security;
alter table public.reminders enable row level security;
alter table public.notification_preferences enable row level security;
alter table public.notification_deliveries enable row level security;
alter table public.audit_logs enable row level security;

-- Users can only see rows in their org. Service_role bypasses RLS (backend only).
create policy "org isolation — projects" on public.projects
  for all using (organization_id in (select organization_id from public.profiles where id = auth.uid()));
create policy "org isolation — pipelines" on public.pipelines
  for select using (organization_id in (select organization_id from public.profiles where id = auth.uid()));
create policy "org isolation — runs" on public.pipeline_runs
  for select using (organization_id in (select organization_id from public.profiles where id = auth.uid()));

-- Storage bucket for logs (create in Dashboard → Storage or via migration)
-- insert into storage.buckets (id, name, public) values ('pipeline-logs','pipeline-logs', false);
