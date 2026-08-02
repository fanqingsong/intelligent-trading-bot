"""Enqueue a Kedro job as a Prefect deployment run (non-blocking)."""
from __future__ import annotations

import os
import time
from typing import Any

from kedro_pipeline.orchestration.prefect_flows import FLOW_NAME
from kedro_pipeline.orchestration.teams import (
    build_run_tags,
    default_team,
    deployment_name_for_team,
    normalize_team,
)


def prefect_enabled() -> bool:
    mode = (os.environ.get("ITB_JOB_EXECUTOR") or "").strip().lower()
    if mode == "local":
        return False
    if mode == "prefect":
        return True
    return bool(os.environ.get("PREFECT_API_URL"))


def enqueue_kedro_job(
    job_id: str,
    steps: list[str],
    config_path: str,
    config_overrides: dict[str, Any] | None = None,
    *,
    team: str | None = None,
) -> str | None:
    """Trigger ``itb-kedro-job/{team}`` and return the Prefect flow run id."""
    from prefect.deployments import run_deployment

    team_name = normalize_team(team or default_team())
    deployment = f"{FLOW_NAME}/{deployment_name_for_team(team_name)}"
    tags = build_run_tags(
        job_id=job_id,
        steps=steps,
        config_overrides=config_overrides,
        team=team_name,
    )
    parameters = {
        "job_id": job_id,
        "steps": steps,
        "config_path": config_path,
        "config_overrides": config_overrides or {},
        "team": team_name,
        "tags": tags,
    }
    attempts = int(os.environ.get("ITB_PREFECT_ENQUEUE_RETRIES", "10"))
    delay_s = float(os.environ.get("ITB_PREFECT_ENQUEUE_RETRY_DELAY", "1.5"))
    last_error: Exception | None = None
    fallback_used = False
    for attempt in range(1, attempts + 1):
        try:
            flow_run = run_deployment(
                name=deployment,
                parameters=parameters,
                timeout=0,
                tags=tags,
            )
            return str(getattr(flow_run, "id", "") or "") or None
        except Exception as e:
            last_error = e
            # Fall back to default deployment once if team-specific one is missing.
            if not fallback_used and deployment != f"{FLOW_NAME}/default":
                deployment = f"{FLOW_NAME}/default"
                fallback_used = True
                continue
            if attempt >= attempts:
                break
            time.sleep(delay_s)
    assert last_error is not None
    raise last_error
