begin;

alter table public.pipelines
  add column if not exists workflow_id text;

alter table public.pipeline_runs
  add column if not exists provider_run_id bigint;

drop index if exists public.idx_runs_provider_id;
create unique index idx_runs_provider_id
  on public.pipeline_runs(pipeline_id, provider_run_id);

commit;
