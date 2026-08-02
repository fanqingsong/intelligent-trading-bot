"""Prefect UI deep-links and optional flow-run status enrichment."""
from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import UUID


def prefect_api_url() -> str:
    return (os.environ.get("PREFECT_API_URL") or "").rstrip("/")


def prefect_ui_url() -> str:
    explicit = (os.environ.get("PREFECT_UI_URL") or "").rstrip("/")
    if explicit:
        return explicit
    api = prefect_api_url()
    if api.endswith("/api"):
        return api[: -len("/api")] or "http://localhost:4200"
    return "http://localhost:4200"


def flow_run_ui_url(flow_run_id: str | None) -> str | None:
    if not flow_run_id:
        return None
    return f"{prefect_ui_url()}/runs/flow-run/{flow_run_id}"


def redis_mirror_enabled() -> bool:
    raw = (os.environ.get("ITB_REDIS_JOB_MIRROR") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def job_source() -> str:
    """redis | prefect | hybrid (default)."""
    return (os.environ.get("ITB_JOB_SOURCE") or "hybrid").strip().lower()


def enrich_job(data: dict[str, Any]) -> dict[str, Any]:
    """Attach Prefect UI URL when a flow run id is present."""
    out = dict(data)
    run_id = out.get("prefect_flow_run_id") or out.get("flow_run_id")
    if run_id:
        out["prefect_flow_run_id"] = str(run_id)
        out["prefect_ui_url"] = flow_run_ui_url(str(run_id))
    return out


def prefect_info() -> dict[str, Any]:
    return {
        "api_url": prefect_api_url() or None,
        "ui_url": prefect_ui_url(),
        "redis_mirror": redis_mirror_enabled(),
        "job_source": job_source(),
    }


async def fetch_flow_run(flow_run_id: str) -> dict[str, Any] | None:
    if not prefect_api_url():
        return None
    from prefect.client.orchestration import get_client

    try:
        async with get_client() as client:
            run = await client.read_flow_run(UUID(str(flow_run_id)))
    except Exception:
        return None
    state = getattr(run.state, "type", None) or getattr(run, "state_name", None)
    status = _map_prefect_state(str(state or ""))
    return {
        "prefect_flow_run_id": str(run.id),
        "prefect_ui_url": flow_run_ui_url(str(run.id)),
        "status": status,
        "name": run.name,
        "tags": list(run.tags or []),
    }


def _map_prefect_state(state: str) -> str:
    s = state.upper()
    if s in ("COMPLETED", "COMPLETE"):
        return "completed"
    if s in ("FAILED", "CRASHED", "CANCELLED", "CANCELED"):
        return "failed"
    if s in ("RUNNING", "PENDING", "SCHEDULED", "LATE", "AWAITINGRETRY", "PAUSED"):
        if s == "SCHEDULED":
            return "queued"
        if s == "PENDING":
            return "queued"
        return "running"
    return state.lower() or "unknown"


async def list_recent_prefect_jobs(limit: int = 20) -> list[dict[str, Any]]:
    """List recent ITB flow runs from Prefect (tag: itb)."""
    if not prefect_api_url():
        return []
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.filters import FlowRunFilter, FlowRunFilterTags
    from prefect.client.schemas.sorting import FlowRunSort

    try:
        async with get_client() as client:
            runs = await client.read_flow_runs(
                flow_run_filter=FlowRunFilter(tags=FlowRunFilterTags(all_=["itb"])),
                sort=FlowRunSort.START_TIME_DESC,
                limit=limit,
            )
    except Exception:
        return []

    jobs: list[dict[str, Any]] = []
    for run in runs:
        tags = list(run.tags or [])
        job_id = ""
        symbol = ""
        team = ""
        for tag in tags:
            if tag.startswith("job:"):
                job_id = tag.split(":", 1)[1]
            elif tag.startswith("symbol:"):
                symbol = tag.split(":", 1)[1]
            elif tag.startswith("team:"):
                team = tag.split(":", 1)[1]
        state = getattr(run.state, "type", None) or getattr(run, "state_name", None)
        jobs.append(
            enrich_job(
                {
                    "job_id": job_id or str(run.id),
                    "prefect_flow_run_id": str(run.id),
                    "status": _map_prefect_state(str(state or "")),
                    "symbol": symbol,
                    "team": team,
                    "tags": tags,
                    "source": "prefect",
                }
            )
        )
    return jobs


def list_recent_prefect_jobs_sync(limit: int = 20) -> list[dict[str, Any]]:
    return asyncio.run(list_recent_prefect_jobs(limit))
