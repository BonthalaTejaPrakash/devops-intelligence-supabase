# Connect DevOps Intelligence to Supabase

Email/password sign-in and **Main Projects** use Supabase Auth and the Postgres Data API directly from the browser. New projects are private to the account that creates them. The authenticated FastAPI API supports GitHub Actions, Jenkins, CircleCI, GitLab CI/CD, Azure DevOps, Bitbucket Pipelines, and Resend notifications when server credentials are configured. Some legacy dashboard panels outside the live workspace remain demo-oriented.

## 1. Prepare the database

Use a development database for your first verification. In your hosted Supabase SQL Editor or your self-hosted Supabase Studio, run the following scripts in order.

1. On a fresh database, run `supabase/schema.sql`. If the original project's schema is already present, skip this step.
2. Run `supabase/migrations/20261123000100_project_workspace.sql`. This adds project ownership and access policies without deleting existing project rows.
3. Run `supabase/migrations/20261123000200_rls_policies.sql` and `supabase/migrations/20261123000300_auth_profiles.sql` to add organization read policies and create profiles for Auth users.
4. Run `supabase/migrations/20261123000400_org_bootstrap.sql` to enable authenticated users to create their first organization safely.
5. Run `supabase/migrations/20261123000500_github_workflow_dispatch.sql` to add workflow identifiers and idempotent GitHub run tracking.
6. Run `supabase/migrations/20261123000600_multi_provider_run_ids.sql` to support external run identifiers from all CI providers.
7. Run `supabase/migrations/20261123000700_sync_auth_profile_email.sql` to keep profile email verification current for notifications.

If you already manage this database through migration history, use your normal migration workflow. If the original schema migration is already recorded, apply any of the project workspace, RLS policy, Auth profile, organization bootstrap, GitHub workflow, multi-provider run ID, and Auth email synchronization migrations that are not yet recorded.

RLS is enabled on every application table. Policies scope data by organization, limit personal project access to its owner, and restrict writes by role. The new migrations backfill profiles for existing Auth users, create profiles on signup, and provide a safe organization setup RPC.

## 2. Configure the website

Copy `supabase.local.example.json` to `supabase.local.json` in the same folder as `index.html` (for example, run `cp supabase.local.example.json supabase.local.json` from the project directory). This ignored local file is required. Set `url` and `publicKey` to the Supabase API URL and publishable/anon key. Set `apiUrl` to the FastAPI origin; keep it separate from the Supabase URL.

- **url** is your Supabase API address. For a local Docker self-hosted installation, the API gateway often uses `http://localhost:8000`. Use the actual gateway address reachable from your browser. A hosted project uses its `https://…supabase.co` project URL.
- **publicKey** is the Supabase publishable key or the legacy anon key. For self-hosted installs with legacy keys, use `ANON_KEY` from the self-hosted stack. Keep the service-role key and secret keys off the website.

For signup email confirmation, set the Auth Site URL and allowed redirects to your website URL. Use the matching settings in your hosted project or self-hosted Auth configuration. Existing test accounts can sign in with their current email and password.

The static app reads this JSON file; it does not read the Vite `VITE_SUPABASE_*` variables in `src/supabaseClient.js` or `src/supabaseClient.ts`. Never put a service-role or secret key in the browser config.

## 3. Open and verify

In PowerShell, from the folder containing `index.html`, run:

```powershell
python -m http.server --bind 127.0.0.1 8001
```

Open [DevOps Intelligence](http://127.0.0.1:8001/). The browser loads the pinned Supabase JavaScript SDK from jsDelivr, so it needs internet access to that CDN.

1. Sign in with a confirmed test account or create an account and follow the confirmation instructions.
2. Open **Main Projects** → **Add project**, enter a name, and click **Save project**.
3. Refresh. The session should restore and the same project should load. You can also check its row in the Supabase Table Editor under `public.projects`.
4. Sign out and check with a second test account. A project created by the first account should stay private to it.

If Supabase is configured but saving returns a database error, verify that the project workspace migration ran in the same database as the API URL.

For static hosting, deploy `index.html`, the `src/` modules, and the configured `supabase.local.json` together. The browser config belongs in the deployment output and is ignored by Git. Keep server credentials and Docker or database environment files off the static host. The GitHub workflow deploys only from `main` or a manual dispatch; protect its `production` environment and store the Supabase deployment secrets there.

## 5. Free GitHub Pages deployment

The repository includes `.github/workflows/github-pages.yml`. It deploys the static app to GitHub Pages and generates `supabase.local.json` using the public Supabase URL and publishable key; no server secret is included.

In **Settings → Pages**, set **Source** to **GitHub Actions**. Push to `main` or run the workflow manually. The public app URL is shown in the workflow's `github-pages` environment.

The signed-in **Main Projects** page includes a live Supabase workspace for projects, pipelines, runs, logs, reminders, integrations, notification history/preferences, audit records, and metrics. GitHub Actions run/retry/cancel/rollback dispatches through the authenticated FastAPI API; a signed `workflow_run` webhook synchronizes its actual run status. Jenkins, CircleCI, GitLab CI/CD, Azure DevOps, and Bitbucket Pipelines also support run/retry/cancel and manual status synchronization when their server-side credentials are configured. The Notifications tab sends a real transactional email to the signed-in user's address through Resend; it does not broadcast to every organization member.

To run the API locally, configure the Supabase variables, `FRONTEND_URL`, GitHub App variables, and the credentials for each CI/notification provider you use in the backend process environment, then run `uvicorn backend.main:app --reload --port 8080`. Jenkins pipelines use the saved workflow identifier as a Jenkins job path. CircleCI derives the project slug from `owner/repository`. GitLab accepts a namespace/project path. Azure DevOps uses a numeric pipeline ID plus `AZURE_DEVOPS_ORG` and `AZURE_DEVOPS_PROJECT`. Bitbucket uses a workspace/repository and may use a custom pipeline name; configure a Bitbucket API token rather than a deprecated app password. Set Resend's `RESEND_API_KEY` and a verified `NOTIFICATION_FROM_EMAIL` to enable email.

## 4. Deploy the FastAPI backend

The repository includes a Docker deployment definition and `render.yaml`. Deploy the repository root to a container host or Render Blueprint. Configure these environment variables on the backend host; never put them in `supabase.local.json`:

- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`
- `FRONTEND_URL` as the exact static-app origin
- GitHub App/provider credentials only for providers you use
- `RESEND_API_KEY` and `NOTIFICATION_FROM_EMAIL` for email

After deployment, set `apiUrl` in `supabase.local.json` to the backend's public HTTPS origin. Deploy `index.html`, `src/`, and `supabase.local.json` together to the static host. The Docker image does not bundle browser configuration or server secrets.

Configure GitHub App permissions for Actions read/write and Metadata read, and set its webhook URL to `/api/v1/webhooks/github` with the `workflow_run` event selected. Keep all provider tokens, the GitHub private key, webhook secret, and Supabase service-role key on the server only. User-facing database reads use the caller's access token; the service-role key is used only after authenticated provider actions and for webhook/delivery persistence.
