"""Fine-grained Kedro → Prefect bridge: each Kedro node becomes a Prefect task.

Runs inside a single flow process so ``MemoryDataset`` intermediates
(``raw_sources``, ``trained_models``) stay valid. Postgres-backed catalog
datasets already persist across tasks.
"""
from __future__ import annotations

import os
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from typing import Any

from kedro.framework.project import pipelines
from kedro.framework.session import KedroSession
from kedro.runner.runner import run_node
from prefect import task

from kedro_pipeline.hooks import clear_job_context, set_job_context
from kedro_pipeline.orchestration.kedro_runner import (
    LogCapture,
    _PROJECT_ROOT,
    _jobs_lock,
    _running_jobs,
    append_log,
    load_params,
    select_pipeline,
    update_job,
)

# Per-job in-process handles (fine-grained tasks share one catalog).
_JOB_STATE: dict[str, dict[str, Any]] = {}


def _retry_for_node(node_name: str) -> int:
    if node_name == "download":
        return max(0, int(os.environ.get("ITB_NODE_RETRIES_DOWNLOAD", "2")))
    return max(0, int(os.environ.get("ITB_NODE_RETRIES", "0")))


def _run_single_node(job_id: str, node_name: str) -> str:
    state = _JOB_STATE[job_id]
    node = state["nodes"][node_name]
    catalog = state["catalog"]
    session_id = state["session_id"]
    hook_manager = state["hook_manager"]
    completed = state["completed"]
    total = state["total"]

    update_job(
        job_id,
        current_step=node_name,
        progress=str(int(100 * completed / total)),
    )
    append_log(job_id, f"=== Node: {node_name} ===")

    out = LogCapture(job_id, sys.stdout)
    err = LogCapture(job_id, sys.stderr)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            run_node(
                node,
                catalog,
                hook_manager,
                is_async=False,
                session_id=session_id,
            )
        out.flush()
        err.flush()
    except Exception as e:
        append_log(job_id, f"ERROR in node {node_name}: {e}")
        raise

    state["completed"] = completed + 1
    update_job(job_id, progress=str(int(100 * state["completed"] / total)))
    return node_name


@task(name="kedro-node", retries=0, log_prints=True)
def kedro_node_task(job_id: str, node_name: str) -> str:
    return _run_single_node(job_id, node_name)


@task(name="kedro-download", retries=2, retry_delay_seconds=60, log_prints=True)
def kedro_download_task(job_id: str, node_name: str = "download") -> str:
    return _run_single_node(job_id, node_name)


def _invoke_node_task(job_id: str, node_name: str) -> None:
    retries = _retry_for_node(node_name)
    if node_name == "download":
        kedro_download_task.with_options(retries=retries)(job_id, node_name)
    else:
        kedro_node_task.with_options(retries=retries)(job_id, node_name)


def execute_job_fine(
    job_id: str,
    steps: list[str],
    config_path: str,
    config_overrides: dict | None = None,
    *,
    team: str = "default",
    tags: list[str] | None = None,
) -> None:
    """Run Kedro nodes as Prefect tasks (topological order), mirroring Redis job state."""
    with _jobs_lock:
        _running_jobs.add(job_id)

    update_job(
        job_id,
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
        current_step="",
        progress="0",
        execution="fine",
        team=team,
    )
    append_log(job_id, f"Job {job_id} started (fine-grained). Steps: {steps} team={team}")
    if tags:
        append_log(job_id, f"Tags: {tags}")
    if config_overrides:
        append_log(job_id, f"Config overrides: {sorted(config_overrides.keys())}")

    try:
        params = load_params(config_path, config_overrides=config_overrides)
        append_log(
            job_id,
            f"Symbol={params.get('symbol')} registry_prefix={params.get('mlflow_registry_prefix')}",
        )
        pipeline_name = select_pipeline(steps)
        pipe = pipelines[pipeline_name].only_nodes(*steps)
        ordered_names = [n.name for group in pipe.grouped_nodes for n in group]
        node_map = {n.name: n for n in pipe.nodes}

        set_job_context(job_id, len(ordered_names), update_job, append_log)
        with KedroSession.create(
            project_path=_PROJECT_ROOT,
            extra_params={**params, "config": params},
        ) as session:
            context = session.load_context()
            hook_manager = getattr(session, "_hook_manager", None) or getattr(
                context, "_hook_manager", None
            )
            if hook_manager is None:
                raise RuntimeError("Kedro session has no hook_manager")
            _JOB_STATE[job_id] = {
                "catalog": context.catalog,
                "nodes": node_map,
                "session_id": session.session_id,
                "hook_manager": hook_manager,
                "completed": 0,
                "total": max(len(ordered_names), 1),
            }
            for node_name in ordered_names:
                _invoke_node_task(job_id, node_name)

        update_job(
            job_id,
            status="completed",
            current_step="",
            progress="100",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        append_log(job_id, "Job completed successfully (fine-grained).")
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
        clear_job_context()
        _JOB_STATE.pop(job_id, None)
        with _jobs_lock:
            _running_jobs.discard(job_id)
