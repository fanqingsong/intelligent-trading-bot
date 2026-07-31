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

from shared import (
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


# ----- Analyze (A-share one-click) -----

ASHARE_TEMPLATE = "config-ashare-1d.jsonc"


class AnalyzeRequest(BaseModel):
    symbol: str


@app.get("/api/analyze/suggest")
def analyze_suggest(
    q: str = Query("", description="Stock code or name fragment"),
    limit: int = Query(15, ge=1, le=50),
):
    """Typeahead suggestions for A-share code / name."""
    from shared.collectors.collector_ashare import search_ashare_stocks

    query = (q or "").strip()
    if len(query) < 1:
        return {"query": query, "items": []}
    try:
        items = search_ashare_stocks(query, limit=limit)
    except Exception as e:
        raise HTTPException(503, f"Stock list unavailable: {e}") from e
    return {"query": query, "items": items}


def _apply_ashare_template(symbol: str) -> str:
    """Load ashare template, inject symbol, preserve current data_folder."""
    import re

    from shared.collectors.collector_ashare import normalize_ashare_symbol

    code = normalize_ashare_symbol(symbol)
    src = PACKAGE_ROOT / "configs" / ASHARE_TEMPLATE
    if not src.exists():
        raise HTTPException(500, f"Template not found: {ASHARE_TEMPLATE}")
    text = src.read_text(encoding="utf-8")

    try:
        current = load_config_dict()
        data_folder = current.get("data_folder", "/app/data")
    except Exception:
        data_folder = "/app/data"

    text = re.sub(
        r'"data_folder"\s*:\s*"[^"]*"',
        f'"data_folder": "{data_folder}"',
        text,
    )
    text = re.sub(r'"symbol"\s*:\s*"[^"]*"', f'"symbol": "{code}"', text, count=1)
    text = re.sub(
        r'"description"\s*:\s*"[^"]*"',
        f'"description": "A-share {code} daily analysis"',
        text,
        count=1,
    )
    # First data_sources folder
    text = re.sub(
        r'("data_sources"\s*:\s*\[\s*\{\s*"folder"\s*:\s*")[^"]*(")',
        rf"\g<1>{code}\2",
        text,
        count=1,
    )
    return text


@app.post("/api/analyze")
async def analyze_symbol(req: AnalyzeRequest):
    from shared.collectors.collector_ashare import resolve_ashare_query

    try:
        code = resolve_ashare_query(req.symbol)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    try:
        text = _apply_ashare_template(code)
        write_config_text(text)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to apply template: {e}") from e

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                f"{PIPELINE_URL}/internal/jobs",
                json={"steps": list(PIPELINE_STEPS), "config_path": str(get_config_path())},
            )
        except httpx.RequestError as e:
            raise HTTPException(503, f"Pipeline service unavailable: {e}") from e
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text)

    payload = resp.json()
    return {
        "job_id": payload.get("job_id"),
        "status": payload.get("status"),
        "symbol": code,
        "steps": payload.get("steps", list(PIPELINE_STEPS)),
    }


@app.get("/api/analyze/result")
def analyze_result(symbol: str | None = Query(None)):
    """Summarize latest signals for the active (or requested) symbol."""
    config = load_config_dict()
    data_folder = get_data_folder(config)
    sym = symbol or config.get("symbol", "")
    if symbol:
        try:
            from shared.collectors.collector_ashare import resolve_ashare_query

            sym = resolve_ashare_query(symbol)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    signal_name = config.get("signal_file_name", "signals.csv")
    path = data_folder / sym / signal_name
    summary: dict[str, Any] = {
        "symbol": sym,
        "path": str(path),
        "available": False,
        "latest": None,
        "recommendation": "HOLD",
    }
    if not path.exists():
        return summary

    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    if df.empty:
        return summary

    row = df.iloc[-1]
    latest = json.loads(pd.DataFrame([row]).to_json(orient="records", date_format="iso"))[0]
    buy = bool(row.get("buy_signal_column", False)) if "buy_signal_column" in df.columns else False
    sell = bool(row.get("sell_signal_column", False)) if "sell_signal_column" in df.columns else False
    score = row.get("trade_score")
    if buy and not sell:
        recommendation = "BUY"
    elif sell and not buy:
        recommendation = "SELL"
    else:
        recommendation = "HOLD"

    summary.update({
        "available": True,
        "latest": latest,
        "recommendation": recommendation,
        "trade_score": None if score is None or (isinstance(score, float) and pd.isna(score)) else float(score),
        "close": float(row["close"]) if "close" in df.columns and pd.notna(row.get("close")) else None,
        "total_rows": len(df),
    })
    return summary


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
    symbol = config.get("symbol", "600519")
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


@app.get("/api/backtest/results")
def backtest_results(tail: int = Query(80, ge=1, le=500)):
    """Rolling-predict metrics and simulate grid-search results for the UI Backtest page."""
    config = load_config_dict()
    data_folder = get_data_folder(config)
    symbol = config.get("symbol", "600519")
    base = data_folder / symbol

    predict_name = config.get("predict_file_name", "predictions.csv")
    metrics_path = (base / predict_name).with_suffix(".txt")
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
            data_lines = lines[1:]
            for ln in data_lines[-tail:]:
                cols = [c.strip() for c in ln.split(",")]
                if len(cols) == len(headers):
                    simulate_rows.append(dict(zip(headers, cols)))

    return {
        "symbol": symbol,
        "metrics_path": str(metrics_path),
        "metrics": metrics,
        "simulate_path": str(simulate_path),
        "simulate_text": simulate_text,
        "simulate_rows": simulate_rows,
    }


@app.get("/api/data/files")
def list_data_files():
    config = load_config_dict()
    data_folder = get_data_folder(config)
    symbol = config.get("symbol", "600519")
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
    symbol = config.get("symbol", "600519")
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
    symbol = config.get("symbol", "600519")
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
        try:
            resp = await client.get(f"{PIPELINE_URL}/health")
            services["pipeline"] = "ok" if resp.status_code == 200 else f"status {resp.status_code}"
        except Exception as e:
            services["pipeline"] = f"error: {e}"
    return {"status": "ok", "services": services}


@app.get("/api/dashboard")
async def dashboard():
    health_data = await health()
    jobs = list_recent_jobs()
    config = load_config_dict()
    return {
        "health": health_data,
        "recent_jobs": jobs["jobs"][:5],
        "symbol": config.get("symbol"),
        "freq": config.get("freq"),
        "description": config.get("description"),
    }
