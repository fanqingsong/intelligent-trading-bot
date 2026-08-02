"""Watchlist daily-predict schedule: Postgres settings synced to Prefect.

UI still reads/writes ``schedule_settings``. The durable cron lives on Prefect
deployment ``itb-daily-predict/default`` (not APScheduler).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from shared.db.engine import get_session_factory
from backend.db.models import ScheduleSettings

log = logging.getLogger("itb.scheduler")

DAILY_PREDICT_DEPLOYMENT = "itb-daily-predict/default"


def get_schedule() -> dict[str, Any]:
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        row = session.get(ScheduleSettings, 1)
        if row is None:
            return {
                "predict_enabled": True,
                "predict_cron": "0 16 * * 1-5",
                "timezone": "Asia/Shanghai",
                "backend": "prefect" if _prefect_api_url() else "db-only",
            }
        return {
            "predict_enabled": bool(row.predict_enabled),
            "predict_cron": row.predict_cron,
            "timezone": row.timezone,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "backend": "prefect" if _prefect_api_url() else "db-only",
        }


def update_schedule(
    *,
    predict_enabled: bool | None = None,
    predict_cron: str | None = None,
    timezone: str | None = None,
) -> dict[str, Any]:
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        row = session.get(ScheduleSettings, 1)
        if row is None:
            row = ScheduleSettings(id=1)
            session.add(row)
        if predict_enabled is not None:
            row.predict_enabled = predict_enabled
        if predict_cron is not None:
            row.predict_cron = predict_cron.strip()
        if timezone is not None:
            row.timezone = timezone.strip() or "Asia/Shanghai"
        session.commit()
    reload_scheduler()
    return get_schedule()


def _prefect_api_url() -> str:
    return (os.environ.get("PREFECT_API_URL") or "").strip()


async def _sync_prefect_schedule_async() -> None:
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.schedules import CronSchedule

    settings = get_schedule()
    cron = settings.get("predict_cron") or "0 16 * * 1-5"
    tz = settings.get("timezone") or "Asia/Shanghai"
    active = bool(settings.get("predict_enabled"))
    schedule = CronSchedule(cron=cron, timezone=tz)

    async with get_client() as client:
        try:
            dep = await client.read_deployment_by_name(DAILY_PREDICT_DEPLOYMENT)
        except Exception as e:
            log.warning(
                "Prefect deployment %s not found yet (%s); will use serve defaults until worker registers it",
                DAILY_PREDICT_DEPLOYMENT,
                e,
            )
            return

        schedules = await client.read_deployment_schedules(dep.id)
        if schedules:
            await client.update_deployment_schedule(
                dep.id,
                schedules[0].id,
                active=active,
                schedule=schedule,
            )
        else:
            await client.create_deployment_schedules(dep.id, [(schedule, active)])

        if active:
            await client.resume_deployment(dep.id)
        else:
            await client.pause_deployment(dep.id)

    log.info(
        "Prefect schedule synced deployment=%s cron=%s tz=%s active=%s",
        DAILY_PREDICT_DEPLOYMENT,
        cron,
        tz,
        active,
    )


def reload_scheduler() -> None:
    """Push DB schedule settings to Prefect (no-op if API URL unset)."""
    if not _prefect_api_url():
        log.info("PREFECT_API_URL unset; schedule stored in DB only")
        return
    try:
        asyncio.run(_sync_prefect_schedule_async())
    except Exception:
        log.exception("Failed to sync schedule to Prefect")


def start_scheduler() -> None:
    """Sync schedule to Prefect on API startup."""
    reload_scheduler()


def stop_scheduler() -> None:
    """No long-lived local scheduler process."""
    return
