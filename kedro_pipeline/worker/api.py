"""Pipeline worker: execute the offline ML pipeline as Kedro jobs.

The HTTP + Redis contract is unchanged from the legacy step-runner so the
backend/frontend need no edits:

* ``GET /health``                                  -> {status, redis, running_jobs}
* ``GET /internal/steps``                          -> {steps: ALL_STEPS}
* ``POST /internal/jobs``                          -> enqueue a Kedro run
* ``GET /internal/jobs/{job_id}``                  -> job hash fields
* ``GET /internal/jobs/{job_id}/logs?offset=N``    -> captured stdout lines

Internally, ``execute_job`` builds a Kedro ``Session`` and runs the requested
step subset (``node_names``) on the ``inference`` or ``backtest`` modular
pipeline. Per-node progress/current_step is fed to Redis by the
``RedisProgressHook``; logs are captured by wrapping ``session.run`` with
``LogCapture`` (same line-by-line model the SSE bridge expects).
"""
from __future__ import annotations

import copy
import io
import json
import os
import sys
import threading
import traceback
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import redis
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from kedro.framework.session import KedroSession
from kedro.framework.startup import bootstrap_project

from shared import ALL_STEPS, BACKTEST_STEPS, load_config_dict
from kedro_pipeline.worker.app import App

from kedro_pipeline.hooks import clear_job_context, set_job_context

app = FastAPI(title="ITB Pipeline Worker", version="0.2.0")


@app.on_event("startup")
def _startup_init_db() -> None:
    try:
        from shared.db import init_db

        init_db()
        print("Postgres schema ready.")
    except Exception as e:  # pragma: no cover
        print(f"WARN: Postgres init failed: {e}")

_redis: redis.Redis | None = None
_jobs_lock = threading.Lock()
_running_jobs: set[str] = set()

# Kedro project is bootstrapped once at import (idempotent).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
try:
    bootstrap_project(_PROJECT_ROOT)
except Exception as _bootstrap_error:  # pragma: no cover - startup diagnostic
    print(f"WARN: Kedro bootstrap failed: {_bootstrap_error}")


def rds() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(get_redis_url(), decode_responses=True)
    return _redis


def get_redis_url() -> str:
    from shared import get_redis_url as _url

    return _url()


def job_key(job_id: str) -> str:
    return f"itb:job:{job_id}"


def logs_key(job_id: str) -> str:
    return f"itb:job:{job_id}:logs"


def append_log(job_id: str, line: str) -> None:
    rds().rpush(logs_key(job_id), line)
    rds().expire(logs_key(job_id), 86400)


def update_job(job_id: str, **fields: Any) -> None:
    key = job_key(job_id)
    payload = {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v)) for k, v in fields.items()}
    rds().hset(key, mapping=payload)
    rds().expire(key, 86400)


def _load_params(config_path: str, config_overrides: dict | None = None) -> dict:
    """Build the full config dict (App defaults + jsonc + overrides).

    Concurrency-safe: never touches the ``App.config`` singleton.
    """
    config = copy.deepcopy(App.config)
    path = Path(config_path)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if path.exists():
        config.update(load_config_dict(path))
    if config_overrides:
        config.update(copy.deepcopy(config_overrides))

    symbol = str(config.get("symbol") or "").strip()
    if symbol:
        # Always isolate MLflow registry/experiment per symbol unless caller
        # already set a symbol-scoped prefix.
        prefix = config.get("mlflow_registry_prefix") or ""
        if not prefix or prefix == "itb_" or not prefix.startswith(f"itb_{symbol}"):
            config["mlflow_registry_prefix"] = f"itb_{symbol}_"
        exp = config.get("mlflow_experiment_name") or ""
        if not exp or exp in ("itb_default", "itb_") or not exp.startswith(f"itb_{symbol}"):
            config["mlflow_experiment_name"] = f"itb_{symbol}"

    # Docker / compose: prefer MLFLOW_TRACKING_URI over localhost defaults.
    if os.environ.get("MLFLOW_TRACKING_URI"):
        config["mlflow_tracking_uri"] = os.environ["MLFLOW_TRACKING_URI"]
    return config


def _select_pipeline(steps: list[str]) -> str:
    return "backtest" if any(s in BACKTEST_STEPS for s in steps) else "inference"


class LogCapture(io.TextIOBase):
    def __init__(self, job_id: str, original):
        self.job_id = job_id
        self.original = original
        self._buf = ""

    def write(self, s: str) -> int:
        if not s:
            return 0
        self.original.write(s)
        self.original.flush()
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            append_log(self.job_id, line)
        return len(s)

    def flush(self) -> None:
        self.original.flush()
        if self._buf:
            append_log(self.job_id, self._buf)
            self._buf = ""


def execute_job(
    job_id: str,
    steps: list[str],
    config_path: str,
    config_overrides: dict | None = None,
) -> None:
    with _jobs_lock:
        _running_jobs.add(job_id)
    update_job(
        job_id,
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
        current_step="",
        progress="0",
    )
    append_log(job_id, f"Job {job_id} started. Steps: {steps}")
    if config_overrides:
        append_log(job_id, f"Config overrides: {sorted(config_overrides.keys())}")

    try:
        params = _load_params(config_path, config_overrides=config_overrides)
        append_log(job_id, f"Symbol={params.get('symbol')} registry_prefix={params.get('mlflow_registry_prefix')}")
        pipeline_name = _select_pipeline(steps)

        set_job_context(job_id, len(steps), update_job, append_log)
        out = LogCapture(job_id, sys.stdout)
        err = LogCapture(job_id, sys.stderr)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                # extra_params belong on Session.create (Kedro 0.19), not run().
                # Spread config at the top level so the catalog's
                # `${runtime_params:...}` resolvers override globals,
                # AND nest it under "config" for `params:config` node input.
                with KedroSession.create(
                    project_path=_PROJECT_ROOT,
                    extra_params={**params, "config": params},
                ) as session:
                    session.run(
                        pipeline_name=pipeline_name,
                        node_names=steps,
                    )
            out.flush()
            err.flush()
        finally:
            clear_job_context()

        update_job(
            job_id,
            status="completed",
            current_step="",
            progress="100",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        append_log(job_id, "Job completed successfully.")
    except Exception as e:
        tb = traceback.format_exc()
        append_log(job_id, f"ERROR: {e}")
        append_log(job_id, tb)
        update_job(
            job_id,
            status="failed",
            error=str(e),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    finally:
        with _jobs_lock:
            _running_jobs.discard(job_id)


class JobRequest(BaseModel):
    steps: list[str] = Field(default_factory=lambda: list(ALL_STEPS[:8]))
    config_path: str | None = None
    job_id: str | None = None
    config_overrides: dict[str, Any] | None = None


class JobResponse(BaseModel):
    job_id: str
    status: str
    steps: list[str]


@app.get("/health")
def health():
    try:
        rds().ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {"status": "ok", "redis": redis_ok, "running_jobs": list(_running_jobs)}


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
    )
    rds().lpush("itb:jobs:recent", job_id)
    rds().ltrim("itb:jobs:recent", 0, 99)

    background_tasks.add_task(execute_job, job_id, req.steps, config_path, overrides)
    return JobResponse(job_id=job_id, status="queued", steps=req.steps)


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
    return data


@app.get("/internal/jobs/{job_id}/logs")
def get_logs(job_id: str, offset: int = 0):
    if not rds().exists(job_key(job_id)):
        raise HTTPException(404, "Job not found")
    lines = rds().lrange(logs_key(job_id), offset, -1)
    return {"job_id": job_id, "offset": offset, "lines": lines, "next_offset": offset + len(lines)}
