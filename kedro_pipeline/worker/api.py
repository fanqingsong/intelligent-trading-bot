"""Pipeline worker HTTP API: enqueue Kedro jobs (Prefect or local).

The HTTP + Redis contract is unchanged so backend/frontend need no edits:

* ``GET /health``                                  -> {status, redis, running_jobs, executor}
* ``GET /internal/steps``                          -> {steps: ALL_STEPS}
* ``POST /internal/jobs``                          -> enqueue a Kedro run
* ``GET /internal/jobs/{job_id}``                  -> job hash fields
* ``GET /internal/jobs/{job_id}/logs?offset=N``    -> captured stdout lines

Execution is delegated to Prefect (``ITB_JOB_EXECUTOR=prefect`` / ``PREFECT_API_URL``)
or FastAPI ``BackgroundTasks`` when ``ITB_JOB_EXECUTOR=local``.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from shared import ALL_STEPS
from kedro_pipeline.orchestration.kedro_runner import (
    append_log,
    execute_job,
    job_key,
    logs_key,
    rds,
    running_job_ids,
    update_job,
)
from kedro_pipeline.orchestration.prefect_enqueue import enqueue_kedro_job, prefect_enabled
from kedro_pipeline.orchestration.teams import default_team, normalize_team

app = FastAPI(title="ITB Pipeline Worker", version="0.4.0")


@app.on_event("startup")
def _startup_init_db() -> None:
    try:
        from shared.db import init_db

        init_db()
        print("Postgres schema ready.")
    except Exception as e:  # pragma: no cover
        print(f"WARN: Postgres init failed: {e}")
    executor = "prefect" if prefect_enabled() else "local"
    print(f"Job executor: {executor}")


class JobRequest(BaseModel):
    steps: list[str] = Field(default_factory=lambda: list(ALL_STEPS[:8]))
    config_path: str | None = None
    job_id: str | None = None
    config_overrides: dict[str, Any] | None = None
    team: str | None = None


class JobResponse(BaseModel):
    job_id: str
    status: str
    steps: list[str]
    team: str = "default"
    prefect_flow_run_id: str | None = None
    prefect_ui_url: str | None = None


@app.get("/health")
def health():
    try:
        rds().ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {
        "status": "ok",
        "redis": redis_ok,
        "running_jobs": running_job_ids(),
        "executor": "prefect" if prefect_enabled() else "local",
        "execution": os.environ.get("ITB_KEDRO_EXECUTION", "fine"),
        "team": default_team(),
    }


@app.get("/internal/steps")
def list_steps():
    return {"steps": ALL_STEPS}


@app.post("/internal/jobs", response_model=JobResponse)
def create_job(req: JobRequest, background_tasks: BackgroundTasks):
    unknown = [s for s in req.steps if s not in ALL_STEPS]
    if unknown:
        raise HTTPException(400, f"Unknown steps: {unknown}")
    if not req.steps:
        raise HTTPException(400, "No steps provided")

    job_id = req.job_id or str(uuid.uuid4())
    config_path = req.config_path or str(_config_path_default())
    overrides = req.config_overrides or {}
    team = normalize_team(req.team or default_team())

    update_job(
        job_id,
        status="queued",
        steps=json.dumps(req.steps),
        config_path=config_path,
        symbol=str(overrides.get("symbol") or ""),
        created_at=datetime.now(timezone.utc).isoformat(),
        progress="0",
        current_step="",
        error="",
        executor="prefect" if prefect_enabled() else "local",
        team=team,
        execution=os.environ.get("ITB_KEDRO_EXECUTION", "fine"),
    )
    rds().lpush("itb:jobs:recent", job_id)
    rds().ltrim("itb:jobs:recent", 0, 99)

    prefect_flow_run_id: str | None = None
    prefect_ui_url: str | None = None
    if prefect_enabled():
        try:
            flow_run_id = enqueue_kedro_job(
                job_id, req.steps, config_path, overrides, team=team
            )
        except Exception as e:
            update_job(
                job_id,
                status="failed",
                error=f"Prefect enqueue failed: {e}",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            append_log(job_id, f"ERROR: Prefect enqueue failed: {e}")
            raise HTTPException(503, f"Prefect enqueue failed: {e}") from e
        if flow_run_id:
            prefect_flow_run_id = flow_run_id
            try:
                from backend.prefect_links import flow_run_ui_url

                prefect_ui_url = flow_run_ui_url(flow_run_id)
            except Exception:
                prefect_ui_url = None
            update_job(
                job_id,
                prefect_flow_run_id=flow_run_id,
                prefect_ui_url=prefect_ui_url or "",
            )
            append_log(job_id, f"Enqueued Prefect flow run {flow_run_id} team={team}")
            if prefect_ui_url:
                append_log(job_id, f"Prefect UI: {prefect_ui_url}")
        else:
            append_log(job_id, f"Enqueued Prefect deployment run team={team}")
    else:
        background_tasks.add_task(execute_job, job_id, req.steps, config_path, overrides)

    return JobResponse(
        job_id=job_id,
        status="queued",
        steps=req.steps,
        team=team,
        prefect_flow_run_id=prefect_flow_run_id,
        prefect_ui_url=prefect_ui_url,
    )


def _config_path_default() -> Path:
    from shared import get_config_path

    return get_config_path()


@app.get("/internal/jobs/{job_id}")
def get_job(job_id: str):
    data = rds().hgetall(job_key(job_id))
    if not data:
        raise HTTPException(404, "Job not found")
    if "steps" in data:
        try:
            data["steps"] = json.loads(data["steps"])
        except Exception:
            pass
    data["job_id"] = job_id
    try:
        from backend.prefect_links import enrich_job

        data = enrich_job(data)
    except Exception:
        pass
    return data


@app.get("/internal/jobs/{job_id}/logs")
def get_logs(job_id: str, offset: int = 0):
    if not rds().exists(job_key(job_id)):
        raise HTTPException(404, "Job not found")
    lines = rds().lrange(logs_key(job_id), offset, -1)
    return {"job_id": job_id, "offset": offset, "lines": lines, "next_offset": offset + len(lines)}
