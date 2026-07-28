"""Pipeline worker: execute offline ML pipeline steps as jobs."""
from __future__ import annotations

import io
import json
import sys
import threading
import traceback
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from typing import Any, Callable

import redis
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from apps import ALL_STEPS, get_config_path, get_redis_url

app = FastAPI(title="ITB Pipeline Worker", version="0.1.0")

_redis: redis.Redis | None = None
_jobs_lock = threading.Lock()
_running_jobs: set[str] = set()


def rds() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(get_redis_url(), decode_responses=True)
    return _redis


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


def get_step_runner(step: str) -> Callable[[str], None]:
    mapping = {
        "download": ("scripts.download", "run_download"),
        "merge": ("scripts.merge", "run_merge"),
        "features": ("scripts.features", "run_features"),
        "labels": ("scripts.labels", "run_labels"),
        "train": ("scripts.train", "run_train"),
        "predict": ("scripts.predict", "run_predict"),
        "signals": ("scripts.signals", "run_signals"),
        "output": ("scripts.output", "run_output"),
        "predict_rolling": ("scripts.predict_rolling", "run_predict_rolling"),
        "simulate": ("scripts.simulate", "run_simulate"),
    }
    if step not in mapping:
        raise ValueError(f"Unknown step: {step}")
    module_name, func_name = mapping[step]
    module = __import__(module_name, fromlist=[func_name])
    return getattr(module, func_name)


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


def execute_job(job_id: str, steps: list[str], config_path: str) -> None:
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

    total = len(steps)
    try:
        for i, step in enumerate(steps):
            update_job(job_id, current_step=step, progress=str(int(100 * i / total)))
            append_log(job_id, f"=== Step {i + 1}/{total}: {step} ===")
            runner = get_step_runner(step)
            out = LogCapture(job_id, sys.stdout)
            err = LogCapture(job_id, sys.stderr)
            with redirect_stdout(out), redirect_stderr(err):
                runner(config_path)
            out.flush()
            err.flush()
            append_log(job_id, f"=== Finished step: {step} ===")

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
    config_path = req.config_path or str(get_config_path())

    update_job(
        job_id,
        status="queued",
        steps=json.dumps(req.steps),
        config_path=config_path,
        created_at=datetime.now(timezone.utc).isoformat(),
        progress="0",
        current_step="",
        error="",
    )
    rds().lpush("itb:jobs:recent", job_id)
    rds().ltrim("itb:jobs:recent", 0, 99)

    background_tasks.add_task(execute_job, job_id, req.steps, config_path)
    return JobResponse(job_id=job_id, status="queued", steps=req.steps)


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
