begin;

alter table public.pipeline_runs
  alter column provider_run_id type text
  using provider_run_id::text;

commit;
