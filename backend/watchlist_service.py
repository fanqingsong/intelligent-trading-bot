"""Watchlist orchestration: CRUD, train/predict jobs, signal summaries."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd
from sqlalchemy import select

from shared import (
    DAILY_PREDICT_STEPS,
    PACKAGE_ROOT,
    TRAIN_UPDATE_STEPS,
    get_config_path,
    load_config_dict,
    symbol_config_overrides,
)
from shared.db.engine import get_session_factory
from shared.db.frames import load_frame
from backend.db.models import BatchRun, SymbolRunLink, WatchlistItem

PIPELINE_URL = os.environ.get("PIPELINE_URL", "http://localhost:8001")
ASHARE_TEMPLATE = PACKAGE_ROOT / "configs" / "config-ashare-1d.jsonc"
ALGOS = ("svc", "gb", "nn", "lc")


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


async def _enqueue_job(steps: list[str], overrides: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "steps": steps,
        "config_path": template_config_path(),
        "config_overrides": overrides,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{PIPELINE_URL}/internal/jobs", json=payload)
    if resp.status_code >= 400:
        raise RuntimeError(resp.text)
    return resp.json()


async def train_symbol(symbol: str) -> dict[str, Any]:
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        item = session.get(WatchlistItem, symbol)
        if not item:
            raise KeyError(symbol)
        item.train_status = "queued"
        item.predict_status = "idle"
        item.last_error = ""
        session.commit()

    overrides = build_overrides(symbol, train=True)
    try:
        job = await _enqueue_job(list(TRAIN_UPDATE_STEPS), overrides)
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
    return {"symbol": symbol, "job_id": job.get("job_id"), "steps": job.get("steps"), "kind": "train"}


def _is_trained(item: WatchlistItem) -> bool:
    if item.train_status in ("ready", "completed"):
        return True
    # Heuristic: has signals or predictions in DB
    df = load_frame(item.symbol, "signals")
    return not df.empty


async def predict_symbols(symbols: list[str] | None = None, note: str = "manual") -> dict[str, Any]:
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        if symbols:
            items = [session.get(WatchlistItem, s) for s in symbols]
            items = [i for i in items if i is not None]
        else:
            items = list(session.scalars(select(WatchlistItem).order_by(WatchlistItem.created_at.asc())).all())

        batch = BatchRun(kind="predict", status="running", note=note)
        session.add(batch)
        session.flush()

        planned: list[tuple[str, WatchlistItem]] = []
        skipped: list[dict[str, str]] = []
        for item in items:
            if not _is_trained(item):
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
    # Serial enqueue (worker still may run concurrently; UI expects ordered submission)
    for symbol in planned_symbols:
        overrides = build_overrides(symbol, train=False)
        try:
            job = await _enqueue_job(list(DAILY_PREDICT_STEPS), overrides)
            job_id = job.get("job_id", "")
            with SessionLocal() as session:
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
        "steps": list(DAILY_PREDICT_STEPS),
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


def symbol_signals(symbol: str) -> dict[str, Any]:
    df = load_frame(symbol, "signals")
    summary: dict[str, Any] = {
        "symbol": symbol,
        "available": False,
        "latest": None,
        "recommendation": "HOLD",
        "algorithms": {},
        "vote": "HOLD",
    }
    if df.empty:
        return summary

    row = df.iloc[-1]
    latest = json.loads(pd.DataFrame([row]).to_json(orient="records", date_format="iso"))[0]
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

    vote = str(row.get("vote_label") or "HOLD") if "vote_label" in df.columns else "HOLD"
    if vote not in ("BUY", "SELL", "HOLD"):
        buy = bool(row.get("buy_signal_vote", False)) if "buy_signal_vote" in df.columns else False
        sell = bool(row.get("sell_signal_vote", False)) if "sell_signal_vote" in df.columns else False
        if buy and not sell:
            vote = "BUY"
        elif sell and not buy:
            vote = "SELL"
        else:
            vote = "HOLD"

    summary.update({
        "available": True,
        "latest": latest,
        "algorithms": algos,
        "vote": vote,
        "recommendation": vote,
        "close": float(row["close"]) if "close" in df.columns and pd.notna(row.get("close")) else None,
        "total_rows": len(df),
        "timestamp": latest.get("timestamp"),
    })
    return summary


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
            elif status == "running":
                item.train_status = "running"
        elif kind == "predict":
            if status == "completed":
                item.predict_status = "ready"
                item.last_predicted_at = _utcnow()
                item.last_error = ""
            elif status == "failed":
                item.predict_status = "failed"
                item.last_error = error or "predict failed"
            elif status == "running":
                item.predict_status = "running"
        if job_id:
            if kind == "train":
                item.last_train_job_id = job_id
            else:
                item.last_predict_job_id = job_id
        session.commit()


async def refresh_running_statuses() -> None:
    """Poll Redis/pipeline for in-flight watchlist jobs and sync DB status."""
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        items = list(session.scalars(select(WatchlistItem)).all())
        snapshot = [
            (i.symbol, i.train_status, i.last_train_job_id, i.predict_status, i.last_predict_job_id)
            for i in items
        ]

    async with httpx.AsyncClient(timeout=10.0) as client:
        for symbol, train_status, train_job, predict_status, predict_job in snapshot:
            if train_status in ("running", "queued") and train_job:
                try:
                    resp = await client.get(f"{PIPELINE_URL}/internal/jobs/{train_job}")
                    if resp.status_code == 200:
                        data = resp.json()
                        sync_job_status(symbol, train_job, "train", data.get("status", ""), data.get("error", ""))
                except Exception:
                    pass
            if predict_status in ("running", "queued") and predict_job:
                try:
                    resp = await client.get(f"{PIPELINE_URL}/internal/jobs/{predict_job}")
                    if resp.status_code == 200:
                        data = resp.json()
                        sync_job_status(symbol, predict_job, "predict", data.get("status", ""), data.get("error", ""))
                except Exception:
                    pass
