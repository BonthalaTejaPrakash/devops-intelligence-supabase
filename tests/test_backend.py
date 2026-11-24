import os
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from backend import ci_providers, github_actions, main, supabase_adapter
from gotrue.errors import AuthApiError


class FakeResponse:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.filters = []
        self.single_result = False

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        self.filters.append(_args)
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def single(self):
        self.single_result = True
        return self

    def maybe_single(self):
        self.single_result = True
        return self

    def execute(self):
        filtered = [
            row for row in self.data
            if all(row.get(column) == value for column, value in self.filters)
        ]
        if self.single_result:
            return FakeResponse(filtered[0] if filtered else None)
        return FakeResponse(filtered, count=len(filtered))


class FakeDatabase:
    def table(self, _name):
        if _name == "pipeline_runs":
            return FakeQuery([
                {"id": "run-1", "status": "success", "pipeline_id": "pipeline-1"},
                {"id": "run-2", "status": "failed", "pipeline_id": "pipeline-1", "branch": "release/2.4"},
                {"id": "run-3", "status": "running", "pipeline_id": "pipeline-1", "provider_run_id": 987},
            ])
        if _name == "pipelines":
            return FakeQuery([{
                "id": "pipeline-1",
                "organization_id": "org-1",
                "provider": "GitHub Actions",
                "repo": "acme/platform",
                "branch": "main",
                "workflow_id": "deploy.yml",
                "has_workflow_dispatch": True,
            }])
        if _name == "profiles":
            return FakeQuery([{
                "id": "user-1",
                "organization_id": "org-1",
                "email": "developer@example.com",
                "email_verified": True,
                "role": "developer",
            }])
        return FakeQuery([])


def override_auth():
    return main.AuthContext(db=FakeDatabase(), user_id="user-1")


class BackendApiTests(unittest.TestCase):
    def tearDown(self):
        main.app.dependency_overrides.clear()

    def test_health_is_public(self):
        response = TestClient(main.app).get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "api": "v1"})

    def test_data_routes_require_a_supabase_token(self):
        response = TestClient(main.app).get("/api/v1/projects")
        self.assertEqual(response.status_code, 401)

    def test_metrics_counts_only_success_and_failure_in_success_rate(self):
        main.app.dependency_overrides[main.authenticated_db] = override_auth
        response = TestClient(main.app).get("/api/v1/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "successful": 1,
            "failed": 1,
            "running": 1,
            "cancelled": 0,
            "success_rate": 50.0,
        })

    def test_provider_actions_do_not_claim_success_without_adapters(self):
        main.app.dependency_overrides[main.authenticated_db] = override_auth
        with patch.dict(os.environ):
            for name in ("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_INSTALLATION_ID"):
                os.environ.pop(name, None)
            response = TestClient(main.app).post("/api/v1/pipelines/pipeline-1/run")
        self.assertEqual(response.status_code, 503)
        self.assertIn("GITHUB_APP_ID", response.json()["detail"])

    @patch.object(main.GitHubActions, "from_environment")
    def test_pipeline_actions_dispatch_only_after_user_scoped_pipeline_lookup(self, from_environment):
        main.app.dependency_overrides[main.authenticated_db] = override_auth
        provider = Mock()
        from_environment.return_value = provider
        client = TestClient(main.app)

        run = client.post("/api/v1/pipelines/pipeline-1/run")
        retry = client.post("/api/v1/pipelines/pipeline-1/retry", params={"run_id": "run-2"})
        cancel = client.post("/api/v1/pipelines/pipeline-1/runs/run-3/cancel")
        rollback = client.post("/api/v1/pipelines/pipeline-1/rollback", json={
            "target_commit_sha": "a" * 40,
            "target_workflow": "deploy.yml",
            "environment": "production",
            "confirm": True,
        })

        self.assertEqual([run.status_code, retry.status_code, cancel.status_code, rollback.status_code], [202] * 4)
        self.assertTrue(run.json()["accepted"])
        self.assertEqual(provider.dispatch_workflow.call_count, 3)
        self.assertEqual(provider.cancel_run.call_args.args, ("acme/platform", 987))

    @patch.object(main.CIProviders, "dispatch")
    @patch.object(main, "get_supabase_admin")
    def test_non_github_pipeline_dispatch_persists_provider_run(self, get_admin, dispatch):
        pipeline = {
            "id": "pipeline-jenkins",
            "organization_id": "org-1",
            "provider": "Jenkins",
            "repo": "acme/platform",
            "branch": "main",
            "workflow_id": "folder/build",
        }

        class JenkinsDatabase(FakeDatabase):
            def table(self, name):
                if name == "pipelines":
                    return FakeQuery([pipeline])
                return super().table(name)

        main.app.dependency_overrides[main.authenticated_db] = lambda: main.AuthContext(
            db=JenkinsDatabase(), user_id="user-1"
        )
        dispatch.return_value = ci_providers.ProviderRun("queue:18", "running", branch="main")
        saved = {
            "id": "run-row-jenkins",
            "pipeline_id": pipeline["id"],
            "provider_run_id": "queue:18",
            "status": "running",
        }
        run_query = Mock()
        run_query.upsert.return_value = run_query
        run_query.select.return_value = run_query
        run_query.single.return_value = run_query
        run_query.execute.return_value = FakeResponse(saved)
        admin = Mock()
        admin.table.return_value = run_query
        get_admin.return_value = admin

        response = TestClient(main.app).post(f"/api/v1/pipelines/{pipeline['id']}/run")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {
            "accepted": True, "action": "run", "provider": "Jenkins", "run": saved,
        })
        self.assertEqual(run_query.upsert.call_args.args[0]["organization_id"], "org-1")
        self.assertEqual(run_query.upsert.call_args.args[0]["provider_run_id"], "queue:18")

    @patch.object(main.GitHubActions, "from_environment")
    def test_viewer_cannot_dispatch_a_pipeline(self, from_environment):
        pipeline = {
            "id": "pipeline-1",
            "organization_id": "org-1",
            "provider": "GitHub Actions",
            "repo": "acme/platform",
            "branch": "main",
            "workflow_id": "deploy.yml",
            "has_workflow_dispatch": True,
        }

        class ViewerDatabase(FakeDatabase):
            def table(self, name):
                if name == "pipelines":
                    return FakeQuery([pipeline])
                if name == "profiles":
                    return FakeQuery([{
                        "id": "user-1", "organization_id": "org-1", "role": "viewer",
                    }])
                return super().table(name)

        main.app.dependency_overrides[main.authenticated_db] = lambda: main.AuthContext(
            db=ViewerDatabase(), user_id="user-1"
        )

        response = TestClient(main.app).post("/api/v1/pipelines/pipeline-1/run")

        self.assertEqual(response.status_code, 403)
        from_environment.assert_not_called()

    @patch.object(main.CIProviders, "sync")
    @patch.object(main, "get_supabase_admin")
    def test_manual_provider_sync_updates_run_status(self, get_admin, sync):
        pipeline = {
            "id": "pipeline-gitlab",
            "organization_id": "org-1",
            "provider": "GitLab CI/CD",
            "repo": "group/project",
            "branch": "main",
            "workflow_id": None,
            "has_workflow_dispatch": False,
        }
        running_run = {
            "id": "run-row-1", "pipeline_id": pipeline["id"],
            "provider_run_id": "321", "status": "running",
        }

        class GitLabDatabase(FakeDatabase):
            def table(self, name):
                if name == "pipelines":
                    return FakeQuery([pipeline])
                if name == "pipeline_runs":
                    return FakeQuery([running_run])
                return super().table(name)

        main.app.dependency_overrides[main.authenticated_db] = lambda: main.AuthContext(
            db=GitLabDatabase(), user_id="user-1"
        )
        sync.return_value = ci_providers.ProviderRun(
            "321", "success", run_number=11, branch="main", commit_sha="a" * 40,
        )
        update_query = Mock()
        update_query.update.return_value = update_query
        update_query.eq.return_value = update_query
        update_query.execute.return_value = FakeResponse([])
        admin = Mock()
        admin.table.return_value = update_query
        get_admin.return_value = admin

        response = TestClient(main.app).post("/api/v1/pipelines/pipeline-gitlab/sync")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"synced": 1, "provider": "GitLab CI/CD"})
        self.assertEqual(update_query.update.call_args.args[0]["status"], "success")

    def test_non_github_adapters_dispatch_using_server_credentials(self):
        responses = {
            "/build": httpx.Response(201, headers={"Location": "https://jenkins.example/queue/item/18/"}),
            "/project/gh/acme/platform/pipeline": httpx.Response(201, json={"id": "circle-pipeline"}),
            "/_apis/pipelines/42/runs": httpx.Response(200, json={
                "id": 887, "state": "inProgress", "createdDate": "2026-11-24T12:00:00Z",
            }),
            "/pipelines/": httpx.Response(201, json={
                "uuid": "{bb-run-1}", "state": {"name": "IN_PROGRESS"},
            }),
        }
        requests = []

        def responder(request):
            requests.append(request)
            if request.url.path.startswith("/api/v4/projects/") and request.url.path.endswith("/pipeline"):
                return httpx.Response(201, json={
                    "id": 321, "iid": 11, "status": "created", "ref": "main", "sha": "a" * 40,
                })
            for suffix, response in responses.items():
                if request.url.path.endswith(suffix):
                    return response
            return httpx.Response(404)

        environment = {
            "JENKINS_URL": "https://jenkins.example",
            "JENKINS_USER": "ci-user",
            "JENKINS_API_TOKEN": "jenkins-token",
            "CIRCLECI_TOKEN": "circle-token",
            "GITLAB_TOKEN": "gitlab-token",
            "AZURE_DEVOPS_ORG": "acme",
            "AZURE_DEVOPS_PROJECT": "platform",
            "AZURE_DEVOPS_PAT": "azure-token",
            "BITBUCKET_USERNAME": "ci-user",
            "BITBUCKET_API_TOKEN": "bitbucket-token",
        }
        pipelines = [
            {"provider": "Jenkins", "repo": "acme/platform", "name": "Build", "workflow_id": "folder/build", "branch": "main"},
            {"provider": "CircleCI", "repo": "acme/platform", "name": "Build", "workflow_id": None, "branch": "main"},
            {"provider": "GitLab CI/CD", "repo": "group/subgroup/project", "name": "Build", "workflow_id": None, "branch": "main"},
            {"provider": "Azure DevOps", "repo": "acme/platform", "name": "Build", "workflow_id": "42", "branch": "main"},
            {"provider": "Bitbucket Pipelines", "repo": "acme/platform", "name": "Build", "workflow_id": "deploy", "branch": "main"},
        ]
        client = httpx.Client(transport=httpx.MockTransport(responder))
        with patch.dict(os.environ, environment, clear=True):
            adapter = ci_providers.CIProviders(client)
            runs = [adapter.dispatch(pipeline) for pipeline in pipelines]

        self.assertEqual([run.provider_run_id for run in runs], [
            "queue:18", "circle-pipeline", "321", "887", "{bb-run-1}",
        ])
        self.assertTrue(all(run.status == "running" for run in runs))
        self.assertEqual(len(requests), 5)
        client.close()

    def test_webhook_fails_explicitly_when_secret_is_not_configured(self):
        with patch.dict(os.environ):
            os.environ.pop("GITHUB_WEBHOOK_SECRET", None)
            response = TestClient(main.app).post("/api/v1/webhooks/github", content=b"{}")
        self.assertEqual(response.status_code, 503)

    @patch.object(main.ResendNotifier, "from_environment")
    @patch.object(main, "get_supabase_admin")
    def test_notification_sends_to_signed_in_user_and_records_delivery(self, get_admin, from_environment):
        main.app.dependency_overrides[main.authenticated_db] = override_auth
        notifier = Mock()
        notifier.send.return_value = "resend-message-1"
        from_environment.return_value = notifier
        delivery_query = Mock()
        delivery_query.insert.return_value = delivery_query
        delivery_query.execute.return_value = FakeResponse({"id": "delivery-1"})
        admin = Mock()
        admin.table.return_value = delivery_query
        get_admin.return_value = admin

        response = TestClient(main.app).post("/api/v1/notifications/send", json={
            "event": "deployment_failed",
            "severity": "high",
            "project": "Platform",
            "message": "Build failed.",
        })

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["provider"], "Resend")
        self.assertEqual(notifier.send.call_args.args[:3], (
            "developer@example.com",
            "[HIGH] Platform: deployment_failed",
            "Build failed.",
        ))
        recorded = delivery_query.insert.call_args.args[0]
        self.assertEqual(recorded["organization_id"], "org-1")
        self.assertEqual(recorded["status"], "sent")
        self.assertEqual(recorded["provider"], "Resend")

    def test_logs_are_redacted_before_returning(self):
        response = TestClient(main.app).post(
            "/api/v1/logs/redact",
            json={"text": "Authorization: Bearer abc.def.ghi"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("abc.def.ghi", response.json()["redacted"])

    @patch.object(supabase_adapter, "create_client")
    def test_authenticated_client_uses_anon_key_and_callers_jwt(self, create_client):
        client = Mock()
        client.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(id="user-1"))
        create_client.return_value = client
        with patch.object(supabase_adapter, "SUPABASE_URL", "https://example.supabase.co"), patch.object(
            supabase_adapter, "SUPABASE_ANON_KEY", "public-anon-key"
        ):
            authenticated, user_id = supabase_adapter.get_authenticated_client("user-access-token")

        self.assertIs(authenticated, client)
        self.assertEqual(user_id, "user-1")
        create_client.assert_called_once_with("https://example.supabase.co", "public-anon-key")
        client.auth.get_user.assert_called_once_with("user-access-token")
        client.postgrest.auth.assert_called_once_with("user-access-token")

    @patch.object(supabase_adapter, "create_client")
    def test_authenticated_client_rejects_invalid_jwt(self, create_client):
        client = Mock()
        client.auth.get_user.side_effect = AuthApiError("invalid token", 401, "bad_jwt")
        create_client.return_value = client
        with patch.object(supabase_adapter, "SUPABASE_URL", "https://example.supabase.co"), patch.object(
            supabase_adapter, "SUPABASE_ANON_KEY", "public-anon-key"
        ):
            with self.assertRaises(supabase_adapter.InvalidAccessToken):
                supabase_adapter.get_authenticated_client("invalid-token")

    @patch("backend.github_actions.jwt.encode", return_value="signed-app-token")
    def test_github_app_dispatch_requests_installation_token_then_dispatches(self, _encode):
        requests = []

        def responder(request):
            requests.append(request)
            if request.url.path.endswith("/access_tokens"):
                return httpx.Response(201, json={"token": "installation-token"})
            return httpx.Response(204)

        client = httpx.Client(transport=httpx.MockTransport(responder))
        adapter = github_actions.GitHubActions("app-1", "private-key", "install-1", client)
        adapter.dispatch_workflow("acme/platform", "deploy.yml", "main")

        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].headers["Authorization"], "Bearer signed-app-token")
        self.assertEqual(requests[1].headers["Authorization"], "Bearer installation-token")
        self.assertEqual(requests[1].url.path, "/repos/acme/platform/actions/workflows/deploy.yml/dispatches")
        self.assertEqual(json.loads(requests[1].read()), {"ref": "main", "inputs": {}})
        client.close()

    def test_github_adapter_rejects_untrusted_repository_path(self):
        adapter = github_actions.GitHubActions("app-1", "private-key", "install-1")
        with self.assertRaisesRegex(ValueError, "owner/repository"):
            adapter.dispatch_workflow("https://attacker.example/acme/platform", "deploy.yml", "main")

    @patch.object(main.GitHubActions, "from_environment")
    @patch.object(main, "get_supabase_admin")
    def test_signed_github_workflow_event_upserts_real_run_state(self, get_admin, from_environment):
        pipeline_query = Mock()
        pipeline_query.select.return_value = pipeline_query
        pipeline_query.eq.return_value = pipeline_query
        pipeline_query.execute.return_value = FakeResponse([{
            "id": "pipeline-1",
            "organization_id": "org-1",
            "workflow_id": "deploy.yml",
        }])

        existing_query = Mock()
        existing_query.select.return_value = existing_query
        existing_query.eq.return_value = existing_query
        existing_query.maybe_single.return_value = existing_query
        existing_query.execute.return_value = FakeResponse(None)

        run_query = Mock()
        run_query.upsert.return_value = run_query
        run_query.select.return_value = run_query
        run_query.single.return_value = run_query
        run_query.execute.return_value = FakeResponse({
            "id": "run-row-1",
            "pipeline_id": "pipeline-1",
            "provider_run_id": "321",
            "status": "running",
        })

        admin = Mock()
        admin.table.side_effect = [pipeline_query, existing_query, run_query]
        get_admin.return_value = admin
        provider = Mock()
        provider.list_workflows.return_value = [{
            "id": 654,
            "path": ".github/workflows/deploy.yml",
        }]
        from_environment.return_value = provider

        import hashlib
        import hmac
        payload = {
            "repository": {"full_name": "acme/platform"},
            "workflow_run": {
                "id": 321,
                "workflow_id": 654,
                "status": "in_progress",
                "head_branch": "main",
                "head_sha": "a" * 40,
                "run_number": 12,
                "created_at": "2026-11-24T12:00:00Z",
            },
        }
        body = json.dumps(payload).encode()
        secret = "webhook-secret"
        signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": secret}):
            response = TestClient(main.app).post(
                "/api/v1/webhooks/github",
                content=body,
                headers={"X-GitHub-Event": "workflow_run", "X-Hub-Signature-256": signature},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["run"]["provider_run_id"], "321")
        stored = run_query.upsert.call_args.args[0]
        self.assertEqual(stored["pipeline_id"], "pipeline-1")
        self.assertEqual(stored["status"], "running")
        self.assertEqual(stored["provider_run_id"], "321")
        provider.list_workflows.assert_called_once_with("acme/platform")

    @patch("backend.github_actions.jwt.encode", return_value="signed-app-token")
    def test_github_adapter_lists_workflows_for_webhook_mapping(self, _encode):
        requests = []

        def responder(request):
            requests.append(request)
            if request.url.path.endswith("/access_tokens"):
                return httpx.Response(201, json={"token": "installation-token"})
            return httpx.Response(200, json={"workflows": [{"id": 654, "path": ".github/workflows/deploy.yml"}]})

        client = httpx.Client(transport=httpx.MockTransport(responder))
        adapter = github_actions.GitHubActions("app-1", "private-key", "install-1", client)

        workflows = adapter.list_workflows("acme/platform")

        self.assertEqual(workflows, [{"id": 654, "path": ".github/workflows/deploy.yml"}])
        self.assertEqual(requests[-1].url.path, "/repos/acme/platform/actions/workflows")
        self.assertEqual(requests[-1].url.params["per_page"], "100")
        client.close()
