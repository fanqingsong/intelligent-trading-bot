"""SQLAlchemy engine / session helpers."""
from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_DEFAULT_URL = "postgresql+psycopg://itb:itb@localhost:5432/itb"


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL") or _DEFAULT_URL


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(
        get_database_url(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    """Create tables already registered on ``Base.metadata`` if missing."""
    from shared.db.models import Base, MarketFrame  # noqa: F401

    engine = get_engine()
    Base.metadata.create_all(engine)

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
