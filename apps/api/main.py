"""API control plane (BFF) for the Intelligent Trading Bot."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import redis
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from apps import (
    ALL_STEPS,
    BACKTEST_STEPS,
    PACKAGE_ROOT,
    PIPELINE_STEPS,
    get_config_path,
    get_data_folder,
    get_redis_url,
    load_config_dict,
    parse_config_text,
    read_config_text,
    write_config_text,
)

app = FastAPI(title="ITB API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PIPELINE_URL = os.environ.get("PIPELINE_URL", "http://localhost:8001")
TRADER_URL = os.environ.get("TRADER_URL", "http://localhost:8002")

_redis: redis.Redis | None = None


def rds() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(get_redis_url(), decode_responses=True)
    return _redis


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
    # Keep docker data folder if present in current config
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


# ----- Pipeline -----

class PipelineJobRequest(BaseModel):
    steps: list[str] = Field(default_factory=lambda: list(PIPELINE_STEPS))


@app.get("/api/pipeline/steps")
def pipeline_steps():
    return {"pipeline": PIPELINE_STEPS, "backtest": BACKTEST_STEPS, "all": ALL_STEPS}


@app.post("/api/pipeline/jobs")
async def create_pipeline_job(req: PipelineJobRequest):
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                f"{PIPELINE_URL}/internal/jobs",
                json={"steps": req.steps, "config_path": str(get_config_path())},
            )
        except httpx.RequestError as e:
            raise HTTPException(503, f"Pipeline service unavailable: {e}") from e
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text)
    return resp.json()


@app.get("/api/pipeline/jobs")
def list_recent_jobs():
    ids = rds().lrange("itb:jobs:recent", 0, 19)
    jobs = []
    for jid in ids:
        data = rds().hgetall(f"itb:job:{jid}")
        if data:
            data["job_id"] = jid
            if "steps" in data:
                try:
                    data["steps"] = json.loads(data["steps"])
                except Exception:
                    pass
            jobs.append(data)
    return {"jobs": jobs}


@app.get("/api/pipeline/jobs/{job_id}")
async def get_pipeline_job(job_id: str):
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(f"{PIPELINE_URL}/internal/jobs/{job_id}")
        except httpx.RequestError as e:
            raise HTTPException(503, f"Pipeline service unavailable: {e}") from e
    if resp.status_code == 404:
        raise HTTPException(404, "Job not found")
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text)
    return resp.json()


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


# ----- Models & Data -----

@app.get("/api/models")
def list_models():
    config = load_config_dict()
    data_folder = get_data_folder(config)
    symbol = config.get("symbol", "BTCUSDT")
    model_folder = config.get("model_folder", "MODELS")
    model_path = data_folder / symbol / model_folder
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
    predict_name = config.get("predict_file_name", "predictions.csv")
    metrics_path = (data_folder / symbol / predict_name).with_suffix(".txt")
    if metrics_path.exists():
        metrics = metrics_path.read_text(encoding="utf-8")[-8000:]
    return {"model_path": str(model_path), "files": files, "metrics": metrics}


@app.get("/api/data/files")
def list_data_files():
    config = load_config_dict()
    data_folder = get_data_folder(config)
    symbol = config.get("symbol", "BTCUSDT")
    base = data_folder / symbol
    files = []
    if base.exists():
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix in (".csv", ".parquet", ".txt"):
                files.append({
                    "name": str(p.relative_to(base)),
                    "size": p.stat().st_size,
                    "mtime": p.stat().st_mtime,
                })
    return {"base": str(base), "files": files}


@app.get("/api/data/preview")
def preview_data(
    file: str = Query(..., description="Relative file under symbol folder"),
    rows: int = Query(20, ge=1, le=200),
):
    config = load_config_dict()
    data_folder = get_data_folder(config)
    symbol = config.get("symbol", "BTCUSDT")
    path = (data_folder / symbol / file).resolve()
    base = (data_folder / symbol).resolve()
    if not str(path).startswith(str(base)) or not path.exists():
        raise HTTPException(404, "File not found")
    if path.suffix == ".csv":
        df = pd.read_csv(path)
    elif path.suffix == ".parquet":
        df = pd.read_parquet(path)
    elif path.suffix == ".txt":
        return {"columns": ["text"], "rows": [{"text": line} for line in path.read_text(encoding="utf-8").splitlines()[-rows:]]}
    else:
        raise HTTPException(400, "Unsupported file type")
    tail = df.tail(rows)
    # Convert timestamps etc. to JSON-safe
    records = json.loads(tail.to_json(orient="records", date_format="iso"))
    return {"columns": list(df.columns), "rows": records, "total_rows": len(df)}


@app.get("/api/signals/recent")
def recent_signals(rows: int = Query(50, ge=1, le=500)):
    config = load_config_dict()
    data_folder = get_data_folder(config)
    symbol = config.get("symbol", "BTCUSDT")
    signal_name = config.get("signal_file_name", "signals.csv")
    path = data_folder / symbol / signal_name
    if not path.exists():
        return {"columns": [], "rows": [], "total_rows": 0, "path": str(path)}
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    tail = df.tail(rows)
    records = json.loads(tail.to_json(orient="records", date_format="iso"))
    return {"columns": list(df.columns), "rows": records, "total_rows": len(df), "path": str(path)}


# ----- Trader proxy -----

@app.get("/api/trader/status")
async def trader_status():
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(f"{TRADER_URL}/status")
        except httpx.RequestError as e:
            return {"available": False, "error": str(e)}
    if resp.status_code >= 400:
        return {"available": False, "error": resp.text}
    data = resp.json()
    data["available"] = True
    return data


@app.post("/api/trader/{action}")
async def trader_control(action: str):
    if action not in ("start", "stop", "pause", "resume", "reload-config"):
        raise HTTPException(400, f"Unknown action: {action}")
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(f"{TRADER_URL}/control/{action}")
        except httpx.RequestError as e:
            raise HTTPException(503, f"Trader unavailable: {e}") from e
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text)
    return resp.json()


# ----- Health / dashboard -----

@app.get("/health")
async def health():
    services: dict[str, Any] = {"api": "ok"}
    try:
        rds().ping()
        services["redis"] = "ok"
    except Exception as e:
        services["redis"] = f"error: {e}"

    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, url in (("pipeline", PIPELINE_URL), ("trader", TRADER_URL)):
            try:
                resp = await client.get(f"{url}/health")
                services[name] = "ok" if resp.status_code == 200 else f"status {resp.status_code}"
            except Exception as e:
                services[name] = f"error: {e}"
    return {"status": "ok", "services": services}


@app.get("/api/dashboard")
async def dashboard():
    health_data = await health()
    jobs = list_recent_jobs()
    trader = await trader_status()
    config = load_config_dict()
    return {
        "health": health_data,
        "recent_jobs": jobs["jobs"][:5],
        "trader": trader,
        "symbol": config.get("symbol"),
        "freq": config.get("freq"),
        "description": config.get("description"),
    }
