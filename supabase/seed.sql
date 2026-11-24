-- Seed — SAMPLE data only, clearly labeled. Delete with: delete from public.projects where name like 'Sample%';
insert into public.organizations (name, slug, plan) values ('Sample Org','sample-org','free') on conflict (slug) do nothing;
