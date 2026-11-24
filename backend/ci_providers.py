import base64
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote, urlsplit

import httpx


class CIProviderConfigurationError(RuntimeError):
    pass


class CIProviderError(RuntimeError):
    pass


@dataclass
class ProviderRun:
    provider_run_id: str
    status: str
    run_number: Optional[int] = None
    branch: Optional[str] = None
    commit_sha: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


class CIProviders:
    def __init__(self, client: Optional[httpx.Client] = None):
        self.client = client or httpx.Client(timeout=20)

    @staticmethod
    def _required(name: str) -> str:
        value = os.getenv(name, "").strip()
        if not value:
            raise CIProviderConfigurationError(f"Set {name} on the backend to use this provider.")
        return value

    @staticmethod
    def _repo(repo: str) -> Tuple[str, str]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or ""):
            raise ValueError("This provider requires repository in owner/repository format.")
        owner, repository = repo.split("/", 1)
        return owner, repository

    @staticmethod
    def _job_path(value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value or ""):
            raise ValueError("Jenkins pipeline identifier must be a safe job path.")
        return "/".join(f"job/{quote(part, safe='')}" for part in value.split("/"))

    @staticmethod
    def _base_url(value: str, name: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise CIProviderConfigurationError(f"{name} must be an HTTP(S) URL without credentials or query parameters.")
        return value.rstrip("/")

    @staticmethod
    def _provider_id(value: Any) -> str:
        if isinstance(value, (str, int)) and str(value):
            return str(value)
        raise CIProviderError("The provider returned an invalid run identifier.")

    def _request(
        self,
        method: str,
        url: str,
        *,
        expected: Tuple[int, ...],
        provider: str,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            response = self.client.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise CIProviderError(f"{provider} request could not reach the provider.") from error
        if response.status_code not in expected:
            raise CIProviderError(f"{provider} request failed with HTTP {response.status_code}.")
        return response

    def dispatch(self, pipeline: Dict[str, Any]) -> ProviderRun:
        provider = pipeline["provider"]
        if provider == "Jenkins":
            return self._dispatch_jenkins(pipeline)
        if provider == "CircleCI":
            return self._dispatch_circleci(pipeline)
        if provider == "GitLab CI/CD":
            return self._dispatch_gitlab(pipeline)
        if provider == "Azure DevOps":
            return self._dispatch_azure(pipeline)
        if provider == "Bitbucket Pipelines":
            return self._dispatch_bitbucket(pipeline)
        raise ValueError(f"Unsupported CI provider: {provider}")

    def _dispatch_jenkins(self, pipeline: Dict[str, Any]) -> ProviderRun:
        base = self._base_url(self._required("JENKINS_URL"), "JENKINS_URL")
        auth = (self._required("JENKINS_USER"), self._required("JENKINS_API_TOKEN"))
        path = self._job_path(pipeline.get("workflow_id") or pipeline["name"])
        response = self._request(
            "POST", f"{base}/{path}/build",
            expected=(201, 302), provider="Jenkins", auth=auth,
        )
        location = response.headers.get("Location", "")
        match = re.search(r"/queue/item/([0-9]+)/?$", urlsplit(location).path)
        if not match:
            raise CIProviderError("Jenkins accepted the build but returned no queue identifier.")
        return ProviderRun(f"queue:{match.group(1)}", "running", branch=pipeline.get("branch"))

    def _dispatch_circleci(self, pipeline: Dict[str, Any]) -> ProviderRun:
        owner, repo = self._repo(pipeline["repo"])
        token = self._required("CIRCLECI_TOKEN")
        response = self._request(
            "POST", f"https://circleci.com/api/v2/project/gh/{owner}/{repo}/pipeline",
            expected=(201,), provider="CircleCI",
            headers={"Circle-Token": token},
            json={"branch": pipeline.get("branch") or "main"},
        )
        payload = response.json()
        run_id = self._provider_id(payload.get("id"))
        return ProviderRun(run_id, "running", branch=pipeline.get("branch"))

    def _gitlab(self) -> Tuple[str, str]:
        base = self._base_url(os.getenv("GITLAB_URL", "https://gitlab.com"), "GITLAB_URL")
        return base, self._required("GITLAB_TOKEN")

    def _dispatch_gitlab(self, pipeline: Dict[str, Any]) -> ProviderRun:
        base, token = self._gitlab()
        project = quote(pipeline["repo"], safe="")
        response = self._request(
            "POST", f"{base}/api/v4/projects/{project}/pipeline",
            expected=(201,), provider="GitLab",
            headers={"PRIVATE-TOKEN": token},
            json={"ref": pipeline.get("branch") or "main"},
        )
        payload = response.json()
        return ProviderRun(
            self._provider_id(payload.get("id")),
            self._gitlab_status(payload.get("status")),
            run_number=payload.get("iid"),
            branch=payload.get("ref"),
            commit_sha=payload.get("sha"),
            started_at=payload.get("created_at"),
            ended_at=payload.get("finished_at"),
        )

    def _azure_settings(self) -> Tuple[str, str, str, str]:
        organization = self._required("AZURE_DEVOPS_ORG")
        project = self._required("AZURE_DEVOPS_PROJECT")
        token = self._required("AZURE_DEVOPS_PAT")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", organization) or not re.fullmatch(r"[A-Za-z0-9_. -]+", project):
            raise CIProviderConfigurationError("Azure DevOps organization or project name is invalid.")
        return organization, project, token, base64.b64encode(f":{token}".encode()).decode()

    def _dispatch_azure(self, pipeline: Dict[str, Any]) -> ProviderRun:
        organization, project, _token, encoded = self._azure_settings()
        pipeline_id = pipeline.get("workflow_id")
        if not isinstance(pipeline_id, str) or not pipeline_id.isdigit():
            raise ValueError("Azure DevOps pipeline identifier must be a numeric pipeline ID.")
        url = (
            f"https://dev.azure.com/{quote(organization, safe='')}/{quote(project, safe='')}"
            f"/_apis/pipelines/{pipeline_id}/runs?api-version=7.1"
        )
        branch = pipeline.get("branch") or "main"
        response = self._request(
            "POST", url, expected=(200, 201), provider="Azure DevOps",
            headers={"Authorization": f"Basic {encoded}"},
            json={"resources": {"repositories": {"self": {"refName": f"refs/heads/{branch}"}}}},
        )
        payload = response.json()
        return ProviderRun(
            self._provider_id(payload.get("id")),
            self._azure_status(payload.get("state"), payload.get("result")),
            run_number=payload.get("id"),
            branch=branch,
            commit_sha=(payload.get("resources", {}).get("repositories", {}).get("self", {}).get("version")),
            started_at=payload.get("createdDate"),
            ended_at=payload.get("finishedDate"),
        )

    def _bitbucket_repo(self, pipeline: Dict[str, Any]) -> Tuple[str, str, str, str]:
        username = self._required("BITBUCKET_USERNAME")
        api_token = self._required("BITBUCKET_API_TOKEN")
        workspace, repo = self._repo(pipeline["repo"])
        return username, api_token, workspace, repo

    def _dispatch_bitbucket(self, pipeline: Dict[str, Any]) -> ProviderRun:
        username, api_token, workspace, repo = self._bitbucket_repo(pipeline)
        branch = pipeline.get("branch") or "main"
        target: Dict[str, Any] = {
            "type": "pipeline_ref_target",
            "ref_type": "branch",
            "ref_name": branch,
        }
        workflow = pipeline.get("workflow_id")
        if workflow:
            target["selector"] = {"type": "custom", "pattern": workflow}
        response = self._request(
            "POST", f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pipelines/",
            expected=(201,), provider="Bitbucket",
            auth=(username, api_token),
            json={"target": target},
        )
        payload = response.json()
        return ProviderRun(
            self._provider_id(payload.get("uuid")),
            self._bitbucket_status(payload.get("state")),
            branch=branch,
            commit_sha=(payload.get("target", {}).get("commit", {}).get("hash")),
            started_at=payload.get("created_on"),
            ended_at=payload.get("completed_on"),
        )

    @staticmethod
    def _gitlab_status(status: Any) -> str:
        if status in {"success"}:
            return "success"
        if status in {"failed"}:
            return "failed"
        if status in {"canceled", "cancelled", "skipped"}:
            return "cancelled"
        if status in {"created", "pending", "preparing", "scheduled", "running", "manual", "waiting_for_resource"}:
            return "running"
        raise CIProviderError("GitLab returned an unsupported pipeline status.")

    @staticmethod
    def _azure_status(state: Any, result: Any) -> str:
        if state != "completed":
            return "running"
        if result == "succeeded":
            return "success"
        if result in {"canceled", "cancelled"}:
            return "cancelled"
        return "failed"

    @staticmethod
    def _bitbucket_status(state: Any) -> str:
        if not isinstance(state, dict):
            raise CIProviderError("Bitbucket returned an invalid pipeline state.")
        state_name = state.get("name")
        result = state.get("result")
        result_name = result.get("name") if isinstance(result, dict) else None
        if state_name == "COMPLETED":
            if result_name == "SUCCESSFUL":
                return "success"
            if result_name == "FAILED":
                return "failed"
            if result_name in {"STOPPED", "ERROR"}:
                return "cancelled" if result_name == "STOPPED" else "failed"
            raise CIProviderError("Bitbucket returned an unsupported completed result.")
        if state_name in {"IN_PROGRESS", "PENDING"}:
            return "running"
        raise CIProviderError("Bitbucket returned an unsupported pipeline state.")

    def cancel(self, pipeline: Dict[str, Any], provider_run_id: str) -> None:
        provider = pipeline["provider"]
        if provider == "Jenkins":
            self._cancel_jenkins(pipeline, provider_run_id)
        elif provider == "CircleCI":
            self._cancel_circleci(provider_run_id)
        elif provider == "GitLab CI/CD":
            self._cancel_gitlab(pipeline, provider_run_id)
        elif provider == "Azure DevOps":
            self._cancel_azure(provider_run_id)
        elif provider == "Bitbucket Pipelines":
            self._cancel_bitbucket(pipeline, provider_run_id)
        else:
            raise ValueError(f"Unsupported CI provider: {provider}")

    def _cancel_jenkins(self, pipeline: Dict[str, Any], run_id: str) -> None:
        base = self._base_url(self._required("JENKINS_URL"), "JENKINS_URL")
        auth = (self._required("JENKINS_USER"), self._required("JENKINS_API_TOKEN"))
        if run_id.startswith("queue:") and run_id[6:].isdigit():
            url = f"{base}/queue/cancelItem?id={run_id[6:]}"
        elif run_id.startswith("build:") and run_id[6:].isdigit():
            path = self._job_path(pipeline.get("workflow_id") or pipeline["name"])
            url = f"{base}/{path}/{run_id[6:]}/stop"
        else:
            raise ValueError("Jenkins run identifier is invalid.")
        self._request("POST", url, expected=(200, 302), provider="Jenkins", auth=auth)

    def _cancel_circleci(self, pipeline_id: str) -> None:
        token = self._required("CIRCLECI_TOKEN")
        workflows = self._request(
            "GET", f"https://circleci.com/api/v2/pipeline/{quote(pipeline_id, safe='')}/workflow",
            expected=(200,), provider="CircleCI", headers={"Circle-Token": token},
        ).json().get("items", [])
        running = [item for item in workflows if item.get("status") in {"running", "on_hold"}]
        if not running:
            raise CIProviderError("CircleCI returned no active workflows for this pipeline.")
        for item in running:
            self._request(
                "POST", f"https://circleci.com/api/v2/workflow/{quote(str(item['id']), safe='')}/cancel",
                expected=(202,), provider="CircleCI", headers={"Circle-Token": token},
            )

    def _cancel_gitlab(self, pipeline: Dict[str, Any], pipeline_id: str) -> None:
        base, token = self._gitlab()
        self._request(
            "POST", f"{base}/api/v4/projects/{quote(pipeline['repo'], safe='')}/pipelines/{quote(pipeline_id, safe='')}/cancel",
            expected=(200,), provider="GitLab", headers={"PRIVATE-TOKEN": token},
        )

    def _cancel_azure(self, run_id: str) -> None:
        if not run_id.isdigit():
            raise ValueError("Azure DevOps run identifier is invalid.")
        organization, project, _token, encoded = self._azure_settings()
        self._request(
            "PATCH",
            f"https://dev.azure.com/{quote(organization, safe='')}/{quote(project, safe='')}/_apis/build/builds/{run_id}?api-version=7.1",
            expected=(200,), provider="Azure DevOps",
            headers={"Authorization": f"Basic {encoded}"},
            json={"status": "Cancelling"},
        )

    def _cancel_bitbucket(self, pipeline: Dict[str, Any], run_id: str) -> None:
        username, api_token, workspace, repo = self._bitbucket_repo(pipeline)
        self._request(
            "POST",
            f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pipelines/{quote(run_id, safe='')}/stopPipeline/",
            expected=(200, 202), provider="Bitbucket", auth=(username, api_token),
        )

    def sync(self, pipeline: Dict[str, Any], provider_run_id: str) -> ProviderRun:
        provider = pipeline["provider"]
        if provider == "GitLab CI/CD":
            return self._sync_gitlab(pipeline, provider_run_id)
        if provider == "Azure DevOps":
            return self._sync_azure(pipeline, provider_run_id)
        if provider == "Bitbucket Pipelines":
            return self._sync_bitbucket(pipeline, provider_run_id)
        if provider == "CircleCI":
            return self._sync_circleci(provider_run_id)
        if provider == "Jenkins":
            return self._sync_jenkins(pipeline, provider_run_id)
        raise ValueError(f"Unsupported CI provider: {provider}")

    def _sync_gitlab(self, pipeline: Dict[str, Any], run_id: str) -> ProviderRun:
        base, token = self._gitlab()
        project = quote(pipeline["repo"], safe="")
        payload = self._request(
            "GET", f"{base}/api/v4/projects/{project}/pipelines/{quote(run_id, safe='')}",
            expected=(200,), provider="GitLab", headers={"PRIVATE-TOKEN": token},
        ).json()
        return ProviderRun(
            run_id, self._gitlab_status(payload.get("status")),
            run_number=payload.get("iid"), branch=payload.get("ref"),
            commit_sha=payload.get("sha"), started_at=payload.get("created_at"),
            ended_at=payload.get("finished_at"),
        )

    def _sync_azure(self, pipeline: Dict[str, Any], run_id: str) -> ProviderRun:
        organization, project, _token, encoded = self._azure_settings()
        pipeline_id = pipeline.get("workflow_id")
        if not isinstance(pipeline_id, str) or not pipeline_id.isdigit() or not run_id.isdigit():
            raise ValueError("Azure DevOps pipeline or run identifier is invalid.")
        payload = self._request(
            "GET",
            f"https://dev.azure.com/{quote(organization, safe='')}/{quote(project, safe='')}/_apis/pipelines/{pipeline_id}/runs/{run_id}?api-version=7.1",
            expected=(200,), provider="Azure DevOps",
            headers={"Authorization": f"Basic {encoded}"},
        ).json()
        repository = payload.get("resources", {}).get("repositories", {}).get("self", {})
        return ProviderRun(
            run_id, self._azure_status(payload.get("state"), payload.get("result")),
            run_number=payload.get("id"),
            branch=(
                repository.get("refName", "").removeprefix("refs/heads/") or None
                if isinstance(repository.get("refName"), str) else None
            ),
            commit_sha=repository.get("version"),
            started_at=payload.get("createdDate"), ended_at=payload.get("finishedDate"),
        )

    def _sync_bitbucket(self, pipeline: Dict[str, Any], run_id: str) -> ProviderRun:
        username, api_token, workspace, repo = self._bitbucket_repo(pipeline)
        payload = self._request(
            "GET",
            f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}/pipelines/{quote(run_id, safe='')}",
            expected=(200,), provider="Bitbucket", auth=(username, api_token),
        ).json()
        target = payload.get("target", {})
        commit = target.get("commit", {})
        return ProviderRun(
            run_id, self._bitbucket_status(payload.get("state")),
            branch=target.get("ref_name"), commit_sha=commit.get("hash"),
            started_at=payload.get("created_on"), ended_at=payload.get("completed_on"),
        )

    def _sync_circleci(self, pipeline_id: str) -> ProviderRun:
        token = self._required("CIRCLECI_TOKEN")
        items = self._request(
            "GET", f"https://circleci.com/api/v2/pipeline/{quote(pipeline_id, safe='')}/workflow",
            expected=(200,), provider="CircleCI", headers={"Circle-Token": token},
        ).json().get("items", [])
        if not items:
            return ProviderRun(pipeline_id, "running")
        item = items[-1]
        status = item.get("status")
        mapped = "success" if status == "success" else (
            "failed" if status == "failed" else (
                "cancelled" if status == "canceled" else "running"
            )
        )
        return ProviderRun(
            pipeline_id, mapped, branch=item.get("tag"),
            started_at=item.get("created_at"), ended_at=item.get("stopped_at"),
        )

    def _sync_jenkins(self, pipeline: Dict[str, Any], run_id: str) -> ProviderRun:
        base = self._base_url(self._required("JENKINS_URL"), "JENKINS_URL")
        auth = (self._required("JENKINS_USER"), self._required("JENKINS_API_TOKEN"))
        if run_id.startswith("queue:") and run_id[6:].isdigit():
            queue = self._request(
                "GET", f"{base}/queue/item/{run_id[6:]}/api/json",
                expected=(200,), provider="Jenkins", auth=auth,
            ).json()
            if queue.get("cancelled"):
                return ProviderRun(run_id, "cancelled", branch=pipeline.get("branch"))
            executable = queue.get("executable")
            if not isinstance(executable, dict) or not isinstance(executable.get("number"), int):
                return ProviderRun(run_id, "running", branch=pipeline.get("branch"))
            build_number = executable["number"]
        elif run_id.startswith("build:") and run_id[6:].isdigit():
            build_number = int(run_id[6:])
        else:
            raise ValueError("Jenkins run identifier is invalid.")
        build = self._request(
            "GET",
            f"{base}/{self._job_path(pipeline.get('workflow_id') or pipeline['name'])}/{build_number}/api/json",
            expected=(200,), provider="Jenkins", auth=auth,
        ).json()
        result = build.get("result")
        status = "running" if build.get("building") else (
            "success" if result == "SUCCESS" else "cancelled" if result == "ABORTED" else "failed"
        )
        normalized_id = f"build:{build_number}"
        return ProviderRun(
            normalized_id, status, run_number=build_number, branch=pipeline.get("branch"),
            started_at=datetime.fromtimestamp(build["timestamp"] / 1000, tz=timezone.utc).isoformat()
            if isinstance(build.get("timestamp"), (int, float)) else None,
            ended_at=datetime.fromtimestamp((build["timestamp"] + build["duration"]) / 1000, tz=timezone.utc).isoformat()
            if not build.get("building") and isinstance(build.get("timestamp"), (int, float))
            and isinstance(build.get("duration"), (int, float)) else None,
        )
