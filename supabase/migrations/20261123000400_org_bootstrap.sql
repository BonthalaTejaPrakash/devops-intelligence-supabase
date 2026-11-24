begin;

create or replace function public.create_workspace_organization(p_name text)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := auth.uid();
  cleaned_name text := nullif(trim(p_name), '');
  new_organization_id uuid;
  base_slug text;
begin
  if current_user_id is null then
    raise exception 'Authentication is required';
  end if;
  if cleaned_name is null or length(cleaned_name) > 120 then
    raise exception 'Organization name must contain 1 to 120 characters';
  end if;

  perform 1 from public.profiles
    where id = current_user_id
    for update;
  if not found then
    raise exception 'User profile is not ready; apply the Auth profile migration first';
  end if;

  if exists (
    select 1 from public.profiles
    where id = current_user_id and organization_id is not null
  ) then
    raise exception 'User already belongs to an organization';
  end if;

  base_slug := trim(both '-' from regexp_replace(lower(cleaned_name), '[^a-z0-9]+', '-', 'g'));
  if base_slug = '' then
    base_slug := 'workspace';
  end if;

  insert into public.organizations (name, slug)
  values (cleaned_name, base_slug || '-' || left(gen_random_uuid()::text, 8))
  returning id into new_organization_id;

  update public.profiles
  set organization_id = new_organization_id, role = 'owner'
  where id = current_user_id;

  return new_organization_id;
end;
$$;

revoke all on function public.create_workspace_organization(text) from public, anon;
grant execute on function public.create_workspace_organization(text) to authenticated;

commit;
