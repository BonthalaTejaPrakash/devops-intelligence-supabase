begin;

drop policy if exists "organization members can read organizations" on public.organizations;
create policy "organization members can read organizations" on public.organizations
  for select to authenticated
  using (id in (select organization_id from public.profiles where id = (select auth.uid())));

drop policy if exists "users can read own profile" on public.profiles;
create policy "users can read own profile" on public.profiles
  for select to authenticated
  using (id = (select auth.uid()));

drop policy if exists "organization members can read integrations" on public.integrations;
create policy "organization members can read integrations" on public.integrations
  for select to authenticated
  using (organization_id in (select organization_id from public.profiles where id = (select auth.uid())));

drop policy if exists "organization members can read pipelines" on public.pipelines;
create policy "organization members can read pipelines" on public.pipelines
  for select to authenticated
  using (organization_id in (select organization_id from public.profiles where id = (select auth.uid())));

drop policy if exists "org isolation — pipelines" on public.pipelines;
drop policy if exists "organization developers can create pipelines" on public.pipelines;
create policy "organization developers can create pipelines" on public.pipelines
  for insert to authenticated
  with check (
    organization_id in (
      select organization_id from public.profiles
      where id = (select auth.uid()) and role in ('owner', 'admin', 'developer')
    )
    and (project_id is null or project_id in (
      select id from public.projects where organization_id = pipelines.organization_id
    ))
    and (integration_id is null or integration_id in (
      select id from public.integrations where organization_id = pipelines.organization_id
    ))
  );

drop policy if exists "organization developers can update pipelines" on public.pipelines;
create policy "organization developers can update pipelines" on public.pipelines
  for update to authenticated
  using (organization_id in (
    select organization_id from public.profiles
    where id = (select auth.uid()) and role in ('owner', 'admin', 'developer')
  ))
  with check (
    organization_id in (
      select organization_id from public.profiles
      where id = (select auth.uid()) and role in ('owner', 'admin', 'developer')
    )
    and (project_id is null or project_id in (
      select id from public.projects where organization_id = pipelines.organization_id
    ))
    and (integration_id is null or integration_id in (
      select id from public.integrations where organization_id = pipelines.organization_id
    ))
  );

drop policy if exists "organization members can read runs" on public.pipeline_runs;
create policy "organization members can read runs" on public.pipeline_runs
  for select to authenticated
  using (organization_id in (select organization_id from public.profiles where id = (select auth.uid())));
drop policy if exists "org isolation — runs" on public.pipeline_runs;

drop policy if exists "organization members can read stages" on public.pipeline_stages;
create policy "organization members can read stages" on public.pipeline_stages
  for select to authenticated
  using (exists (
    select 1 from public.pipeline_runs r
    where r.id = run_id
      and r.organization_id in (
        select organization_id from public.profiles where id = (select auth.uid())
      )
  ));

drop policy if exists "organization members can read logs" on public.pipeline_logs;
create policy "organization members can read logs" on public.pipeline_logs
  for select to authenticated
  using (exists (
    select 1 from public.pipeline_runs r
    where r.id = run_id
      and r.organization_id in (
        select organization_id from public.profiles where id = (select auth.uid())
      )
  ));

drop policy if exists "users can manage own selections" on public.custom_selections;
create policy "users can manage own selections" on public.custom_selections
  for all to authenticated
  using (
    profile_id = (select auth.uid())
    and organization_id in (select organization_id from public.profiles where id = (select auth.uid()))
  )
  with check (
    profile_id = (select auth.uid())
    and organization_id in (select organization_id from public.profiles where id = (select auth.uid()))
  );

drop policy if exists "organization members can manage reminders" on public.reminders;
drop policy if exists "organization members can read reminders" on public.reminders;
create policy "organization members can read reminders" on public.reminders
  for select to authenticated
  using (organization_id in (select organization_id from public.profiles where id = (select auth.uid())));

drop policy if exists "organization developers can manage reminders" on public.reminders;
create policy "organization developers can manage reminders" on public.reminders
  for all to authenticated
  using (organization_id in (
    select organization_id from public.profiles
    where id = (select auth.uid()) and role in ('owner', 'admin', 'developer')
  ))
  with check (organization_id in (
    select organization_id from public.profiles
    where id = (select auth.uid()) and role in ('owner', 'admin', 'developer')
  ));

drop policy if exists "organization members can read notification preferences" on public.notification_preferences;
create policy "organization members can read notification preferences" on public.notification_preferences
  for select to authenticated
  using (organization_id in (select organization_id from public.profiles where id = (select auth.uid())));

drop policy if exists "organization members can read notification deliveries" on public.notification_deliveries;
create policy "organization members can read notification deliveries" on public.notification_deliveries
  for select to authenticated
  using (organization_id in (select organization_id from public.profiles where id = (select auth.uid())));

drop policy if exists "organization admins can read audit logs" on public.audit_logs;
create policy "organization admins can read audit logs" on public.audit_logs
  for select to authenticated
  using (organization_id in (
    select organization_id from public.profiles
    where id = (select auth.uid()) and role in ('owner', 'admin')
  ));

commit;
