"""Pure Kedro job execution + Redis status/log mirror.

Extracted from the FastAPI worker so Prefect (or any other executor) can run the
same code path without importing the HTTP layer.
"""
from __future__ import annotations

import copy
import io
import json
import os
import sys
import threading
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import redis
from kedro.framework.session import KedroSession
from kedro.framework.startup import bootstrap_project

from kedro_pipeline.hooks import clear_job_context, set_job_context
from kedro_pipeline.worker.app import App
from shared import BACKTEST_STEPS, load_config_dict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_redis: redis.Redis | None = None
_jobs_lock = threading.Lock()
_running_jobs: set[str] = set()

try:
    bootstrap_project(_PROJECT_ROOT)
except Exception as _bootstrap_error:  # pragma: no cover
    print(f"WARN: Kedro bootstrap failed: {_bootstrap_error}")


def get_redis_url() -> str:
    from shared import get_redis_url as _url

    return _url()


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


def running_job_ids() -> list[str]:
    with _jobs_lock:
        return list(_running_jobs)


def load_params(config_path: str, config_overrides: dict | None = None) -> dict:
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
        prefix = config.get("mlflow_registry_prefix") or ""
        if not prefix or prefix == "itb_" or not prefix.startswith(f"itb_{symbol}"):
            config["mlflow_registry_prefix"] = f"itb_{symbol}_"
        exp = config.get("mlflow_experiment_name") or ""
        if not exp or exp in ("itb_default", "itb_") or not exp.startswith(f"itb_{symbol}"):
            config["mlflow_experiment_name"] = f"itb_{symbol}"

    if os.environ.get("MLFLOW_TRACKING_URI"):
        config["mlflow_tracking_uri"] = os.environ["MLFLOW_TRACKING_URI"]
    return config


def select_pipeline(steps: list[str]) -> str:
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
        params = load_params(config_path, config_overrides=config_overrides)
        append_log(
            job_id,
            f"Symbol={params.get('symbol')} registry_prefix={params.get('mlflow_registry_prefix')}",
        )
        pipeline_name = select_pipeline(steps)

        set_job_context(job_id, len(steps), update_job, append_log)
        out = LogCapture(job_id, sys.stdout)
        err = LogCapture(job_id, sys.stderr)
        try:
            with redirect_stdout(out), redirect_stderr(err):
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
        raise
    finally:
        with _jobs_lock:
            _running_jobs.discard(job_id)
