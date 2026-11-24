"""DevOps Intelligence API.

Database reads use the caller's Supabase JWT so Postgres RLS remains the
authorization boundary. External provider actions use server-side adapters and
credentials and persist results only after the user-scoped pipeline is loaded.
"""
import hashlib
import hmac
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import Client

from backend.supabase_adapter import InvalidAccessToken, get_authenticated_client
from backend.supabase_adapter import get_supabase_admin
from backend.github_actions import (
    GitHubActions,
    GitHubApiError,
    GitHubConfigurationError,
)
from backend.ci_providers import (
    CIProviderConfigurationError,
    CIProviderError,
    CIProviders,
    ProviderRun,
)
from backend.notifications import (
    NotificationConfigurationError,
    NotificationDeliveryError,
    ResendNotifier,
)

app = FastAPI(title="DevOps Intelligence", version="1.0.0")
frontend_url = os.getenv("FRONTEND_URL", "http://localhost:8001")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in frontend_url.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Hub-Signature-256", "X-GitHub-Delivery"],
)

SECRET_PATTERNS = [
    (r"ghp_[A-Za-z0-9_]{20,}", "[REDACTED SECRET]"),
    (r"github_pat_[A-Za-z0-9_]+", "[REDACTED SECRET]"),
    (r"AKIA[0-9A-Z]{16}", "[REDACTED SECRET]"),
    (r"-----BEGIN.*?PRIVATE KEY-----", "[REDACTED SECRET]"),
    (r"(?i)(password|api_key|apikey|token|secret)\s*=\s*[^\s]+", r"\1=[REDACTED SECRET]"),
    (r"Bearer\s+[A-Za-z0-9._-]+", "Bearer [REDACTED SECRET]"),
]


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    repository: Optional[str] = None
    description: Optional[str] = None
    status: Literal["active", "paused", "rollback_required", "archived"] = "active"
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    is_pinned: bool = False


class ReminderIn(BaseModel):
    organization_id: str
    project_id: Optional[str] = None
    remind_at: datetime
    channel: str = "in_app"


class ReminderReviewIn(BaseModel):
    is_reviewed: bool


class NotifyIn(BaseModel):
    event: str = Field(min_length=1, max_length=200)
    severity: str
    project: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=5000)


def redact_logs(text: str) -> str:
    redacted = text or ""
    for pattern, replacement in SECRET_PATTERNS:
        redacted = re.sub(pattern, replacement, redacted, flags=re.DOTALL)
    return redacted


def success_rate(success: int, failed: int) -> float:
    denominator = success + failed
    return round(success / denominator * 100, 1) if denominator else 0.0


def verify_webhook_signature(request_body: bytes, signature: str, secret: str) -> bool:
    if not secret:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), request_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


@dataclass
class AuthContext:
    db: Client
    user_id: str


def authenticated_db(authorization: Optional[str] = Header(default=None)) -> AuthContext:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="A Supabase access token is required.")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        client, user_id = get_authenticated_client(token)
    except InvalidAccessToken as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return AuthContext(db=client, user_id=user_id)


def rows(response: Any) -> List[Dict[str, Any]]:
    return response.data or []


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "api": "v1"}


@app.get("/api/v1/projects")
def list_projects(context: AuthContext = Depends(authenticated_db)) -> List[Dict[str, Any]]:
    response = context.db.table("projects").select(
        "id,owner_id,organization_id,name,repository,description,status,priority,is_pinned,"
        "sort_order,last_reviewed_at,next_review_at,created_at"
    ).order("sort_order").order("created_at").execute()
    return rows(response)


@app.post("/api/v1/projects", status_code=201)
def create_project(body: ProjectIn, context: AuthContext = Depends(authenticated_db)) -> Dict[str, Any]:
    values = body.model_dump()
    values["name"] = values["name"].strip()
    if not values["name"]:
        raise HTTPException(status_code=422, detail="Project name cannot be blank.")
    values["owner_id"] = context.user_id
    response = context.db.table("projects").insert(values).select(
        "id,owner_id,organization_id,name,repository,description,status,priority,is_pinned,"
        "sort_order,last_reviewed_at,next_review_at,created_at"
    ).single().execute()
    return response.data


@app.patch("/api/v1/projects/{project_id}")
def update_project(
    project_id: str,
    body: ProjectIn,
    context: AuthContext = Depends(authenticated_db),
) -> Dict[str, Any]:
    values = body.model_dump()
    values["name"] = values["name"].strip()
    if not values["name"]:
        raise HTTPException(status_code=422, detail="Project name cannot be blank.")
    response = context.db.table("projects").update(values).eq("id", project_id).select(
        "id,owner_id,organization_id,name,repository,description,status,priority,is_pinned,"
        "sort_order,last_reviewed_at,next_review_at,created_at"
    ).maybe_single().execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Project not found or not editable.")
    return response.data


@app.get("/api/v1/pipelines")
def list_pipelines(context: AuthContext = Depends(authenticated_db)) -> List[Dict[str, Any]]:
    response = context.db.table("pipelines").select(
        "id,organization_id,project_id,integration_id,name,provider,repo,branch,"
        "workflow_id,has_workflow_dispatch,is_selected,priority,sort_order,created_at"
    ).order("sort_order").execute()
    return rows(response)


@app.get("/api/v1/pipelines/{pipeline_id}/runs")
def list_runs(pipeline_id: str, context: AuthContext = Depends(authenticated_db)) -> List[Dict[str, Any]]:
    response = context.db.table("pipeline_runs").select(
        "id,pipeline_id,run_number,status,branch,commit_sha,triggered_by,trigger_type,"
        "environment,started_at,ended_at,duration_sec,is_retry,parent_run_id,created_at"
    ).eq("pipeline_id", pipeline_id).order("started_at", desc=True).execute()
    return rows(response)


@app.get("/api/v1/integrations")
def list_integrations(context: AuthContext = Depends(authenticated_db)) -> List[Dict[str, Any]]:
    response = context.db.table("integrations").select(
        "id,organization_id,provider,auth_method,status,last_sync_at,last_error,sync_method,created_at"
    ).order("created_at", desc=True).execute()
    return rows(response)


@app.get("/api/v1/reminders")
def list_reminders(context: AuthContext = Depends(authenticated_db)) -> List[Dict[str, Any]]:
    response = context.db.table("reminders").select(
        "id,organization_id,project_id,remind_at,channel,is_reviewed,reviewed_by,reviewed_at"
    ).order("remind_at").execute()
    return rows(response)


@app.post("/api/v1/reminders", status_code=201)
def create_reminder(body: ReminderIn, context: AuthContext = Depends(authenticated_db)) -> Dict[str, Any]:
    response = context.db.table("reminders").insert(body.model_dump(mode="json")).select(
        "id,organization_id,project_id,remind_at,channel,is_reviewed,reviewed_by,reviewed_at"
    ).single().execute()
    return response.data


@app.patch("/api/v1/reminders/{reminder_id}/review")
def review_reminder(
    reminder_id: str,
    body: ReminderReviewIn,
    context: AuthContext = Depends(authenticated_db),
) -> Dict[str, Any]:
    changes = {
        "is_reviewed": body.is_reviewed,
        "reviewed_by": context.user_id if body.is_reviewed else None,
        "reviewed_at": datetime.now(timezone.utc).isoformat() if body.is_reviewed else None,
    }
    response = context.db.table("reminders").update(changes).eq(
        "id", reminder_id
    ).select("id,organization_id,project_id,remind_at,channel,is_reviewed,reviewed_by,reviewed_at").maybe_single().execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Reminder not found or not editable.")
    return response.data


@app.get("/api/v1/notification-preferences")
def list_notification_preferences(context: AuthContext = Depends(authenticated_db)) -> List[Dict[str, Any]]:
    response = context.db.table("notification_preferences").select(
        "id,organization_id,event,channel,severity,quiet_hours_start,quiet_hours_end,"
        "critical_bypass_quiet_hours,timezone"
    ).execute()
    return rows(response)


@app.get("/api/v1/metrics")
def metrics(context: AuthContext = Depends(authenticated_db)) -> Dict[str, Any]:
    def count_status(status: str) -> int:
        response = context.db.table("pipeline_runs").select(
            "id", count="exact", head=True
        ).eq("status", status).execute()
        return response.count or 0

    successful = count_status("success")
    failed = count_status("failed")
    return {
        "successful": successful,
        "failed": failed,
        "running": count_status("running"),
        "cancelled": count_status("cancelled"),
        "success_rate": success_rate(successful, failed),
    }


@app.post("/api/v1/pipelines/{pipeline_id}/run", status_code=202)
def run_pipeline(pipeline_id: str, context: AuthContext = Depends(authenticated_db)) -> Dict[str, Any]:
    pipeline = load_pipeline(pipeline_id, context.db)
    require_pipeline_operator(pipeline, context)
    if pipeline["provider"] == "GitHub Actions":
        pipeline = validate_github_pipeline(pipeline)
        try:
            GitHubActions.from_environment().dispatch_workflow(
                pipeline["repo"], pipeline["workflow_id"], pipeline["branch"] or "main"
            )
        except GitHubConfigurationError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except GitHubApiError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"accepted": True, "action": "run", "provider": "github", "workflow_id": pipeline["workflow_id"]}
    return dispatch_ci_pipeline(pipeline)


@app.post("/api/v1/pipelines/{pipeline_id}/retry", status_code=202)
def retry_pipeline(pipeline_id: str, run_id: str, context: AuthContext = Depends(authenticated_db)) -> Dict[str, Any]:
    run_response = context.db.table("pipeline_runs").select(
        "id,pipeline_id,status,branch"
    ).eq("id", run_id).eq(
        "pipeline_id", pipeline_id
    ).maybe_single().execute()
    run = run_response.data
    if not run:
        raise HTTPException(status_code=404, detail="Pipeline run not found or not accessible.")
    if run["status"] not in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Only failed or cancelled runs can be retried.")
    pipeline = load_pipeline(pipeline_id, context.db)
    require_pipeline_operator(pipeline, context)
    if pipeline["provider"] == "GitHub Actions":
        pipeline = validate_github_pipeline(pipeline)
        try:
            GitHubActions.from_environment().dispatch_workflow(
                pipeline["repo"], pipeline["workflow_id"], run["branch"] or pipeline["branch"] or "main"
            )
        except GitHubConfigurationError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except GitHubApiError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        provider_name = "github"
    else:
        pipeline = {**pipeline, "branch": run["branch"] or pipeline["branch"]}
        result = dispatch_ci_pipeline(pipeline, is_retry=True, parent_run_id=run_id)
        return {**result, "retry_of_run_id": run_id}
    return {
        "accepted": True,
        "action": "retry",
        "provider": provider_name,
        "workflow_id": pipeline["workflow_id"],
        "retry_of_run_id": run_id,
    }


def load_pipeline(pipeline_id: str, db: Client) -> Dict[str, Any]:
    response = db.table("pipelines").select(
        "id,organization_id,provider,repo,branch,workflow_id,has_workflow_dispatch"
    ).eq("id", pipeline_id).maybe_single().execute()
    pipeline = response.data
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found or not accessible.")
    return pipeline


def require_pipeline_operator(pipeline: Dict[str, Any], context: AuthContext) -> None:
    response = context.db.table("profiles").select(
        "organization_id,role"
    ).eq("id", context.user_id).maybe_single().execute()
    profile = response.data
    if not profile or profile.get("organization_id") != pipeline.get("organization_id"):
        raise HTTPException(status_code=404, detail="Pipeline not found or not accessible.")
    if profile.get("role") not in {"owner", "admin", "developer"}:
        raise HTTPException(status_code=403, detail="Your organization role cannot run or manage pipelines.")


def validate_github_pipeline(pipeline: Dict[str, Any]) -> Dict[str, Any]:
    if pipeline["provider"] != "GitHub Actions":
        raise HTTPException(status_code=422, detail="This endpoint currently supports GitHub Actions only.")
    if not pipeline["has_workflow_dispatch"]:
        raise HTTPException(status_code=409, detail="The workflow must enable workflow_dispatch.")
    if not pipeline["workflow_id"]:
        raise HTTPException(status_code=409, detail="Set the workflow filename before running this pipeline.")
    return pipeline


def load_github_pipeline(pipeline_id: str, db: Client) -> Dict[str, Any]:
    return validate_github_pipeline(load_pipeline(pipeline_id, db))


def dispatch_ci_pipeline(
    pipeline: Dict[str, Any],
    *,
    is_retry: bool = False,
    parent_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        admin = get_supabase_admin()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="Server-side Supabase credentials are not configured.") from error
    try:
        provider_run = CIProviders().dispatch(pipeline)
    except CIProviderConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except CIProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    values = {
        "organization_id": pipeline["organization_id"],
        "pipeline_id": pipeline["id"],
        "provider_run_id": provider_run.provider_run_id,
        "run_number": provider_run.run_number,
        "status": provider_run.status,
        "branch": provider_run.branch or pipeline.get("branch"),
        "commit_sha": provider_run.commit_sha,
        "started_at": provider_run.started_at,
        "ended_at": provider_run.ended_at,
        "is_retry": is_retry,
        "parent_run_id": parent_run_id,
    }
    saved = admin.table("pipeline_runs").upsert(
        values, on_conflict="pipeline_id,provider_run_id"
    ).select("id,pipeline_id,provider_run_id,status").single().execute()
    return {
        "accepted": True,
        "action": "retry" if is_retry else "run",
        "provider": pipeline["provider"],
        "run": saved.data,
    }


@app.post("/api/v1/pipelines/{pipeline_id}/runs/{run_id}/cancel", status_code=202)
def cancel_pipeline(
    pipeline_id: str,
    run_id: str,
    context: AuthContext = Depends(authenticated_db),
) -> Dict[str, Any]:
    run_response = context.db.table("pipeline_runs").select(
        "id,status,provider_run_id"
    ).eq("id", run_id).eq("pipeline_id", pipeline_id).maybe_single().execute()
    run = run_response.data
    if not run:
        raise HTTPException(status_code=404, detail="Pipeline run not found or not accessible.")
    if run["status"] != "running" or not run["provider_run_id"]:
        raise HTTPException(status_code=409, detail="Only active provider runs can be cancelled.")
    pipeline = load_pipeline(pipeline_id, context.db)
    require_pipeline_operator(pipeline, context)
    if pipeline["provider"] == "GitHub Actions":
        pipeline = validate_github_pipeline(pipeline)
        try:
            GitHubActions.from_environment().cancel_run(pipeline["repo"], int(run["provider_run_id"]))
        except GitHubConfigurationError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except GitHubApiError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except (ValueError, TypeError) as error:
            raise HTTPException(status_code=422, detail="GitHub run identifier is invalid.") from error
    else:
        try:
            CIProviders().cancel(pipeline, str(run["provider_run_id"]))
        except CIProviderConfigurationError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except CIProviderError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "accepted": True,
        "action": "cancel",
        "provider": pipeline["provider"],
        "provider_run_id": run["provider_run_id"],
    }


@app.post("/api/v1/pipelines/{pipeline_id}/sync")
def sync_pipeline(pipeline_id: str, context: AuthContext = Depends(authenticated_db)) -> Dict[str, Any]:
    pipeline = load_pipeline(pipeline_id, context.db)
    require_pipeline_operator(pipeline, context)
    if pipeline["provider"] == "GitHub Actions":
        return {"synced": 0, "provider": "GitHub Actions", "message": "GitHub run status is synchronized by webhook."}
    pending = context.db.table("pipeline_runs").select(
        "id,provider_run_id"
    ).eq("pipeline_id", pipeline_id).eq("status", "running").execute().data or []
    if not pending:
        return {"synced": 0, "provider": pipeline["provider"]}
    try:
        admin = get_supabase_admin()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="Server-side Supabase credentials are not configured.") from error
    provider = CIProviders()
    synced = 0
    try:
        for run in pending:
            if not run.get("provider_run_id"):
                continue
            current = provider.sync(pipeline, str(run["provider_run_id"]))
            admin.table("pipeline_runs").update({
                "provider_run_id": current.provider_run_id,
                "run_number": current.run_number,
                "status": current.status,
                "branch": current.branch or pipeline.get("branch"),
                "commit_sha": current.commit_sha,
                "started_at": current.started_at,
                "ended_at": current.ended_at,
            }).eq("id", run["id"]).eq("pipeline_id", pipeline_id).execute()
            synced += 1
    except CIProviderConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except CIProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"synced": synced, "provider": pipeline["provider"]}


class RollbackIn(BaseModel):
    target_commit_sha: str = Field(min_length=7, max_length=64)
    target_workflow: str = Field(min_length=1, max_length=200)
    environment: str = Field(min_length=1, max_length=100)
    confirm: bool


@app.post("/api/v1/pipelines/{pipeline_id}/rollback", status_code=202)
def rollback(
    pipeline_id: str,
    body: RollbackIn,
    context: AuthContext = Depends(authenticated_db),
) -> Dict[str, Any]:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Rollback requires explicit confirmation.")
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", body.target_commit_sha):
        raise HTTPException(status_code=422, detail="Rollback target must be a commit SHA.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", body.environment):
        raise HTTPException(status_code=422, detail="Rollback environment name is invalid.")
    pipeline = load_github_pipeline(pipeline_id, context.db)
    require_pipeline_operator(pipeline, context)
    if body.target_workflow != pipeline["workflow_id"]:
        raise HTTPException(status_code=422, detail="Rollback workflow must match the saved pipeline workflow.")
    try:
        GitHubActions.from_environment().dispatch_workflow(
            pipeline["repo"],
            pipeline["workflow_id"],
            pipeline["branch"] or "main",
            inputs={
                "target_commit_sha": body.target_commit_sha,
                "environment": body.environment,
            },
        )
    except GitHubConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except GitHubApiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"accepted": True, "action": "rollback", "provider": "github", "workflow_id": pipeline["workflow_id"]}


@app.post("/api/v1/logs/redact")
def redact(body: Dict[str, Any]) -> Dict[str, str]:
    text = body.get("text", "")
    if not isinstance(text, str):
        raise HTTPException(status_code=422, detail="text must be a string.")
    return {"redacted": redact_logs(text)}


@app.post("/api/v1/webhooks/github", status_code=202)
async def github_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    body = await request.body()
    if not secret:
        raise HTTPException(status_code=503, detail="GitHub webhook secret is not configured.")
    if not verify_webhook_signature(body, x_hub_signature_256 or "", secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")
    event = request.headers.get("X-GitHub-Event")
    if event != "workflow_run":
        return {"accepted": True, "ignored": "unsupported_event"}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="Webhook payload must be valid JSON.") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object.")
    run = payload.get("workflow_run")
    repository_payload = payload.get("repository")
    repository = repository_payload.get("full_name") if isinstance(repository_payload, dict) else None
    if not isinstance(run, dict) or not isinstance(repository, str):
        raise HTTPException(status_code=400, detail="Webhook payload is missing workflow run details.")
    provider_run_number = run.get("id")
    workflow_id = run.get("workflow_id")
    if not isinstance(provider_run_number, int) or not isinstance(workflow_id, int):
        raise HTTPException(status_code=400, detail="Webhook payload has invalid GitHub run identifiers.")
    provider_run_id = str(provider_run_number)
    raw_status = run.get("status")
    if raw_status == "completed":
        conclusion = run.get("conclusion")
        status = "success" if conclusion == "success" else (
            "cancelled" if conclusion in {"cancelled", "skipped"} else "failed"
        )
    elif raw_status in {"queued", "in_progress", "requested", "waiting", "pending"}:
        status = "running"
    else:
        return {"accepted": True, "ignored": "unsupported_run_status"}
    try:
        admin = get_supabase_admin()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="Server-side Supabase credentials are not configured.") from error
    pipeline_response = admin.table("pipelines").select(
        "id,organization_id,workflow_id"
    ).eq("provider", "GitHub Actions").eq("repo", repository).execute()
    candidates = pipeline_response.data or []
    if not candidates:
        return {"accepted": True, "ignored": "pipeline_not_registered"}
    try:
        workflows = GitHubActions.from_environment().list_workflows(repository)
    except GitHubConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except GitHubApiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    remote_workflow = next(
        (workflow for workflow in workflows if workflow.get("id") == workflow_id),
        None,
    )
    if not remote_workflow:
        return {"accepted": True, "ignored": "workflow_not_found"}
    workflow_path = remote_workflow.get("path")
    workflow_filename = workflow_path.rsplit("/", 1)[-1] if isinstance(workflow_path, str) else None
    matching_pipelines = [
        pipeline for pipeline in candidates
        if pipeline.get("workflow_id") in {str(workflow_id), workflow_path, workflow_filename}
    ]
    if not matching_pipelines:
        return {"accepted": True, "ignored": "pipeline_not_registered_for_workflow"}
    if len(matching_pipelines) > 1:
        raise HTTPException(
            status_code=409,
            detail="Multiple pipelines reference this GitHub workflow; remove the duplicate before syncing runs.",
        )
    pipeline = matching_pipelines[0]
    existing_response = admin.table("pipeline_runs").select(
        "id,status"
    ).eq("pipeline_id", pipeline["id"]).eq(
        "provider_run_id", provider_run_id
    ).maybe_single().execute()
    existing_run = existing_response.data
    if existing_run and existing_run["status"] in {"success", "failed", "cancelled"} and status == "running":
        return {"accepted": True, "ignored": "stale_running_event"}
    values = {
        "organization_id": pipeline["organization_id"],
        "pipeline_id": pipeline["id"],
        "provider_run_id": provider_run_id,
        "run_number": run.get("run_number"),
        "status": status,
        "branch": run.get("head_branch"),
        "commit_sha": run.get("head_sha"),
        "triggered_by": run.get("actor", {}).get("login") if isinstance(run.get("actor"), dict) else None,
        "trigger_type": run.get("event"),
        "environment": None,
        "started_at": run.get("run_started_at") or run.get("created_at"),
        "ended_at": run.get("updated_at") if raw_status == "completed" else None,
        "is_retry": False,
    }
    saved = admin.table("pipeline_runs").upsert(
        values, on_conflict="pipeline_id,provider_run_id"
    ).select("id,pipeline_id,provider_run_id,status").single().execute()
    return {"accepted": True, "run": saved.data}


@app.post("/api/v1/notifications/send", status_code=202)
def send_notification(body: NotifyIn, context: AuthContext = Depends(authenticated_db)) -> Dict[str, Any]:
    if body.severity not in {"critical", "high", "medium", "low"}:
        raise HTTPException(status_code=422, detail="severity must be critical, high, medium, or low.")
    event = body.event.strip()
    project = body.project.strip()
    message = body.message.strip()
    if not event or not project or not message:
        raise HTTPException(status_code=422, detail="Event, project, and message cannot be blank.")
    profile_response = context.db.table("profiles").select(
        "organization_id,email,email_verified"
    ).eq("id", context.user_id).maybe_single().execute()
    profile = profile_response.data
    if not profile or not profile.get("organization_id"):
        raise HTTPException(status_code=409, detail="Create an organization before sending notifications.")
    recipient = profile.get("email")
    if not isinstance(recipient, str) or not recipient.strip():
        raise HTTPException(status_code=409, detail="Your account does not have a notification email address.")
    if not profile.get("email_verified"):
        raise HTTPException(status_code=403, detail="Verify your account email before sending notifications.")
    try:
        notifier = ResendNotifier.from_environment()
    except NotificationConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    try:
        admin = get_supabase_admin()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="Server-side Supabase credentials are not configured.") from error

    dedupe_key = str(uuid.uuid4())
    try:
        provider_message_id = notifier.send(
            recipient.strip(),
            f"[{body.severity.upper()}] {project}: {event}",
            message,
            dedupe_key,
        )
    except NotificationDeliveryError as error:
        admin.table("notification_deliveries").insert({
            "organization_id": profile["organization_id"],
            "event": event,
            "channel": "email_transactional",
            "status": "failed",
            "provider": "Resend",
            "dedupe_key": dedupe_key,
        }).execute()
        raise HTTPException(status_code=502, detail=str(error)) from error

    admin.table("notification_deliveries").insert({
        "organization_id": profile["organization_id"],
        "event": event,
        "channel": "email_transactional",
        "status": "sent",
        "provider": "Resend",
        "dedupe_key": dedupe_key,
    }).execute()
    return {"accepted": True, "provider": "Resend", "provider_message_id": provider_message_id}
