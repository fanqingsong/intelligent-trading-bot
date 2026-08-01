"""APScheduler for post-market daily predict."""
from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from shared.db.engine import get_session_factory
from backend.db.models import ScheduleSettings

log = logging.getLogger("itb.scheduler")

_scheduler: AsyncIOScheduler | None = None
_JOB_ID = "watchlist_daily_predict"


def get_schedule() -> dict[str, Any]:
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        row = session.get(ScheduleSettings, 1)
        if row is None:
            return {
                "predict_enabled": True,
                "predict_cron": "0 16 * * 1-5",
                "timezone": "Asia/Shanghai",
            }
        return {
            "predict_enabled": bool(row.predict_enabled),
            "predict_cron": row.predict_cron,
            "timezone": row.timezone,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
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


async def _run_daily_predict() -> None:
    from backend.watchlist_service import predict_symbols

    settings = get_schedule()
    if not settings.get("predict_enabled"):
        log.info("Scheduled predict skipped (disabled)")
        return
    log.info("Scheduled post-market predict starting")
    try:
        result = await predict_symbols(note="scheduled")
        log.info(
            "Scheduled predict enqueued batch=%s jobs=%s skipped=%s",
            result.get("batch_id"),
            len(result.get("jobs") or []),
            len(result.get("skipped") or []),
        )
    except Exception:
        log.exception("Scheduled predict failed")


def reload_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    settings = get_schedule()
    try:
        _scheduler.remove_job(_JOB_ID)
    except Exception:
        pass
    if not settings.get("predict_enabled"):
        log.info("Scheduler job removed (predict disabled)")
        return
    cron = settings.get("predict_cron") or "0 16 * * 1-5"
    tz = settings.get("timezone") or "Asia/Shanghai"
    try:
        trigger = CronTrigger.from_crontab(cron, timezone=tz)
    except Exception as e:
        log.error("Invalid cron %r: %s", cron, e)
        return
    _scheduler.add_job(
        _run_daily_predict,
        trigger=trigger,
        id=_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    log.info("Scheduler loaded cron=%s tz=%s", cron, tz)


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = AsyncIOScheduler()
    _scheduler.start()
    reload_scheduler()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
