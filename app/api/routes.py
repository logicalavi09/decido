from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from app.api.schemas import (
    ImpactRequest,
    ResolveRequest,
    ResumeRequest,
    RunCreateRequest,
)
from app.core.errors import ConflictError, InputValidationError
from app.domain.config_diff import diff_configs
from app.domain.config_lint import lint_summary, lint_workflow, lint_workflows
from app.domain.models import RunState, RunStatus
from app.infra.config_loader import compute_config_etag
from app.services.workflow_service import WorkflowService

router = APIRouter()


def _service(request: Request) -> WorkflowService:
    return request.app.state.service


def run_view(run: RunState) -> dict[str, Any]:
    retry_config = run.workflow_snapshot.get("retry") or {}
    return {
        "id": run.id,
        "workflow": run.workflow_name,
        "workflow_version": run.workflow_version,
        "workflow_hash": run.workflow_hash,
        "idempotency_key": run.idempotency_key,
        "status": run.status.value,
        "outcome": run.outcome.value if run.outcome else None,
        "current_stage": run.current_stage,
        "retry_count": run.retry_count,
        "max_workflow_attempts": retry_config.get("max_workflow_attempts"),
        "next_retry_at": run.next_retry_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "input": run.input,
        "result": run.result,
        "last_error": run.last_error,
    }


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    registry = request.app.state.registry
    repository = request.app.state.repository
    repository.list_runs(limit=1)
    return {
        "status": "ok",
        "workflows": len(registry.all()),
        "workflows_loaded_at": registry.loaded_at,
    }


@router.get("/workflows")
def list_workflows(request: Request) -> dict[str, Any]:
    registry = request.app.state.registry
    return registry.summary()


@router.get("/workflows/{workflow_name}")
def get_workflow(workflow_name: str, request: Request) -> dict[str, Any]:
    registry = request.app.state.registry
    workflow = registry.get(workflow_name)
    findings = lint_workflow(workflow)
    return {
        "workflow": workflow.snapshot(),
        "snapshot_hash": workflow.snapshot_hash(),
        "lint": {"summary": lint_summary(findings), "findings": findings},
    }


@router.get("/workflows/{workflow_name}/lint")
def lint_single_workflow(workflow_name: str, request: Request) -> dict[str, Any]:
    workflow = request.app.state.registry.get(workflow_name)
    findings = lint_workflow(workflow)
    return {
        "workflow": workflow.name,
        "version": workflow.version,
        "summary": lint_summary(findings),
        "findings": findings,
    }


@router.post("/workflows/{workflow_name}/impact")
def analyze_workflow_impact(
    workflow_name: str, payload: ImpactRequest, request: Request
) -> dict[str, Any]:
    return _service(request).analyze_impact(
        workflow_name,
        payload.candidate,
        sample_size=payload.sample_size,
        inputs=payload.inputs,
        simulation=payload.simulation,
        include_inputs=payload.include_inputs,
    )


@router.post("/workflows/reload")
def reload_workflows(
    request: Request,
    dry_run: bool = False,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> dict[str, Any]:
    registry = request.app.state.registry
    active_etag = registry.etag()
    if if_match is not None:
        normalized = if_match.strip().strip('"')
        if normalized != active_etag:
            raise ConflictError(
                "configuration changed since the supplied If-Match ETag",
                details={
                    "current_etag": active_etag,
                    "provided_etag": normalized,
                },
            )

    before = {workflow.name: workflow for workflow in registry.all()}
    if dry_run:
        candidate, sources = registry.preview()
        candidate_etag = compute_config_etag(candidate)
        lint = lint_workflows(candidate)
        return {
            "dry_run": True,
            "active_etag": active_etag,
            "candidate_etag": candidate_etag,
            "has_changes": compute_config_etag(before) != candidate_etag,
            "diff": diff_configs(before, candidate),
            "candidate": registry.summarize(
                candidate, sources, etag=candidate_etag
            ),
            "lint": lint,
        }

    after = registry.reload()
    return {
        **registry.summary(),
        "dry_run": False,
        "previous_etag": active_etag,
        "diff": diff_configs(before, after),
    }


@router.post("/runs")
def create_run(payload: RunCreateRequest, request: Request):
    idempotency_key = request.headers.get("Idempotency-Key")
    submission = _service(request).submit(
        payload.workflow,
        payload.input,
        idempotency_key=idempotency_key,
        simulation=payload.simulation,
    )
    body = run_view(submission.run)
    headers = {"Idempotency-Replayed": "true"} if submission.replayed else {}
    if not submission.replayed:
        headers["Location"] = f"/runs/{submission.run.id}"
    return JSONResponse(
        status_code=200 if submission.replayed else 201,
        content=body,
        headers=headers,
    )


@router.get("/runs")
def list_runs(
    request: Request,
    status: str | None = Query(default=None),
    workflow: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    if status is not None:
        try:
            status = RunStatus(status).value
        except ValueError as exc:
            raise InputValidationError(
                f"unknown run status '{status}'",
                details={"allowed": [item.value for item in RunStatus]},
            ) from exc
    runs = request.app.state.repository.list_runs(
        status=status, workflow=workflow, limit=limit
    )
    return {"count": len(runs), "items": [run_view(run) for run in runs]}


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict[str, Any]:
    return run_view(_service(request).get_run(run_id))


@router.get("/runs/{run_id}/audit")
def get_run_audit(run_id: str, request: Request) -> dict[str, Any]:
    return _service(request).get_audit(run_id)


@router.get("/runs/{run_id}/audit/verify")
def verify_run_audit(run_id: str, request: Request) -> dict[str, Any]:
    return _service(request).verify_audit(run_id)


@router.get("/runs/{run_id}/explanation")
def get_run_explanation(run_id: str, request: Request) -> dict[str, Any]:
    return _service(request).explain(run_id)


@router.post("/runs/{run_id}/resume")
def resume_run(
    run_id: str,
    request: Request,
    payload: ResumeRequest | None = None,
) -> dict[str, Any]:
    simulation = payload.simulation if payload else None
    run = _service(request).resume_run(run_id, simulation=simulation)
    return run_view(run)


@router.post("/runs/{run_id}/resolve")
def resolve_run(
    run_id: str,
    payload: ResolveRequest,
    request: Request,
) -> dict[str, Any]:
    run = _service(request).resolve_manual_review(
        run_id,
        decision=payload.decision,
        reviewer=payload.reviewer,
        comment=payload.comment,
    )
    return run_view(run)
