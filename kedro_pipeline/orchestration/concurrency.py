"""Prefect global concurrency limits for ITB jobs."""
from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger("itb.prefect.concurrency")

TRAIN_LIMIT_NAME = "itb-train"
PREDICT_LIMIT_NAME = "itb-predict"


def _train_limit() -> int:
    return max(1, int(os.environ.get("ITB_TRAIN_CONCURRENCY", "1")))


def _predict_limit() -> int:
    return max(1, int(os.environ.get("ITB_PREDICT_CONCURRENCY", "10")))


def symbol_limit_name(symbol: str) -> str:
    return f"itb-symbol:{symbol}"


def ensure_concurrency_limits(extra_symbol: str | None = None) -> None:
    """Upsert global limits used by kedro_job_flow (sync wrapper)."""
    asyncio.run(_ensure_concurrency_limits_async(extra_symbol))


async def _ensure_concurrency_limits_async(extra_symbol: str | None = None) -> None:
    from prefect.client.orchestration import get_client

    async with get_client() as client:
        await client.upsert_global_concurrency_limit_by_name(TRAIN_LIMIT_NAME, _train_limit())
        await client.upsert_global_concurrency_limit_by_name(PREDICT_LIMIT_NAME, _predict_limit())
        if extra_symbol:
            await client.upsert_global_concurrency_limit_by_name(symbol_limit_name(extra_symbol), 1)
    log.info(
        "Concurrency limits ready train=%s predict=%s symbol=%s",
        _train_limit(),
        _predict_limit(),
        extra_symbol or "-",
    )


def concurrency_names_for_job(symbol: str, *, is_train: bool) -> list[str]:
    names = [symbol_limit_name(symbol or "unknown")]
    names.append(TRAIN_LIMIT_NAME if is_train else PREDICT_LIMIT_NAME)
    return names
