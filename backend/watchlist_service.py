"""Watchlist orchestration: CRUD, train/predict jobs, signal summaries."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd
from sqlalchemy import select

from shared import (
    DAILY_PREDICT_STEPS,
    DATA_UPDATE_STEPS,
    INFER_STEPS,
    PACKAGE_ROOT,
    TRAIN_UPDATE_STEPS,
    get_config_path,
    load_config_dict,
    symbol_config_overrides,
)
from shared.db.engine import get_session_factory
from shared.db.frames import frame_exists, load_frame_tail, load_latest_frames
from backend.db.models import BatchRun, SymbolRunLink, WatchlistItem

PIPELINE_URL = os.environ.get("PIPELINE_URL", "http://localhost:8001")
ASHARE_TEMPLATE = PACKAGE_ROOT / "configs" / "config-ashare-1d.jsonc"
ALGOS = ("svc", "gb", "nn", "lc")
_TERMINAL_LINK = frozenset({"completed", "failed", "skipped"})
_TRAIN_BATCH_POLL_S = float(os.environ.get("ITB_TRAIN_BATCH_POLL_S", "5"))

# In-process processors for durable train-all batches (checkpointed in Postgres).
_train_batch_tasks: dict[int, asyncio.Task] = {}
_train_batch_teams: dict[int, str | None] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def template_config_path() -> str:
    if ASHARE_TEMPLATE.exists():
        return str(ASHARE_TEMPLATE)
    return str(get_config_path())


def _data_folder_override() -> dict[str, Any]:
    try:
        current = load_config_dict()
        folder = current.get("data_folder") or os.environ.get("DATA_FOLDER") or "/app/data"
    except Exception:
        folder = os.environ.get("DATA_FOLDER") or "/app/data"
    return {"data_folder": folder}


def build_overrides(symbol: str, *, train: bool) -> dict[str, Any]:
    overrides = symbol_config_overrides(symbol)
    overrides.update(_data_folder_override())
    overrides["train"] = train
    return overrides


def build_batch_overrides(symbols: list[str], *, batch_mode: str) -> dict[str, Any]:
    """Overrides for a multi-symbol watchlist job (symbol=_watchlist concurrency key)."""
    codes = [str(s).zfill(6) for s in symbols if str(s).strip()]
    overrides: dict[str, Any] = {
        "symbol": "_watchlist",
        "description": f"Watchlist batch {batch_mode} ({len(codes)} symbols)",
        "data_sources": [{"folder": c, "file": "klines", "column_prefix": ""} for c in codes],
        "mlflow_registry_prefix": "itb_watchlist_",
        "mlflow_experiment_name": "itb_watchlist",
        "train": False,
        "batch_mode": batch_mode,
        "batch_symbols": codes,
    }
    overrides.update(_data_folder_override())
    return overrides


def list_items() -> list[dict[str, Any]]:
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        rows = session.scalars(select(WatchlistItem).order_by(WatchlistItem.created_at.asc())).all()
        return [_item_dict(r) for r in rows]


def _item_dict(item: WatchlistItem) -> dict[str, Any]:
    return {
        "symbol": item.symbol,
        "name": item.name,
        "exchange": item.exchange,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "last_trained_at": item.last_trained_at.isoformat() if item.last_trained_at else None,
        "last_predicted_at": item.last_predicted_at.isoformat() if item.last_predicted_at else None,
        "train_status": item.train_status,
        "predict_status": item.predict_status,
        "last_error": item.last_error,
        "last_train_job_id": item.last_train_job_id,
        "last_predict_job_id": item.last_predict_job_id,
    }


def add_item(query: str) -> dict[str, Any]:
    from shared.collectors.collector_ashare import (
        ashare_exchange,
        resolve_ashare_query,
        search_ashare_stocks,
    )

    code = resolve_ashare_query(query)
    name = ""
    try:
        hits = search_ashare_stocks(code, limit=1)
        if hits:
            name = hits[0].get("name") or ""
    except Exception:
        pass

    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        existing = session.get(WatchlistItem, code)
        if existing:
            return _item_dict(existing)
        item = WatchlistItem(
            symbol=code,
            name=name,
            exchange=ashare_exchange(code),
            train_status="untrained",
            predict_status="idle",
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return _item_dict(item)


def delete_item(symbol: str) -> bool:
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        item = session.get(WatchlistItem, symbol)
        if not item:
            return False
        session.delete(item)
        session.commit()
        return True


def import_index(index: str) -> dict[str, Any]:
    """Bulk-add index constituents to the watchlist (idempotent)."""
    from shared.collectors.collector_ashare import (
        ashare_exchange,
        fetch_index_constituents,
        resolve_index_preset,
    )

    preset = resolve_index_preset(index)
    constituents = fetch_index_constituents(index)
    codes = [row["code"] for row in constituents]

    added: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        existing_codes = set(
            session.scalars(
                select(WatchlistItem.symbol).where(WatchlistItem.symbol.in_(codes))
            ).all()
        ) if codes else set()

        new_items: list[WatchlistItem] = []
        for row in constituents:
            code = row["code"]
            if code in existing_codes:
                skipped.append({"symbol": code, "reason": "exists"})
                continue
            item = WatchlistItem(
                symbol=code,
                name=row.get("name") or "",
                exchange=row.get("exchange") or ashare_exchange(code),
                train_status="untrained",
                predict_status="idle",
            )
            new_items.append(item)
            existing_codes.add(code)

        if new_items:
            session.add_all(new_items)
            session.commit()
            added = [_item_dict(item) for item in new_items]
        else:
            session.rollback()

    return {
        "index": preset["code"],
        "index_name": preset["name"],
        "total": len(constituents),
        "added": len(added),
        "skipped": len(skipped),
        "items": added,
        "skipped_items": skipped,
    }


async def _enqueue_job(
    steps: list[str],
    overrides: dict[str, Any],
    *,
    team: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "steps": steps,
        "config_path": template_config_path(),
        "config_overrides": overrides,
    }
    if team:
        payload["team"] = team
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{PIPELINE_URL}/internal/jobs", json=payload)
    if resp.status_code >= 400:
        raise RuntimeError(resp.text)
    return resp.json()


async def train_symbol(symbol: str, *, team: str | None = None) -> dict[str, Any]:
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        item = session.get(WatchlistItem, symbol)
        if not item:
            raise KeyError(symbol)
        # Same-symbol mutex at control-plane level (Prefect also enforces itb-symbol:*).
        if item.train_status in ("queued", "running") and item.last_train_job_id:
            return {
                "symbol": symbol,
                "job_id": item.last_train_job_id,
                "steps": list(TRAIN_UPDATE_STEPS),
                "kind": "train",
                "deduped": True,
                "status": item.train_status,
            }
        item.train_status = "queued"
        item.predict_status = "idle"
        item.last_error = ""
        session.commit()

    overrides = build_overrides(symbol, train=True)
    try:
        job = await _enqueue_job(list(TRAIN_UPDATE_STEPS), overrides, team=team)
    except Exception as e:
        with SessionLocal() as session:
            item = session.get(WatchlistItem, symbol)
            if item:
                item.train_status = "failed"
                item.last_error = str(e)
                session.commit()
        raise

    with SessionLocal() as session:
        item = session.get(WatchlistItem, symbol)
        if item:
            item.train_status = "running"
            item.last_train_job_id = job.get("job_id", "")
            session.commit()
    return {
        "symbol": symbol,
        "job_id": job.get("job_id"),
        "steps": job.get("steps"),
        "kind": "train",
        "team": job.get("team") or team,
    }


def _batch_last_error(note: str) -> str:
    """Surface the latest processor_error line from batch.note for the UI."""
    if not note:
        return ""
    for line in reversed(note.splitlines()):
        line = line.strip()
        if line.startswith("processor_error:"):
            return line.removeprefix("processor_error:").strip()
    return ""


def _batch_progress(session, batch_id: int) -> dict[str, Any]:
    batch = session.get(BatchRun, batch_id)
    links = list(
        session.scalars(
            select(SymbolRunLink)
            .where(SymbolRunLink.batch_id == batch_id)
            .order_by(SymbolRunLink.id.asc())
        ).all()
    )
    counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0, "skipped": 0}
    current_symbol = ""
    for link in links:
        counts[link.status] = counts.get(link.status, 0) + 1
        if not current_symbol and link.status in ("queued", "running"):
            current_symbol = link.symbol
    note = batch.note if batch else ""
    return {
        "batch_id": batch_id,
        "kind": batch.kind if batch else "train",
        "status": batch.status if batch else "unknown",
        "note": note,
        "last_error": _batch_last_error(note),
        "total": len(links),
        "current_symbol": current_symbol,
        **counts,
        "steps": list(TRAIN_UPDATE_STEPS),
    }


def find_open_train_batch_id() -> int | None:
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        batch = session.scalars(
            select(BatchRun)
            .where(BatchRun.kind == "train", BatchRun.status.in_(("queued", "running")))
            .order_by(BatchRun.id.desc())
        ).first()
        return batch.id if batch else None


def active_train_batch() -> dict[str, Any] | None:
    batch_id = find_open_train_batch_id()
    if batch_id is None:
        return None
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        return _batch_progress(session, batch_id)


def _ensure_train_batch_processor(batch_id: int, *, team: str | None = None) -> None:
    if team is not None or batch_id not in _train_batch_teams:
        _train_batch_teams[batch_id] = team
    task = _train_batch_tasks.get(batch_id)
    if task is not None and not task.done():
        return
    _train_batch_tasks[batch_id] = asyncio.create_task(
        _process_train_batch(batch_id, team=_train_batch_teams.get(batch_id)),
        name=f"train-batch-{batch_id}",
    )


async def resume_open_train_batches() -> list[int]:
    """Re-attach processors for unfinished train-all batches (crash recovery)."""
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        rows = list(
            session.scalars(
                select(BatchRun).where(
                    BatchRun.kind == "train",
                    BatchRun.status.in_(("queued", "running")),
                )
            ).all()
        )
        batch_ids = [b.id for b in rows]
    for batch_id in batch_ids:
        _ensure_train_batch_processor(batch_id)
    return batch_ids


def _restore_train_status_after_cancel(item: WatchlistItem, *, error: str) -> None:
    """Reset watchlist train status after cancel; keep ready if previously trained."""
    item.train_status = "ready" if item.last_trained_at else "untrained"
    item.last_error = error


async def _cancel_pipeline_job(job_id: str) -> dict[str, Any] | None:
    """Cancel a pipeline/Prefect job. Best-effort; returns job payload or None."""
    if not job_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{PIPELINE_URL}/internal/jobs/{job_id}/cancel")
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            print(f"WARN: cancel job {job_id}: {resp.status_code} {resp.text[:200]}")
            return None
        return resp.json()
    except Exception as e:
        print(f"WARN: cancel job {job_id} failed: {e}")
        return None


async def cancel_open_train_batch() -> dict[str, Any] | None:
    """Cancel the open train-all batch: stop processor, cancel jobs, skip links."""
    batch_id = find_open_train_batch_id()
    if batch_id is None:
        return None

    task = _train_batch_tasks.pop(batch_id, None)
    _train_batch_teams.pop(batch_id, None)
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        links = list(
            session.scalars(
                select(SymbolRunLink).where(
                    SymbolRunLink.batch_id == batch_id,
                    SymbolRunLink.status.in_(("queued", "running")),
                )
            ).all()
        )
        job_ids = [link.job_id for link in links if link.job_id]
        # Also cancel any orphaned in-flight train jobs (e.g. leftover from prior batch).
        for item in session.scalars(
            select(WatchlistItem).where(WatchlistItem.train_status.in_(("queued", "running")))
        ).all():
            if item.last_train_job_id and item.last_train_job_id not in job_ids:
                job_ids.append(item.last_train_job_id)
        link_ids = [link.id for link in links]

    for job_id in job_ids:
        await _cancel_pipeline_job(job_id)

    with SessionLocal() as session:
        for link_id in link_ids:
            link = session.get(SymbolRunLink, link_id)
            if not link or link.status in _TERMINAL_LINK:
                continue
            link.status = "skipped"
            link.error = "cancelled by user"
            item = session.get(WatchlistItem, link.symbol)
            if item and item.train_status in ("queued", "running"):
                _restore_train_status_after_cancel(item, error="batch cancelled")
        # Clear any remaining in-flight train flags outside skipped links.
        for item in session.scalars(
            select(WatchlistItem).where(WatchlistItem.train_status.in_(("queued", "running")))
        ).all():
            _restore_train_status_after_cancel(item, error="batch cancelled")
        batch = session.get(BatchRun, batch_id)
        if batch and batch.status in ("queued", "running"):
            note = batch.note or ""
            if "cancelled by user" not in note:
                batch.note = (note + "\ncancelled by user").strip()[:2000]
            _maybe_finish_batch(session, batch_id)
        session.commit()
        return _batch_progress(session, batch_id)


_PREDICT_BATCH_KINDS = ("predict", "download")


def find_open_predict_batch_id() -> int | None:
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        batch = session.scalars(
            select(BatchRun)
            .where(
                BatchRun.kind.in_(_PREDICT_BATCH_KINDS),
                BatchRun.status.in_(("queued", "running")),
            )
            .order_by(BatchRun.id.desc())
        ).first()
        return batch.id if batch else None


def _steps_for_batch_kind(kind: str) -> list[str]:
    if kind == "download":
        return list(DATA_UPDATE_STEPS)
    return list(DAILY_PREDICT_STEPS)


def active_predict_batch() -> dict[str, Any] | None:
    batch_id = find_open_predict_batch_id()
    if batch_id is None:
        return None
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        progress = _batch_progress(session, batch_id)
        batch = session.get(BatchRun, batch_id)
        progress["steps"] = _steps_for_batch_kind(batch.kind if batch else "predict")
        return progress


async def cancel_open_predict_batch() -> dict[str, Any] | None:
    """Cancel open predict batch and any orphaned queued/running predict jobs."""
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        batch_id = find_open_predict_batch_id()
        job_ids: list[str] = []
        link_ids: list[int] = []
        if batch_id is not None:
            links = list(
                session.scalars(
                    select(SymbolRunLink).where(
                        SymbolRunLink.batch_id == batch_id,
                        SymbolRunLink.status.in_(("queued", "running")),
                    )
                ).all()
            )
            job_ids = [link.job_id for link in links if link.job_id]
            link_ids = [link.id for link in links]

        for item in session.scalars(
            select(WatchlistItem).where(WatchlistItem.predict_status.in_(("queued", "running")))
        ).all():
            if item.last_predict_job_id and item.last_predict_job_id not in job_ids:
                job_ids.append(item.last_predict_job_id)

        if batch_id is None and not job_ids:
            return None

    for job_id in job_ids:
        await _cancel_pipeline_job(job_id)

    with SessionLocal() as session:
        for link_id in link_ids:
            link = session.get(SymbolRunLink, link_id)
            if not link or link.status in _TERMINAL_LINK:
                continue
            link.status = "skipped"
            link.error = "cancelled by user"
        for item in session.scalars(
            select(WatchlistItem).where(WatchlistItem.predict_status.in_(("queued", "running")))
        ).all():
            item.predict_status = "idle"
            item.last_error = "cancelled by user"
        if batch_id is not None:
            batch = session.get(BatchRun, batch_id)
            if batch and batch.status in ("queued", "running"):
                note = batch.note or ""
                if "cancelled by user" not in note:
                    batch.note = (note + "\ncancelled by user").strip()[:2000]
                _maybe_finish_batch(session, batch_id)
        session.commit()
        if batch_id is not None:
            progress = _batch_progress(session, batch_id)
            batch = session.get(BatchRun, batch_id)
            progress["steps"] = _steps_for_batch_kind(batch.kind if batch else "predict")
            return progress
        return {
            "batch_id": None,
            "kind": "predict",
            "status": "cancelled",
            "total": 0,
            "queued": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "current_symbol": "",
            "steps": list(DAILY_PREDICT_STEPS),
        }


async def cancel_train_symbol(symbol: str) -> dict[str, Any]:
    """Cancel in-flight train for one symbol (single-run or current batch slot)."""
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        item = session.get(WatchlistItem, symbol)
        if not item:
            raise KeyError(symbol)
        was_inflight = item.train_status in ("queued", "running")
        open_links = list(
            session.scalars(
                select(SymbolRunLink)
                .where(
                    SymbolRunLink.symbol == symbol,
                    SymbolRunLink.status.in_(("queued", "running")),
                )
                .order_by(SymbolRunLink.id.desc())
            ).all()
        )
        job_ids: list[str] = []
        if was_inflight and item.last_train_job_id:
            job_ids.append(item.last_train_job_id)
        for link in open_links:
            if link.job_id and link.job_id not in job_ids:
                job_ids.append(link.job_id)
        link_ids = [link.id for link in open_links]
        batch_ids = list({link.batch_id for link in open_links if link.batch_id})
        current_status = item.train_status

    if not was_inflight and not job_ids and not link_ids:
        return {
            "symbol": symbol,
            "status": current_status,
            "job_id": "",
            "cancelled": False,
            "message": "no in-flight train",
        }

    for jid in job_ids:
        await _cancel_pipeline_job(jid)

    with SessionLocal() as session:
        item = session.get(WatchlistItem, symbol)
        if item and item.train_status in ("queued", "running"):
            _restore_train_status_after_cancel(item, error="cancelled by user")
        for link_id in link_ids:
            link = session.get(SymbolRunLink, link_id)
            if link and link.status not in _TERMINAL_LINK:
                link.status = "skipped"
                link.error = "cancelled by user"
                _maybe_finish_batch(session, link.batch_id)
        for batch_id in batch_ids:
            _maybe_finish_batch(session, batch_id)
        session.commit()
        item = session.get(WatchlistItem, symbol)
        return {
            "symbol": symbol,
            "status": item.train_status if item else "idle",
            "job_id": job_ids[0] if job_ids else "",
            "cancelled": True,
        }


async def train_symbols(
    symbols: list[str] | None = None,
    note: str = "manual",
    *,
    team: str | None = None,
) -> dict[str, Any]:
    """Train all (or selected) watchlist symbols sequentially with checkpoint resume.

    Progress is persisted in ``batch_runs`` / ``symbol_run_links``. On API restart,
    ``resume_open_train_batches`` continues from the first unfinished symbol.
    """
    open_id = find_open_train_batch_id()
    if open_id is not None:
        _ensure_train_batch_processor(open_id, team=team)
        SessionLocal = get_session_factory()
        with SessionLocal() as session:
            progress = _batch_progress(session, open_id)
        progress["resumed"] = True
        progress["deduped"] = True
        return progress

    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        if symbols:
            items = [session.get(WatchlistItem, s) for s in symbols]
            items = [i for i in items if i is not None]
        else:
            items = list(
                session.scalars(select(WatchlistItem).order_by(WatchlistItem.created_at.asc())).all()
            )
        if not items:
            return {
                "batch_id": None,
                "status": "completed",
                "total": 0,
                "queued": 0,
                "running": 0,
                "completed": 0,
                "failed": 0,
                "skipped": 0,
                "current_symbol": "",
                "resumed": False,
                "steps": list(TRAIN_UPDATE_STEPS),
            }

        batch = BatchRun(kind="train", status="running", note=note)
        session.add(batch)
        session.flush()
        for item in items:
            session.add(
                SymbolRunLink(
                    batch_id=batch.id,
                    symbol=item.symbol,
                    status="queued",
                )
            )
            item.train_status = "queued"
            item.predict_status = "idle"
            item.last_error = ""
        session.commit()
        batch_id = batch.id
        progress = _batch_progress(session, batch_id)

    _ensure_train_batch_processor(batch_id, team=team)
    progress["resumed"] = False
    progress["deduped"] = False
    return progress


async def _fetch_job(
    job_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    """Fetch a pipeline job by id. Optional shared ``client`` avoids per-call pools."""
    if not job_id:
        return None
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=10.0) as own:
                resp = await own.get(f"{PIPELINE_URL}/internal/jobs/{job_id}")
        else:
            resp = await client.get(f"{PIPELINE_URL}/internal/jobs/{job_id}")
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


async def _process_train_batch(batch_id: int, *, team: str | None = None) -> None:
    """Walk SymbolRunLinks in order: enqueue one, wait until terminal, then next."""
    SessionLocal = get_session_factory()
    # Drop stale processor_error lines from earlier crash loops once we are running again.
    with SessionLocal() as session:
        batch = session.get(BatchRun, batch_id)
        if batch and batch.note and "processor_error:" in batch.note:
            cleaned = "\n".join(
                ln for ln in batch.note.splitlines() if not ln.strip().startswith("processor_error:")
            ).strip()
            batch.note = cleaned
            session.commit()
    try:
        while True:
            with SessionLocal() as session:
                batch = session.get(BatchRun, batch_id)
                if not batch or batch.status not in ("queued", "running"):
                    return
                if batch.status == "queued":
                    batch.status = "running"
                    session.commit()
                link = session.scalars(
                    select(SymbolRunLink)
                    .where(
                        SymbolRunLink.batch_id == batch_id,
                        SymbolRunLink.status.in_(("queued", "running")),
                    )
                    .order_by(SymbolRunLink.id.asc())
                ).first()
                if link is None:
                    _maybe_finish_batch(session, batch_id)
                    session.commit()
                    return
                link_id = link.id
                symbol = link.symbol
                job_id = link.job_id or ""
                link_status = link.status

            if link_status == "queued" and not job_id:
                overrides = build_overrides(symbol, train=True)
                try:
                    job = await _enqueue_job(list(TRAIN_UPDATE_STEPS), overrides, team=team)
                    job_id = job.get("job_id", "")
                except Exception as e:
                    with SessionLocal() as session:
                        link = session.get(SymbolRunLink, link_id)
                        item = session.get(WatchlistItem, symbol)
                        if link:
                            link.status = "failed"
                            link.error = str(e)
                        if item:
                            item.train_status = "failed"
                            item.last_error = str(e)
                        _maybe_finish_batch(session, batch_id)
                        session.commit()
                    continue

                with SessionLocal() as session:
                    link = session.get(SymbolRunLink, link_id)
                    item = session.get(WatchlistItem, symbol)
                    if link:
                        link.job_id = job_id
                        link.status = "running"
                    if item:
                        item.train_status = "running"
                        item.last_train_job_id = job_id
                        item.last_error = ""
                    session.commit()

            # Wait for in-flight job (or re-check after crash).
            data = await _fetch_job(job_id) if job_id else None
            if data is None and job_id:
                # Job record missing after outage — re-queue this symbol.
                with SessionLocal() as session:
                    link = session.get(SymbolRunLink, link_id)
                    item = session.get(WatchlistItem, symbol)
                    if link and link.status not in _TERMINAL_LINK:
                        link.job_id = ""
                        link.status = "queued"
                        link.error = "job missing after restart; re-queued"
                    if item and item.train_status in ("queued", "running"):
                        item.train_status = "queued"
                        item.last_train_job_id = ""
                    session.commit()
                await asyncio.sleep(_TRAIN_BATCH_POLL_S)
                continue

            if data is None:
                await asyncio.sleep(_TRAIN_BATCH_POLL_S)
                continue

            status = str(data.get("status") or "")
            error = str(data.get("error") or "")
            if status in ("completed", "failed", "cancelled"):
                sync_job_status(symbol, job_id, "train", status, error)
                # Ensure we advance even if sync missed the link.
                with SessionLocal() as session:
                    link = session.get(SymbolRunLink, link_id)
                    if link and link.status not in _TERMINAL_LINK:
                        link.status = "skipped" if status == "cancelled" else status
                        if error:
                            link.error = error
                        _maybe_finish_batch(session, batch_id)
                        session.commit()
                continue

            if status in ("queued", "running", ""):
                sync_job_status(symbol, job_id, "train", status or "running", error)
            await asyncio.sleep(_TRAIN_BATCH_POLL_S)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"WARN: train batch {batch_id} processor failed: {e}")
        with SessionLocal() as session:
            batch = session.get(BatchRun, batch_id)
            if batch and batch.status in ("queued", "running"):
                # Leave batch open so startup resume can retry; avoid note spam on crash loops.
                err_line = f"processor_error: {e}"
                note = batch.note or ""
                if err_line not in note:
                    batch.note = (note + "\n" + err_line).strip()[:2000]
                session.commit()
    finally:
        task = _train_batch_tasks.get(batch_id)
        if task is asyncio.current_task():
            _train_batch_tasks.pop(batch_id, None)


def _is_trained(item: WatchlistItem) -> bool:
    if item.train_status in ("ready", "completed"):
        return True
    # Heuristic: has signals in DB
    return frame_exists(item.symbol, "signals")


def _predict_mode_plan(mode: str | None) -> tuple[str, list[str], bool]:
    """Return (batch_kind, steps, require_trained) for a predict API mode."""
    m = (mode or "full").strip().lower()
    if m == "data":
        return "download", list(DATA_UPDATE_STEPS), False
    if m == "predict":
        return "predict", list(INFER_STEPS), True
    return "predict", list(DAILY_PREDICT_STEPS), True


def _attach_job_to_symbols(
    session,
    *,
    batch_id: int,
    symbols: list[str],
    job_id: str,
) -> None:
    for symbol in symbols:
        item = session.get(WatchlistItem, symbol)
        link = session.scalars(
            select(SymbolRunLink).where(
                SymbolRunLink.batch_id == batch_id,
                SymbolRunLink.symbol == symbol,
            )
        ).first()
        if item:
            item.predict_status = "running"
            item.last_predict_job_id = job_id
        if link is None:
            session.add(
                SymbolRunLink(
                    batch_id=batch_id,
                    symbol=symbol,
                    job_id=job_id,
                    status="running",
                )
            )
        else:
            link.job_id = job_id
            link.status = "running"


async def predict_symbols(
    symbols: list[str] | None = None,
    note: str = "manual",
    *,
    team: str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    mode_resolved = (mode or "full").strip().lower()
    batch_kind, steps, require_trained = _predict_mode_plan(mode_resolved)
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        if symbols:
            items = [session.get(WatchlistItem, s) for s in symbols]
            items = [i for i in items if i is not None]
        else:
            items = list(session.scalars(select(WatchlistItem).order_by(WatchlistItem.created_at.asc())).all())

        batch = BatchRun(kind=batch_kind, status="running", note=note)
        session.add(batch)
        session.flush()

        planned: list[tuple[str, WatchlistItem]] = []
        skipped: list[dict[str, str]] = []
        for item in items:
            if require_trained and not _is_trained(item):
                skipped.append({"symbol": item.symbol, "reason": "untrained"})
                item.predict_status = "skipped"
                item.last_error = "Model not trained; run 更新模型 first"
                session.add(
                    SymbolRunLink(
                        batch_id=batch.id,
                        symbol=item.symbol,
                        status="skipped",
                        error="untrained",
                    )
                )
                continue
            planned.append((item.symbol, item))
            item.predict_status = "queued"
            item.last_error = ""
        session.commit()
        batch_id = batch.id
        planned_symbols = [s for s, _ in planned]

    jobs: list[dict[str, Any]] = []
    use_batch = len(planned_symbols) > 1

    if use_batch:
        # One Prefect job for the whole watchlist slice.
        batch_mode = mode_resolved if mode_resolved in ("data", "predict", "full") else "full"
        if batch_mode == "data":
            steps = list(DATA_UPDATE_STEPS)
        elif batch_mode == "predict":
            steps = list(INFER_STEPS)
        else:
            steps = list(DAILY_PREDICT_STEPS)
        overrides = build_batch_overrides(planned_symbols, batch_mode=batch_mode)
        try:
            job = await _enqueue_job(list(steps), overrides, team=team)
            job_id = job.get("job_id", "")
            with SessionLocal() as session:
                _attach_job_to_symbols(
                    session, batch_id=batch_id, symbols=planned_symbols, job_id=job_id
                )
                session.commit()
            jobs.append(
                {
                    "symbol": "_watchlist",
                    "job_id": job_id,
                    "status": "queued",
                    "batch": True,
                    "symbols": list(planned_symbols),
                }
            )
        except Exception as e:
            with SessionLocal() as session:
                for symbol in planned_symbols:
                    item = session.get(WatchlistItem, symbol)
                    if item:
                        item.predict_status = "failed"
                        item.last_error = str(e)
                    session.add(
                        SymbolRunLink(
                            batch_id=batch_id,
                            symbol=symbol,
                            status="failed",
                            error=str(e),
                        )
                    )
                session.commit()
            skipped.extend({"symbol": s, "reason": str(e)} for s in planned_symbols)
    else:
        # Single-symbol path (per-row buttons / tiny watchlist).
        for symbol in planned_symbols:
            overrides = build_overrides(symbol, train=False)
            try:
                job = await _enqueue_job(list(steps), overrides, team=team)
                job_id = job.get("job_id", "")
                with SessionLocal() as session:
                    _attach_job_to_symbols(
                        session, batch_id=batch_id, symbols=[symbol], job_id=job_id
                    )
                    session.commit()
                jobs.append({"symbol": symbol, "job_id": job_id, "status": "queued"})
            except Exception as e:
                with SessionLocal() as session:
                    item = session.get(WatchlistItem, symbol)
                    if item:
                        item.predict_status = "failed"
                        item.last_error = str(e)
                    session.add(
                        SymbolRunLink(
                            batch_id=batch_id,
                            symbol=symbol,
                            status="failed",
                            error=str(e),
                        )
                    )
                    session.commit()
                skipped.append({"symbol": symbol, "reason": str(e)})

    with SessionLocal() as session:
        batch = session.get(BatchRun, batch_id)
        if batch:
            if not jobs and skipped:
                batch.status = "completed"
                batch.finished_at = _utcnow()
            session.commit()

    return {
        "batch_id": batch_id,
        "jobs": jobs,
        "skipped": skipped,
        "steps": list(steps),
        "mode": mode_resolved,
        "kind": batch_kind,
        "batched": use_batch,
    }


def _algo_recommendation(row: pd.Series, algo: str) -> str:
    buy_col = f"buy_signal_{algo}"
    sell_col = f"sell_signal_{algo}"
    buy = bool(row.get(buy_col, False)) if buy_col in row.index else False
    sell = bool(row.get(sell_col, False)) if sell_col in row.index else False
    if buy and not sell:
        return "BUY"
    if sell and not buy:
        return "SELL"
    return "HOLD"


def _frame_day(df: pd.DataFrame, time_column: str = "timestamp"):
    """Calendar day of the last row, or None."""
    if df.empty or time_column not in df.columns:
        return None
    ts = pd.Timestamp(df.iloc[-1][time_column])
    if pd.isna(ts):
        return None
    return ts.date()


def _row_vote(row: pd.Series, columns) -> str:
    vote = str(row.get("vote_label") or "HOLD") if "vote_label" in columns else "HOLD"
    if vote in ("BUY", "SELL", "HOLD"):
        return vote
    buy = bool(row.get("buy_signal_vote", False)) if "buy_signal_vote" in columns else False
    sell = bool(row.get("sell_signal_vote", False)) if "sell_signal_vote" in columns else False
    if buy and not sell:
        return "BUY"
    if sell and not buy:
        return "SELL"
    return "HOLD"


def _empty_signal_summary(symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "available": False,
        "latest": None,
        "recommendation": "HOLD",
        "algorithms": {},
        "vote": "HOLD",
        "fresh": False,
        "has_signal": False,
        "timestamp": None,
        "close": None,
    }


def _summarize_signal_frames(
    symbol: str,
    df: pd.DataFrame,
    klines: pd.DataFrame,
) -> dict[str, Any]:
    """Build board summary from already-loaded latest signal/kline frames."""
    summary = _empty_signal_summary(symbol)

    close = None
    if not klines.empty and "close" in klines.columns and pd.notna(klines.iloc[-1].get("close")):
        close = float(klines.iloc[-1]["close"])

    if df.empty:
        if close is not None:
            summary["close"] = close
        return summary

    row = df.iloc[-1]
    latest = json.loads(pd.DataFrame([row]).to_json(orient="records", date_format="iso"))[0]
    if close is None and "close" in df.columns and pd.notna(row.get("close")):
        close = float(row["close"])

    algos: dict[str, Any] = {}
    for algo in ALGOS:
        score_col = f"trade_score_{algo}"
        score = row.get(score_col) if score_col in df.columns else None
        if score is not None and isinstance(score, float) and pd.isna(score):
            score = None
        elif score is not None:
            try:
                score = float(score)
            except Exception:
                score = None
        algos[algo] = {
            "recommendation": _algo_recommendation(row, algo),
            "trade_score": score,
        }

    vote = _row_vote(row, df.columns)
    signal_day = _frame_day(df)
    kline_day = _frame_day(klines)
    # No klines yet → treat signals as the latest market view.
    fresh = kline_day is None or signal_day == kline_day
    if not fresh:
        # Stale prediction must not look like a live BUY/SELL on the board.
        vote = "HOLD"
        for algo in algos.values():
            algo["recommendation"] = "HOLD"

    has_signal = fresh and vote in ("BUY", "SELL")
    summary.update({
        "available": True,
        "latest": latest,
        "algorithms": algos,
        "vote": vote,
        "recommendation": vote,
        "close": close,
        "fresh": fresh,
        "has_signal": has_signal,
        # Timestamp of the latest prediction row (BUY/SELL/HOLD); empty frames stay null.
        "timestamp": latest.get("timestamp"),
    })
    return summary


def symbol_signals(symbol: str) -> dict[str, Any]:
    """Latest board summary for one symbol.

    Board semantics:
    - ``close`` comes from the latest kline when available (else signals).
    - A trade signal (BUY/SELL) is only ``has_signal`` when predictions align with the
      latest kline day. Fresh/stale HOLD still returns ``available`` + ``timestamp``.
    """
    df = load_frame_tail(symbol, "signals", n=1)
    klines = load_frame_tail(symbol, "klines", n=1)
    return _summarize_signal_frames(symbol, df, klines)


def board_signals(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Batch board summaries for many symbols (one DB round-trip)."""
    symbols = [str(s).strip() for s in symbols if str(s).strip()]
    if not symbols:
        return {}
    latest = load_latest_frames(symbols, ["signals", "klines"])
    empty = pd.DataFrame()
    return {
        sym: _summarize_signal_frames(
            sym,
            latest.get((sym, "signals"), empty),
            latest.get((sym, "klines"), empty),
        )
        for sym in symbols
    }


def sync_job_status(symbol: str, job_id: str, kind: str, status: str, error: str = "") -> None:
    """Update watchlist item after a pipeline job finishes (called by API pollers / webhook)."""
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        item = session.get(WatchlistItem, symbol)
        if not item:
            return
        if kind == "train":
            if status == "completed":
                item.train_status = "ready"
                item.last_trained_at = _utcnow()
                item.last_predicted_at = _utcnow()
                item.predict_status = "ready"
                item.last_error = ""
            elif status == "failed":
                item.train_status = "failed"
                item.last_error = error or "train failed"
            elif status == "cancelled":
                _restore_train_status_after_cancel(item, error=error or "cancelled by user")
            elif status == "running":
                item.train_status = "running"
            elif status == "queued":
                item.train_status = "queued"
        elif kind in ("predict", "download"):
            if status == "completed":
                item.predict_status = "ready" if kind == "predict" else "idle"
                if kind == "predict":
                    item.last_predicted_at = _utcnow()
                item.last_error = ""
            elif status == "failed":
                item.predict_status = "failed"
                item.last_error = error or ("predict failed" if kind == "predict" else "data update failed")
            elif status == "cancelled":
                item.predict_status = "idle"
                item.last_error = error or "cancelled by user"
            elif status == "running":
                item.predict_status = "running"
            elif status == "queued":
                item.predict_status = "queued"
        if job_id:
            if kind == "train":
                item.last_train_job_id = job_id
            else:
                item.last_predict_job_id = job_id

        if job_id and status in ("completed", "failed", "cancelled", "running", "queued"):
            # Scope by symbol so shared batch job_ids do not clobber sibling links.
            links = list(
                session.scalars(
                    select(SymbolRunLink).where(
                        SymbolRunLink.job_id == job_id,
                        SymbolRunLink.symbol == symbol,
                    )
                ).all()
            )
            for link in links:
                link.status = "skipped" if status == "cancelled" else status
                if error:
                    link.error = error
                _maybe_finish_batch(session, link.batch_id)
        session.commit()


def _maybe_finish_batch(session, batch_id: int | None) -> None:
    if not batch_id:
        return
    batch = session.get(BatchRun, batch_id)
    if not batch or batch.status not in ("running", "queued"):
        return
    links = list(session.scalars(select(SymbolRunLink).where(SymbolRunLink.batch_id == batch_id)).all())
    if not links:
        return
    terminal = {"completed", "failed", "skipped"}
    if not all(link.status in terminal for link in links):
        return
    batch.status = "completed" if any(link.status == "completed" for link in links) else "failed"
    if all(link.status == "failed" for link in links):
        batch.status = "failed"
    elif all(link.status in ("completed", "skipped") for link in links):
        batch.status = "completed"
    else:
        batch.status = "completed"
    batch.finished_at = _utcnow()


def _parse_job_steps(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(s) for s in raw]
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(s) for s in data]
        except Exception:
            pass
    return []


def _job_progress_payload(kind: str, job: dict[str, Any]) -> dict[str, Any]:
    steps = _parse_job_steps(job.get("steps"))
    # Download-only jobs reuse predict_status / last_predict_job_id; infer kind from steps.
    resolved_kind = kind
    if kind != "train" and steps == list(DATA_UPDATE_STEPS):
        resolved_kind = "download"
    current = str(job.get("current_step") or "").strip()
    try:
        progress = int(float(job.get("progress") or 0))
    except Exception:
        progress = 0
    progress = max(0, min(100, progress))
    step_index = steps.index(current) + 1 if current and current in steps else 0
    return {
        "kind": resolved_kind,
        "job_id": job.get("job_id") or "",
        "status": str(job.get("status") or ""),
        "current_step": current,
        "progress": progress,
        "steps": steps,
        "step_index": step_index,
        "step_total": len(steps),
        "prefect_ui_url": job.get("prefect_ui_url") or None,
        "error": str(job.get("error") or ""),
    }


async def refresh_running_statuses() -> None:
    """Poll Redis/pipeline for in-flight watchlist jobs and sync DB status."""
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        items = list(session.scalars(select(WatchlistItem)).all())
        snapshot = [
            (i.symbol, i.train_status, i.last_train_job_id, i.predict_status, i.last_predict_job_id)
            for i in items
        ]
        open_links = list(
            session.scalars(
                select(SymbolRunLink).where(SymbolRunLink.status.in_(("queued", "running")))
            ).all()
        )
        link_snapshot: list[tuple[str, str, str]] = []
        for link in open_links:
            if not link.job_id:
                continue
            batch = session.get(BatchRun, link.batch_id)
            kind = (
                batch.kind
                if batch and batch.kind in ("train", "predict", "download")
                else "predict"
            )
            link_snapshot.append((link.symbol, link.job_id, kind))

    # Deduplicate by job_id — large watchlists otherwise fan out hundreds of HTTP calls.
    by_job: dict[str, tuple[str, str, str]] = {}
    for symbol, train_status, train_job, predict_status, predict_job in snapshot:
        if train_status in ("running", "queued") and train_job:
            by_job[train_job] = (symbol, train_job, "train")
        if predict_status in ("running", "queued") and predict_job:
            by_job[predict_job] = (symbol, predict_job, "predict")
    for symbol, job_id, kind in link_snapshot:
        by_job.setdefault(job_id, (symbol, job_id, kind))
    pending = list(by_job.values())

    if pending:
        sem = asyncio.Semaphore(8)

        async def _limited(job_id: str, client: httpx.AsyncClient):
            async with sem:
                return await _fetch_job(job_id, client=client)

        async with httpx.AsyncClient(timeout=5.0) as client:
            results = await asyncio.gather(*[_limited(job_id, client) for _, job_id, _ in pending])
        for (symbol, job_id, kind), data in zip(pending, results):
            if not data:
                continue
            sync_job_status(symbol, job_id, kind, data.get("status", ""), data.get("error", ""))

    # Keep durable train-all processors alive across task death / process restart.
    try:
        await resume_open_train_batches()
    except Exception:
        pass


async def enrich_items_with_job_progress(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach live Redis/pipeline progress for queued/running train & predict jobs."""
    need: list[tuple[int, str, str]] = []  # (index, kind, job_id)
    seen_jobs: set[str] = set()
    for idx, item in enumerate(items):
        if item.get("train_status") in ("running", "queued") and item.get("last_train_job_id"):
            jid = item["last_train_job_id"]
            if jid not in seen_jobs:
                seen_jobs.add(jid)
                need.append((idx, "train", jid))
        if item.get("predict_status") in ("running", "queued") and item.get("last_predict_job_id"):
            jid = item["last_predict_job_id"]
            if jid not in seen_jobs:
                seen_jobs.add(jid)
                need.append((idx, "predict", jid))

    if not need:
        return items

    out = [dict(item) for item in items]
    sem = asyncio.Semaphore(8)

    async def _limited(job_id: str, client: httpx.AsyncClient):
        async with sem:
            return await _fetch_job(job_id, client=client)

    async with httpx.AsyncClient(timeout=5.0) as client:
        results = await asyncio.gather(*[_limited(job_id, client) for _, _, job_id in need])
    job_data = {job_id: data for (_, _, job_id), data in zip(need, results)}

    # Re-attach by scanning items so duplicate job_ids still get progress payloads.
    for idx, item in enumerate(out):
        for kind, key in (("train", "last_train_job_id"), ("predict", "last_predict_job_id")):
            if item.get(f"{kind}_status") not in ("running", "queued"):
                continue
            jid = item.get(key)
            data = job_data.get(jid) if jid else None
            if not data:
                continue
            payload = _job_progress_payload(kind, data)
            if kind == "train":
                out[idx]["train_job"] = payload
                if out[idx].get("train_status") in ("running", "queued"):
                    out[idx]["job_progress"] = payload
            else:
                out[idx]["predict_job"] = payload
                if (
                    out[idx].get("predict_status") in ("running", "queued")
                    and out[idx].get("train_status") not in ("running", "queued")
                ):
                    out[idx]["job_progress"] = payload
    return out