
"""
DevOps Intelligence — Backend + Supabase adapter
Frontend uses SUPABASE_ANON_KEY only. Backend uses SUPABASE_SERVICE_ROLE_KEY (server only, never in frontend).
The static frontend uses src/workspace.mjs with supabase.local.json.
"""
import os
from gotrue.errors import AuthApiError
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()


def configured_secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.startswith("YOUR_") or value.startswith("CHANGE_ME"):
        return ""
    return value


SUPABASE_URL = configured_secret("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = configured_secret("SUPABASE_SERVICE_ROLE_KEY")  # SERVER ONLY
SUPABASE_ANON_KEY = configured_secret("SUPABASE_ANON_KEY")


class InvalidAccessToken(ValueError):
    pass


def get_authenticated_client(access_token: str) -> tuple[Client, str]:
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise RuntimeError("Set SUPABASE_URL + SUPABASE_ANON_KEY for authenticated API requests.")
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    try:
        user = client.auth.get_user(access_token).user
    except AuthApiError as error:
        raise InvalidAccessToken("Supabase access token is invalid or expired.") from error
    if not user:
        raise InvalidAccessToken("Supabase access token has no user.")
    client.postgrest.auth(access_token)
    return client, user.id


def get_supabase_admin() -> Client:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY in .env (server only)")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# Example: fetch pipelines for an org (RLS bypassed only because service_role is server-side)
# db = get_supabase_admin()
# res = db.table("pipelines").select("*").eq("organization_id", org_id).execute()
# Example: insert redacted log (redact BEFORE insert — see main.py redact_logs)
# Example: verify Google email_verified before linking (see main.py require_email_verified)
