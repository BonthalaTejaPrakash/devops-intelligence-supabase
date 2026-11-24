import os
import re
import time
from typing import Dict, List, Optional
from urllib.parse import quote

import httpx
import jwt
from jwt.exceptions import PyJWTError

GITHUB_API = "https://api.github.com"
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKFLOW_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
REF_PATTERN = re.compile(r"^[A-Za-z0-9_./-]{1,255}$")


class GitHubConfigurationError(RuntimeError):
    pass


class GitHubApiError(RuntimeError):
    pass


class GitHubActions:
    def __init__(
        self,
        app_id: str,
        private_key: str,
        installation_id: str,
        client: Optional[httpx.Client] = None,
    ):
        self.app_id = app_id
        self.private_key = private_key.replace("\\n", "\n")
        self.installation_id = installation_id
        self.client = client or httpx.Client(timeout=15)

    @classmethod
    def from_environment(cls) -> "GitHubActions":
        app_id = os.getenv("GITHUB_APP_ID")
        private_key = os.getenv("GITHUB_APP_PRIVATE_KEY")
        installation_id = os.getenv("GITHUB_INSTALLATION_ID")
        if (
            not app_id or app_id.startswith("YOUR_")
            or not private_key or private_key.startswith("YOUR_")
            or not installation_id or installation_id.startswith("YOUR_")
        ):
            raise GitHubConfigurationError(
                "Set GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY, and GITHUB_INSTALLATION_ID."
            )
        return cls(app_id, private_key, installation_id)

    def _installation_token(self) -> str:
        issued_at = int(time.time()) - 30
        try:
            app_jwt = jwt.encode(
                {"iat": issued_at, "exp": issued_at + 540, "iss": self.app_id},
                self.private_key,
                algorithm="RS256",
            )
        except PyJWTError as error:
            raise GitHubConfigurationError("GitHub App private key is invalid.") from error
        try:
            response = self.client.post(
                f"{GITHUB_API}/app/installations/{quote(self.installation_id, safe='')}/access_tokens",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {app_jwt}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        except httpx.HTTPError as error:
            raise GitHubApiError("GitHub App token request could not reach GitHub.") from error
        if response.status_code != 201:
            raise GitHubApiError(f"GitHub App token request failed with HTTP {response.status_code}.")
        token = response.json().get("token")
        if not isinstance(token, str) or not token:
            raise GitHubApiError("GitHub did not return an installation access token.")
        return token

    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._installation_token()}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def dispatch_workflow(
        self,
        repository: str,
        workflow_id: str,
        ref: str,
        inputs: Optional[Dict[str, str]] = None,
    ) -> None:
        if not REPO_PATTERN.fullmatch(repository):
            raise ValueError("Repository must be in owner/repository format.")
        if not WORKFLOW_PATTERN.fullmatch(workflow_id):
            raise ValueError("Workflow must be a workflow filename or numeric ID.")
        if not REF_PATTERN.fullmatch(ref) or ".." in ref or ref.startswith("/") or ref.endswith("/"):
            raise ValueError("Git ref is invalid.")
        try:
            response = self.client.post(
                f"{GITHUB_API}/repos/{repository}/actions/workflows/{quote(workflow_id, safe='')}/dispatches",
                headers=self._headers(),
                json={"ref": ref, "inputs": inputs or {}},
            )
        except httpx.HTTPError as error:
            raise GitHubApiError("Workflow dispatch request could not reach GitHub.") from error
        if response.status_code != 204:
            raise GitHubApiError(f"GitHub workflow dispatch failed with HTTP {response.status_code}.")

    def cancel_run(self, repository: str, run_id: int) -> None:
        if not REPO_PATTERN.fullmatch(repository) or run_id < 1:
            raise ValueError("Repository or workflow run ID is invalid.")
        try:
            response = self.client.post(
                f"{GITHUB_API}/repos/{repository}/actions/runs/{run_id}/cancel",
                headers=self._headers(),
            )
        except httpx.HTTPError as error:
            raise GitHubApiError("Run cancellation request could not reach GitHub.") from error
        if response.status_code != 202:
            raise GitHubApiError(f"GitHub run cancellation failed with HTTP {response.status_code}.")

    def list_workflows(self, repository: str) -> List[Dict[str, object]]:
        if not REPO_PATTERN.fullmatch(repository):
            raise ValueError("Repository must be in owner/repository format.")
        try:
            response = self.client.get(
                f"{GITHUB_API}/repos/{repository}/actions/workflows?per_page=100",
                headers=self._headers(),
            )
        except httpx.HTTPError as error:
            raise GitHubApiError("GitHub workflow listing request could not reach GitHub.") from error
        if response.status_code != 200:
            raise GitHubApiError(f"GitHub workflow listing failed with HTTP {response.status_code}.")
        workflows = response.json().get("workflows")
        if not isinstance(workflows, list):
            raise GitHubApiError("GitHub returned an invalid workflow list.")
        return [workflow for workflow in workflows if isinstance(workflow, dict)]
