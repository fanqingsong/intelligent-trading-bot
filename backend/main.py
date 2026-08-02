"""API control plane (BFF) for the Intelligent Trading Bot."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated, Any

import httpx
import redis
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from backend.team_rbac import Caller, caller_dep, require_team

from shared import (
    ALL_STEPS,
    BACKTEST_STEPS,
    DAILY_PREDICT_STEPS,
    PACKAGE_ROOT,
    PIPELINE_STEPS,
    TRAIN_UPDATE_STEPS,
    get_config_path,
    get_redis_url,
    load_config_dict,
    parse_config_text,
    read_config_text,
    write_config_text,
)
from backend.db import ensure_control_plane_db
from shared.db.frames import FRAME_KINDS, list_kinds_for_symbol, load_frame

app = FastAPI(title="ITB API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-ITB-User", "X-ITB-Teams", "X-ITB-Admin"],
)

PIPELINE_URL = os.environ.get("PIPELINE_URL", "http://localhost:8001")
STATUS_SYNC_INTERVAL_S = float(os.environ.get("ITB_STATUS_SYNC_INTERVAL_S", "5"))

_redis: redis.Redis | None = None
_status_sync_task: asyncio.Task | None = None


def rds() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(get_redis_url(), decode_responses=True)
    return _redis


async def _status_sync_loop() -> None:
    from backend.watchlist_service import refresh_running_statuses

    while True:
        try:
            await refresh_running_statuses()
        except Exception as e:
            print(f"WARN: status sync failed: {e}")
        await asyncio.sleep(STATUS_SYNC_INTERVAL_S)


@app.on_event("startup")
async def on_startup() -> None:
    global _status_sync_task
    try:
        ensure_control_plane_db()
    except Exception as e:
        print(f"WARN: init_db failed: {e}")
    try:
        from backend.scheduler import start_scheduler

        start_scheduler()
    except Exception as e:
        print(f"WARN: scheduler start failed: {e}")
    _status_sync_task = asyncio.create_task(_status_sync_loop())
    # Resume unfinished train-all batches after crash / restart.
    try:
        from backend.watchlist_service import resume_open_train_batches

        asyncio.create_task(resume_open_train_batches())
    except Exception as e:
        print(f"WARN: train-all resume schedule failed: {e}")
    # Warm A-share code/name cache so Watchlist suggest is not cold on first keystroke.
    try:
        from shared.collectors.collector_ashare import warmup_ashare_stock_list

        asyncio.create_task(asyncio.to_thread(warmup_ashare_stock_list))
    except Exception as e:
        print(f"WARN: ashare stock list warmup schedule failed: {e}")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global _status_sync_task
    if _status_sync_task is not None:
        _status_sync_task.cancel()
        _status_sync_task = None
    try:
        from backend.scheduler import stop_scheduler

        stop_scheduler()
    except Exception:
        pass


# ----- Config -----

class ConfigUpdate(BaseModel):
    content: str


@app.get("/api/config")
def get_config():
    path = get_config_path()
    if not path.exists():
        raise HTTPException(404, f"Config not found: {path}")
    text = read_config_text(path)
    return {
        "path": str(path.relative_to(PACKAGE_ROOT)) if path.is_relative_to(PACKAGE_ROOT) else str(path),
        "content": text,
        "parsed": parse_config_text(text),
    }


@app.put("/api/config")
def put_config(body: ConfigUpdate):
    try:
        parse_config_text(body.content)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Invalid JSONC: {e}") from e
    write_config_text(body.content)
    return {"ok": True, "path": str(get_config_path())}


@app.get("/api/config/samples")
def list_samples():
    configs_dir = PACKAGE_ROOT / "configs"
    files = sorted(p.name for p in configs_dir.glob("*.jsonc"))
    return {"samples": files}


@app.post("/api/config/load-sample/{name}")
def load_sample(name: str):
    src = PACKAGE_ROOT / "configs" / name
    if not src.exists():
        raise HTTPException(404, f"Sample not found: {name}")
    text = src.read_text(encoding="utf-8")
    try:
        current = load_config_dict()
        data_folder = current.get("data_folder", "/app/data")
    except Exception:
        data_folder = "/app/data"
    if '"data_folder"' in text:
        import re

        text = re.sub(
            r'"data_folder"\s*:\s*"[^"]*"',
            f'"data_folder": "{data_folder}"',
            text,
        )
    write_config_text(text)
    return {"ok": True, "loaded": name}


# ----- Watchlist -----

class WatchlistAddRequest(BaseModel):
    symbol: str


class WatchlistImportRequest(BaseModel):
    index: str  # sse50 | csi300 | 000016 | 000300


class PredictRequest(BaseModel):
    symbols: list[str] | None = None
    team: str | None = None


class TrainAllRequest(BaseModel):
    symbols: list[str] | None = None
    team: str | None = None


class ScheduleUpdate(BaseModel):
    predict_enabled: bool | None = None
    predict_cron: str | None = None
    timezone: str | None = None


@app.get("/api/watchlist/suggest")
def watchlist_suggest(
    q: str = Query("", description="Stock code or name fragment"),
    limit: int = Query(15, ge=1, le=50),
):
    from shared.collectors.collector_ashare import search_ashare_stocks

    query = (q or "").strip()
    if len(query) < 1:
        return {"query": query, "items": []}
    try:
        items = search_ashare_stocks(query, limit=limit)
    except Exception as e:
        raise HTTPException(503, f"Stock list unavailable: {e}") from e
    return {"query": query, "items": items}


@app.get("/api/watchlist")
async def watchlist_list():
    from backend.watchlist_service import (
        enrich_items_with_job_progress,
        list_items,
        refresh_running_statuses,
        symbol_signals,
    )

    await refresh_running_statuses()
    items = await enrich_items_with_job_progress(list_items())
    # Attach latest signal summary (lightweight)
    enriched = []
    for item in items:
        sig = symbol_signals(item["symbol"])
        enriched.append({
            **item,
            "vote": sig.get("vote"),
            "algorithms": sig.get("algorithms") or {},
            "signal_available": sig.get("available", False),
            "close": sig.get("close"),
            "signal_timestamp": sig.get("timestamp"),
        })
    return {"items": enriched}


@app.post("/api/watchlist")
def watchlist_add(req: WatchlistAddRequest):
    from backend.watchlist_service import add_item

    try:
        item = add_item(req.symbol)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, str(e)) from e
    return item


@app.post("/api/watchlist/import")
def watchlist_import(req: WatchlistImportRequest):
    from backend.watchlist_service import import_index

    try:
        return import_index(req.index)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(503, f"Index import failed: {e}") from e


@app.delete("/api/watchlist/{symbol}")
def watchlist_delete(symbol: str):
    from backend.watchlist_service import delete_item

    if not delete_item(symbol):
        raise HTTPException(404, "Symbol not in watchlist")
    return {"ok": True, "symbol": symbol}


@app.post("/api/watchlist/predict")
async def watchlist_predict(
    caller: Annotated[Caller, Depends(caller_dep)],
    req: PredictRequest | None = None,
):
    from backend.watchlist_service import predict_symbols

    body = req or PredictRequest()
    require_team(caller, body.team)
    try:
        return await predict_symbols(symbols=body.symbols, note="manual", team=body.team)
    except Exception as e:
        raise HTTPException(503, str(e)) from e


@app.post("/api/watchlist/train")
async def watchlist_train_all(
    caller: Annotated[Caller, Depends(caller_dep)],
    req: TrainAllRequest | None = None,
):
    """Train all watchlist symbols sequentially (checkpointed; resumes after restart)."""
    from backend.watchlist_service import train_symbols

    body = req or TrainAllRequest()
    require_team(caller, body.team)
    try:
        return await train_symbols(symbols=body.symbols, note="manual", team=body.team)
    except Exception as e:
        raise HTTPException(503, str(e)) from e


@app.get("/api/watchlist/train/active")
def watchlist_train_active():
    from backend.watchlist_service import active_train_batch

    batch = active_train_batch()
    return {"batch": batch}


@app.post("/api/watchlist/{symbol}/train")
async def watchlist_train(
    symbol: str,
    caller: Annotated[Caller, Depends(caller_dep)],
    team: str | None = Query(None, description="Prefect team deployment / tag"),
):
    from backend.watchlist_service import train_symbol

    require_team(caller, team)
    try:
        return await train_symbol(symbol, team=team)
    except KeyError:
        raise HTTPException(404, "Symbol not in watchlist") from None
    except Exception as e:
        raise HTTPException(503, str(e)) from e


@app.get("/api/watchlist/{symbol}/signals")
def watchlist_symbol_signals(symbol: str):
    from backend.watchlist_service import symbol_signals

    return symbol_signals(symbol)


@app.get("/api/schedule")
def get_schedule_api():
    from backend.scheduler import get_schedule

    return get_schedule()


@app.put("/api/schedule")
def put_schedule_api(body: ScheduleUpdate):
    from backend.scheduler import update_schedule

    try:
        return update_schedule(
            predict_enabled=body.predict_enabled,
            predict_cron=body.predict_cron,
            timezone=body.timezone,
        )
    except Exception as e:
        raise HTTPException(400, str(e)) from e


# ----- Pipeline -----

class PipelineJobRequest(BaseModel):
    steps: list[str] = Field(default_factory=lambda: list(PIPELINE_STEPS))
    config_overrides: dict[str, Any] | None = None
    team: str | None = None


@app.get("/api/pipeline/steps")
def pipeline_steps():
    return {
        "pipeline": PIPELINE_STEPS,
        "train_update": TRAIN_UPDATE_STEPS,
        "daily_predict": DAILY_PREDICT_STEPS,
        "backtest": BACKTEST_STEPS,
        "all": ALL_STEPS,
    }


@app.post("/api/pipeline/jobs")
async def create_pipeline_job(
    req: PipelineJobRequest,
    caller: Annotated[Caller, Depends(caller_dep)],
):
    require_team(caller, req.team)
    payload: dict[str, Any] = {
        "steps": req.steps,
        "config_path": str(get_config_path()),
    }
    if req.config_overrides:
        payload["config_overrides"] = req.config_overrides
    if req.team:
        payload["team"] = req.team
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(f"{PIPELINE_URL}/internal/jobs", json=payload)
        except httpx.RequestError as e:
            raise HTTPException(503, f"Pipeline service unavailable: {e}") from e
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text)
    from backend.prefect_links import enrich_job

    return enrich_job(resp.json())


@app.get("/api/pipeline/jobs")
async def list_recent_jobs():
    from backend.prefect_links import (
        enrich_job,
        job_source,
        list_recent_prefect_jobs,
        redis_mirror_enabled,
    )

    source = job_source()
    jobs: list[dict[str, Any]] = []
    if source in ("redis", "hybrid") and redis_mirror_enabled():
        try:
            ids = rds().lrange("itb:jobs:recent", 0, 19)
            for jid in ids:
                data = rds().hgetall(f"itb:job:{jid}")
                if data:
                    data["job_id"] = jid
                    if "steps" in data:
                        try:
                            data["steps"] = json.loads(data["steps"])
                        except Exception:
                            pass
                    data["source"] = "redis"
                    jobs.append(enrich_job(data))
        except Exception:
            jobs = []

    if source == "prefect" or (source == "hybrid" and not jobs):
        jobs = await list_recent_prefect_jobs(20)
    elif source == "hybrid" and jobs:
        pref = await list_recent_prefect_jobs(20)
        by_id = {j.get("job_id"): j for j in pref}
        for j in jobs:
            p = by_id.get(j.get("job_id"))
            if p:
                j.setdefault("prefect_flow_run_id", p.get("prefect_flow_run_id"))
                j.setdefault("prefect_ui_url", p.get("prefect_ui_url"))
            enrich_job(j)
    return {"jobs": jobs, "source": source}


@app.get("/api/pipeline/jobs/{job_id}")
async def get_pipeline_job(job_id: str):
    from backend.prefect_links import enrich_job, fetch_flow_run

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(f"{PIPELINE_URL}/internal/jobs/{job_id}")
        except httpx.RequestError as e:
            raise HTTPException(503, f"Pipeline service unavailable: {e}") from e
    if resp.status_code == 404:
        # Redis expired: try Prefect tag lookup via recent runs
        for job in await list_recent_prefect_jobs_safe():
            if job.get("job_id") == job_id and job.get("prefect_flow_run_id"):
                pref = await fetch_flow_run(job["prefect_flow_run_id"])
                if pref:
                    return enrich_job({**job, **pref})
        raise HTTPException(404, "Job not found")
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text)
    data = enrich_job(resp.json())
    if data.get("prefect_flow_run_id"):
        pref = await fetch_flow_run(data["prefect_flow_run_id"])
        if pref:
            data.setdefault("prefect_ui_url", pref.get("prefect_ui_url"))
            # Prefer Redis progress/status while present; keep Prefect link.
    return data


async def list_recent_prefect_jobs_safe() -> list[dict[str, Any]]:
    from backend.prefect_links import list_recent_prefect_jobs

    try:
        return await list_recent_prefect_jobs(50)
    except Exception:
        return []

@app.get("/api/pipeline/jobs/{job_id}/logs")
async def stream_job_logs(job_id: str):
    async def event_generator():
        offset = 0
        while True:
            async with httpx.AsyncClient(timeout=15.0) as client:
                try:
                    resp = await client.get(
                        f"{PIPELINE_URL}/internal/jobs/{job_id}/logs",
                        params={"offset": offset},
                    )
                    job_resp = await client.get(f"{PIPELINE_URL}/internal/jobs/{job_id}")
                except httpx.RequestError as e:
                    yield {"event": "error", "data": str(e)}
                    break

            if resp.status_code == 404:
                yield {"event": "error", "data": "Job not found"}
                break

            payload = resp.json()
            for line in payload.get("lines", []):
                yield {"event": "log", "data": line}
            offset = payload.get("next_offset", offset)

            job = job_resp.json() if job_resp.status_code == 200 else {}
            status = job.get("status")
            if status in ("completed", "failed"):
                yield {"event": "done", "data": json.dumps(job)}
                break
            await asyncio.sleep(1.0)

    return EventSourceResponse(event_generator())


# ----- Models & Data (Postgres frames) -----

@app.get("/api/models")
def list_models(symbol: str | None = Query(None)):
    config = load_config_dict()
    sym = symbol or config.get("symbol", "600519")
    # MLflow-backed: surface prediction metrics sidecar if still on disk, plus DB row counts
    from pathlib import Path

    from shared import get_data_folder

    data_folder = get_data_folder(config)
    model_folder = config.get("model_folder", "MODELS")
    model_path = data_folder / sym / model_folder
    files = []
    if model_path.exists():
        for p in sorted(model_path.iterdir()):
            if p.is_file():
                files.append({
                    "name": p.name,
                    "size": p.stat().st_size,
                    "mtime": p.stat().st_mtime,
                })
    metrics = ""
    metrics_path = Path(data_folder) / sym / "predictions.txt"
    if metrics_path.exists():
        metrics = metrics_path.read_text(encoding="utf-8")[-8000:]
    kinds = list_kinds_for_symbol(sym)
    return {
        "symbol": sym,
        "model_path": str(model_path),
        "files": files,
        "metrics": metrics,
        "frames": kinds,
        "mlflow_prefix": f"itb_{sym}_",
    }


# --------------------------------------------------------------------------- #
# MLflow platform endpoints (read-only views over Tracking + Registry)
# --------------------------------------------------------------------------- #

@app.get("/api/mlflow/info")
def mlflow_info():
    from backend.mlflow_service import mlflow_info as _info
    return _info()


@app.get("/api/mlflow/models")
def mlflow_models(symbol: str | None = Query(None)):
    from backend.mlflow_service import list_registered_models
    return {"models": list_registered_models(load_config_dict(), symbol)}


@app.get("/api/mlflow/models/{name}/versions")
def mlflow_model_versions(name: str):
    from backend.mlflow_service import list_model_versions
    return {"name": name, "versions": list_model_versions(name, load_config_dict())}


@app.get("/api/mlflow/runs")
def mlflow_runs(symbol: str | None = Query(None), limit: int = Query(50, ge=1, le=200)):
    from backend.mlflow_service import list_runs
    return {"runs": list_runs(load_config_dict(), symbol, limit)}


@app.get("/api/backtest/results")
def backtest_results(tail: int = Query(80, ge=1, le=500), symbol: str | None = Query(None)):
    from pathlib import Path

    from shared import get_data_folder

    config = load_config_dict()
    data_folder = get_data_folder(config)
    sym = symbol or config.get("symbol", "600519")
    base = Path(data_folder) / sym

    metrics_path = base / "predictions.txt"
    metrics = metrics_path.read_text(encoding="utf-8")[-12000:] if metrics_path.exists() else ""

    models_name = config.get("signal_models_file_name", "signal_models")
    simulate_path = (base / models_name).with_suffix(".txt")
    simulate_text = ""
    simulate_rows: list[dict[str, str]] = []
    if simulate_path.exists():
        simulate_text = simulate_path.read_text(encoding="utf-8")[-12000:]
        lines = [ln.strip() for ln in simulate_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            headers = [h.strip() for h in lines[0].split(",")]
            for ln in lines[1:][-tail:]:
                cols = [c.strip() for c in ln.split(",")]
                if len(cols) == len(headers):
                    simulate_rows.append(dict(zip(headers, cols)))

    return {
        "symbol": sym,
        "metrics_path": str(metrics_path),
        "metrics": metrics,
        "simulate_path": str(simulate_path),
        "simulate_text": simulate_text,
        "simulate_rows": simulate_rows,
    }


@app.get("/api/data/files")
def list_data_files(symbol: str | None = Query(None)):
    config = load_config_dict()
    sym = symbol or config.get("symbol", "600519")
    kinds = list_kinds_for_symbol(sym)
    files = [
        {
            "name": k["kind"],
            "kind": k["kind"],
            "size": k["rows"],
            "mtime": k.get("mtime"),
            "source": "postgres",
        }
        for k in kinds
    ]
    # Always list known kinds for discoverability
    present = {f["kind"] for f in files}
    for kind in FRAME_KINDS:
        if kind not in present:
            files.append({"name": kind, "kind": kind, "size": 0, "mtime": None, "source": "postgres"})
    return {"base": f"postgres://market_frames/{sym}", "symbol": sym, "files": files}


@app.get("/api/data/preview")
def preview_data(
    file: str = Query(..., description="Frame kind under symbol (e.g. signals)"),
    rows: int = Query(20, ge=1, le=200),
    symbol: str | None = Query(None),
):
    config = load_config_dict()
    sym = symbol or config.get("symbol", "600519")
    try:
        df = load_frame(sym, file)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if df.empty:
        return {"columns": [], "rows": [], "total_rows": 0, "symbol": sym, "kind": file}
    tail = df.tail(rows)
    records = json.loads(tail.to_json(orient="records", date_format="iso"))
    return {"columns": list(df.columns), "rows": records, "total_rows": len(df), "symbol": sym, "kind": file}


@app.get("/api/signals/recent")
def recent_signals(
    rows: int = Query(50, ge=1, le=500),
    symbol: str | None = Query(None),
):
    config = load_config_dict()
    sym = symbol or config.get("symbol", "600519")
    df = load_frame(sym, "signals")
    if df.empty:
        return {"columns": [], "rows": [], "total_rows": 0, "symbol": sym, "source": "postgres"}
    tail = df.tail(rows)
    records = json.loads(tail.to_json(orient="records", date_format="iso"))
    return {
        "columns": list(df.columns),
        "rows": records,
        "total_rows": len(df),
        "symbol": sym,
        "source": "postgres",
    }


# ----- Health / dashboard -----

@app.get("/api/prefect/info")
def prefect_info_api():
    from backend.prefect_links import prefect_info
    from backend.team_rbac import rbac_enabled

    info = prefect_info()
    info["rbac_enabled"] = rbac_enabled()
    return info


@app.get("/health")
async def health():
    from backend.prefect_links import prefect_api_url, prefect_ui_url

    services: dict[str, Any] = {"api": "ok"}
    try:
        rds().ping()
        services["redis"] = "ok"
    except Exception as e:
        services["redis"] = f"error: {e}"

    try:
        from shared.db.engine import get_engine
        from sqlalchemy import text

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        services["postgres"] = "ok"
    except Exception as e:
        services["postgres"] = f"error: {e}"

    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(f"{PIPELINE_URL}/health")
            services["pipeline"] = "ok" if resp.status_code == 200 else f"status {resp.status_code}"
        except Exception as e:
            services["pipeline"] = f"error: {e}"
        if prefect_api_url():
            try:
                resp = await client.get(f"{prefect_api_url()}/health")
                services["prefect"] = "ok" if resp.status_code == 200 else f"status {resp.status_code}"
            except Exception as e:
                services["prefect"] = f"error: {e}"
    return {
        "status": "ok",
        "services": services,
        "prefect_ui_url": prefect_ui_url(),
    }


@app.get("/api/dashboard")
async def dashboard():
    from backend.watchlist_service import list_items

    health_data = await health()
    jobs = await list_recent_jobs()
    config = load_config_dict()
    items = list_items()
    return {
        "health": health_data,
        "recent_jobs": jobs["jobs"][:5],
        "watchlist_count": len(items),
        "symbol": config.get("symbol"),
        "freq": config.get("freq"),
        "description": config.get("description"),
        "prefect_ui_url": health_data.get("prefect_ui_url"),
    }
