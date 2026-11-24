begin;

update public.profiles profiles
set email = coalesce(users.email, ''),
    email_verified = users.email_confirmed_at is not null
from auth.users users
where profiles.id = users.id;

create or replace function public.sync_auth_user_profile()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.profiles
  set email = coalesce(new.email, ''),
      email_verified = new.email_confirmed_at is not null
  where id = new.id;
  return new;
end;
$$;

drop trigger if exists on_auth_user_profile_updated on auth.users;
create trigger on_auth_user_profile_updated
  after update of email, email_confirmed_at on auth.users
  for each row execute function public.sync_auth_user_profile();

commit;
