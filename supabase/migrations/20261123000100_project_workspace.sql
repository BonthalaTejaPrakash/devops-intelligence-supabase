-- Keep existing organization projects and give new accounts a private workspace.
begin;

alter table public.projects
  add column if not exists owner_id uuid references auth.users(id) on delete cascade;

alter table public.projects alter column organization_id drop not null;
alter table public.projects drop constraint if exists projects_one_workspace;
alter table public.projects add constraint projects_one_workspace
  check (num_nonnulls(organization_id, owner_id) = 1);
create index if not exists projects_owner_id_idx on public.projects(owner_id);

alter table public.profiles enable row level security;
alter table public.pipeline_stages enable row level security;
alter table public.pipeline_logs enable row level security;
alter table public.custom_selections enable row level security;
alter table public.notification_preferences enable row level security;
alter table public.audit_logs enable row level security;

revoke all on public.profiles, public.projects from anon, authenticated;
grant select (id, organization_id, role) on public.profiles to authenticated;
grant select on public.projects to authenticated;
grant insert (organization_id, owner_id, name, repository, description, status, priority, is_pinned, sort_order)
  on public.projects to authenticated;
grant update (name, repository, description, status, priority, is_pinned, sort_order)
  on public.projects to authenticated;

drop policy if exists "profile membership lookup" on public.profiles;
create policy "profile membership lookup" on public.profiles for select to authenticated
  using (id = (select auth.uid()));

drop policy if exists "org isolation — projects" on public.projects;
drop policy if exists "read workspace projects" on public.projects;
drop policy if exists "create workspace projects" on public.projects;
drop policy if exists "update workspace projects" on public.projects;

create policy "read workspace projects" on public.projects for select to authenticated
  using (owner_id = (select auth.uid()) or organization_id in (
    select p.organization_id from public.profiles p where p.id = (select auth.uid())
  ));

create policy "create workspace projects" on public.projects for insert to authenticated
  with check (owner_id = (select auth.uid()) or organization_id in (
    select p.organization_id from public.profiles p
    where p.id = (select auth.uid()) and p.role in ('owner', 'admin', 'developer')
  ));

create policy "update workspace projects" on public.projects for update to authenticated
  using (owner_id = (select auth.uid()) or organization_id in (
    select p.organization_id from public.profiles p
    where p.id = (select auth.uid()) and p.role in ('owner', 'admin', 'developer')
  ))
  with check (owner_id = (select auth.uid()) or organization_id in (
    select p.organization_id from public.profiles p
    where p.id = (select auth.uid()) and p.role in ('owner', 'admin', 'developer')
  ));

commit;
