"""Long-running Prefect serve process for ITB deployments.

Serves:
* ``itb-kedro-job/{team}`` — on-demand fine-grained Kedro jobs (one deployment per team)
* ``itb-daily-predict/default`` — cron-scheduled watchlist predict batch

Usage (compose)::

    python -m kedro_pipeline.orchestration.prefect_serve
"""
from __future__ import annotations

import logging
import os
import time

from prefect import serve

from kedro_pipeline.orchestration.concurrency import ensure_concurrency_limits
from kedro_pipeline.orchestration.prefect_flows import daily_predict_flow, kedro_job_flow
from kedro_pipeline.orchestration.teams import configured_teams, default_env

log = logging.getLogger("itb.prefect.serve")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _wait_for_api(timeout_s: float = 120.0) -> None:
    import asyncio

    from prefect.client.orchestration import get_client

    deadline = time.time() + timeout_s
    last: Exception | None = None
    while time.time() < deadline:
        try:

            async def _ping() -> None:
                async with get_client() as client:
                    await client.hello()

            asyncio.run(_ping())
            return
        except Exception as e:
            last = e
            time.sleep(2)
    raise RuntimeError(f"Prefect API not ready: {last}")


def _build_daily_deployment(cron: str, tz: str, predict_enabled: bool, team: str, env_name: str):
    """Build daily-predict deployment; prefer cron= to avoid schedule schema drift."""
    # Use cron= (not schedules=[Cron(...)]) — newer clients may send fields
    # (e.g. replaces) that older Prefect servers reject with HTTP 422.
    return daily_predict_flow.to_deployment(
        name="default",
        cron=cron,
        tags=["itb", "daily-predict", f"env:{env_name}"],
        paused=not predict_enabled,
        parameters={"team": team},
        description=f"Daily predict cron={cron} tz={tz}",
    )


def main() -> None:
    _wait_for_api()
    try:
        ensure_concurrency_limits()
    except Exception as e:
        log.warning("Concurrency limit upsert failed (continuing): %s", e)

    limit = int(os.environ.get("ITB_PREFECT_SERVE_LIMIT", "10"))
    cron = os.environ.get("ITB_PREDICT_CRON", "0 16 * * 1-5")
    tz = os.environ.get("ITB_PREDICT_TZ", "Asia/Shanghai")
    predict_enabled = _env_bool("ITB_PREDICT_ENABLED", True)
    teams = configured_teams()
    env_name = default_env()

    kedro_deps = [
        kedro_job_flow.to_deployment(
            name=team,
            tags=["itb", "kedro", f"team:{team}", f"env:{env_name}"],
            parameters={"team": team},
            description=f"Fine-grained Kedro jobs for team={team}",
        )
        for team in teams
    ]

    deployments = list(kedro_deps)
    try:
        deployments.append(
            _build_daily_deployment(cron, tz, predict_enabled, teams[0], env_name)
        )
    except Exception as e:
        log.warning("Daily-predict deployment build failed (serving Kedro only): %s", e)

    log.info(
        "Serving teams=%s deployments=%s limit=%s daily_cron=%s tz=%s enabled=%s execution=%s",
        teams,
        [getattr(d, "name", "?") for d in deployments],
        limit,
        cron,
        tz,
        predict_enabled,
        os.environ.get("ITB_KEDRO_EXECUTION", "fine"),
    )
    # If daily schedule apply fails at serve-time, retry Kedro-only so jobs are not stuck queued.
    try:
        serve(*deployments, limit=limit)
    except Exception as e:
        if len(deployments) > len(kedro_deps):
            log.error("Serve with daily schedule failed (%s); falling back to Kedro jobs only", e)
            serve(*kedro_deps, limit=limit)
        else:
            raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
