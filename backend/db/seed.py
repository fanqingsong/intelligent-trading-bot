"""Ensure control-plane tables exist and seed defaults."""
from __future__ import annotations

import backend.db.models as _models  # noqa: F401 — register tables on Base.metadata
from backend.db.models import ScheduleSettings
from shared.db.engine import get_session_factory, init_db


def ensure_control_plane_db() -> None:
    """Create all registered tables (incl. control-plane) and seed schedule row."""
    init_db()
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        row = session.get(ScheduleSettings, 1)
        if row is None:
            session.add(
                ScheduleSettings(
                    id=1,
                    predict_enabled=True,
                    predict_cron="0 16 * * 1-5",
                    timezone="Asia/Shanghai",
                )
            )
            session.commit()
