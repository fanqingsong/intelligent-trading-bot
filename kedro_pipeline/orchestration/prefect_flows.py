"""Prefect flows for ITB.

Phase 3: fine-grained Kedro node tasks + team-scoped deployments.
Prefect owns queueing, schedules, concurrency, and recovery; Kedro owns the step DAG.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from prefect import flow, get_run_logger
from prefect.concurrency.sync import concurrency

from kedro_pipeline.orchestration.concurrency import (
    concurrency_names_for_job,
    ensure_concurrency_limits,
)
from kedro_pipeline.orchestration.teams import default_team, job_kind, normalize_team

FLOW_NAME = "itb-kedro-job"
DAILY_PREDICT_FLOW_NAME = "itb-daily-predict"
DEPLOYMENT_NAME = "default"
DEPLOYMENT_REF = f"{FLOW_NAME}/{DEPLOYMENT_NAME}"
DAILY_PREDICT_DEPLOYMENT_REF = f"{DAILY_PREDICT_FLOW_NAME}/{DEPLOYMENT_NAME}"


def _use_fine_execution() -> bool:
    mode = (os.environ.get("ITB_KEDRO_EXECUTION") or "fine").strip().lower()
    return mode != "coarse"


@flow(name=FLOW_NAME, log_prints=True)
def kedro_job_flow(
    job_id: str,
    steps: list[str],
    config_path: str,
    config_overrides: dict[str, Any] | None = None,
    team: str = "default",
    tags: list[str] | None = None,
) -> str:
    """Run a Kedro step subset; default fine-grained (one Prefect task per node)."""
    logger = get_run_logger()
    overrides = config_overrides or {}
    symbol = str(overrides.get("symbol") or "").strip() or "unknown"
    team = normalize_team(team)
    kind = job_kind(steps, overrides)
    is_train = kind == "train"
    batch_mode = str(overrides.get("batch_mode") or "").strip().lower()
    batch_symbols = overrides.get("batch_symbols")
    is_batch = bool(batch_mode and batch_symbols)
    ensure_concurrency_limits(extra_symbol=symbol)
    names = concurrency_names_for_job(symbol, is_train=is_train)
    lease = float(os.environ.get("ITB_PREFECT_LEASE_SECONDS", "3600"))
    fine = _use_fine_execution()
    logger.info(
        "Starting Kedro job %s symbol=%s team=%s kind=%s fine=%s batch=%s slots=%s steps=%s",
        job_id,
        symbol,
        team,
        kind,
        fine,
        batch_mode or "-",
        names,
        steps,
    )
    with concurrency(names, occupy=1, timeout_seconds=None, lease_duration=lease):
        if is_batch:
            from kedro_pipeline.orchestration.batch_runner import execute_batch_job

            execute_batch_job(
                job_id,
                config_path,
                overrides,
                team=team,
                tags=tags,
                fine=fine,
            )
        elif fine:
            from kedro_pipeline.orchestration.prefect_bridge import execute_job_fine

            execute_job_fine(
                job_id,
                steps,
                config_path,
                config_overrides,
                team=team,
                tags=tags,
            )
        else:
            from kedro_pipeline.orchestration.kedro_runner import execute_job

            execute_job(job_id, steps, config_path, config_overrides)
    logger.info("Kedro job %s finished", job_id)
    return job_id


@flow(name=DAILY_PREDICT_FLOW_NAME, log_prints=True)
def daily_predict_flow(note: str = "scheduled", team: str | None = None) -> dict[str, Any]:
    """Enqueue daily_predict for all trained watchlist symbols."""
    logger = get_run_logger()
    team = normalize_team(team or default_team())
    logger.info("Daily predict batch starting note=%s team=%s", note, team)
    from backend.watchlist_service import predict_symbols

    result = asyncio.run(predict_symbols(note=note, team=team, mode="full"))
    logger.info(
        "Daily predict enqueued batch=%s jobs=%s skipped=%s",
        result.get("batch_id"),
        len(result.get("jobs") or []),
        len(result.get("skipped") or []),
    )
    return result
