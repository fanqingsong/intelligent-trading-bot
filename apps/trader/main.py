"""Trader microservice wrapping the online analyzer/scheduler with control HTTP API."""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from apps import get_config_path, get_redis_url

log = logging.getLogger("trader")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="ITB Trader Service", version="0.1.0")

# Runtime state
_state = {
    "running": False,
    "paused": False,
    "started_at": None,
    "last_tick_at": None,
    "last_error": None,
    "init_ok": False,
}
_engine_lock = threading.Lock()
_bg_loop: asyncio.AbstractEventLoop | None = None
_bg_thread: threading.Thread | None = None


class ControlResponse(BaseModel):
    ok: bool
    message: str
    state: dict[str, Any]


def _get_status_payload() -> dict[str, Any]:
    try:
        from service.App import App
    except Exception as e:
        return {
            "running": _state["running"],
            "paused": _state["paused"],
            "started_at": _state["started_at"],
            "last_tick_at": _state["last_tick_at"],
            "last_error": _state["last_error"] or str(e),
            "init_ok": _state["init_ok"],
            "latest": {},
        }

    latest = {}
    try:
        df = getattr(App, "df", None) or (App.analyzer.df if App.analyzer is not None else None)
        if df is not None and len(df) > 0:
            row = df.iloc[-1]
            for col in ("close", "timestamp", "close_time"):
                if col in df.columns:
                    val = row[col]
                    latest[col] = str(val)
            for col in df.columns:
                name = str(col).lower()
                if "score" in name or "signal" in name or "buy" in name or "sell" in name:
                    try:
                        latest[col] = float(row[col]) if pd_is_number(row[col]) else str(row[col])
                    except Exception:
                        latest[col] = str(row[col])
    except Exception as e:
        latest["error"] = str(e)

    return {
        "running": _state["running"],
        "paused": _state["paused"],
        "started_at": _state["started_at"],
        "last_tick_at": _state["last_tick_at"],
        "last_error": _state["last_error"],
        "init_ok": _state["init_ok"],
        "error_status": getattr(App, "error_status", None),
        "server_status": getattr(App, "server_status", None),
        "account_status": getattr(App, "account_status", None),
        "symbol": App.config.get("symbol") if hasattr(App, "config") else None,
        "freq": App.config.get("freq") if hasattr(App, "config") else None,
        "latest": latest,
    }


def pd_is_number(v) -> bool:
    try:
        float(v)
        return True
    except Exception:
        return False


async def _wrapped_main_task():
    """Run one scheduler tick, respecting pause flag."""
    if _state["paused"]:
        log.info("Trader paused — skipping tick")
        return
    try:
        from service.server import main_task
        await main_task()
        _state["last_tick_at"] = datetime.now(timezone.utc).isoformat()
        _state["last_error"] = None
    except Exception as e:
        log.exception("main_task failed")
        _state["last_error"] = str(e)


def _init_trader(config_file: str) -> None:
    """Initialize App, models, analyzer, venue client (cold start). Runs on bg loop."""
    import asyncio as aio

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from common.analyzer import Analyzer
    from common.model_store import ModelStore
    from common.types import Venue
    from common.utils import freq_to_CronTrigger
    from inputs import get_collector_functions
    from outputs import get_trader_functions
    from outputs.notifier_trades import load_last_transaction
    from service.App import App, data_provider_problems_exist, load_config
    from service.server import main_collector_task

    load_config(config_file)
    App.config["train"] = False

    symbol = App.config["symbol"]
    freq = App.config["freq"]
    venue = App.config.get("venue")
    try:
        if venue is not None:
            venue = Venue(venue)
    except ValueError as e:
        raise RuntimeError(f"Invalid venue: {venue}: {e}") from e

    trader_funcs = get_trader_functions(venue)
    log.info("Initializing trader. Venue=%s symbol=%s freq=%s", venue.value if venue else None, symbol, freq)

    if venue == Venue.BINANCE:
        client_params = {}
        if App.config.get("append_overlap_records"):
            client_params["append_overlap_records"] = App.config["append_overlap_records"]
        client_args = dict(
            api_key=App.config.get("api_key"),
            api_secret=App.config.get("api_secret"),
        )
        client_args = client_args | App.config.get("client_args", {})
        from inputs.collector_binance import init_client
        try:
            init_client(client_params, client_args)
        except Exception as e:
            log.error("Binance client init failed (continuing): %s", e)
            _state["last_error"] = f"binance init: {e}"

    if venue == Venue.MT5:
        client_params = {}
        client_args = dict(
            mt5_account_id=int(App.config.get("mt5_account_id")),
            mt5_password=str(App.config.get("mt5_password")),
            mt5_server=str(App.config.get("mt5_server")),
        )
        client_args = client_args | App.config.get("client_args", {})
        from inputs.collector_mt5 import init_client
        try:
            init_client(client_params, client_args)
        except Exception as e:
            log.error("MT5 client init failed (continuing): %s", e)
            _state["last_error"] = f"mt5 init: {e}"

    try:
        App.model_store = ModelStore(App.config)
        App.model_store.load_models()
    except Exception as e:
        log.error("Model load failed (continuing): %s", e)
        _state["last_error"] = f"models: {e}"
        App.model_store = ModelStore(App.config)

    App.analyzer = Analyzer(App.config, App.model_store)
    try:
        App.transaction = load_last_transaction()
    except Exception:
        App.transaction = None

    App.loop = aio.get_event_loop()

    # Cold start — may fail without valid API keys; keep service up
    try:
        App.loop.run_until_complete(main_collector_task())
        App.loop.run_until_complete(main_collector_task())
        App.analyzer.analyze()
        _state["init_ok"] = not data_provider_problems_exist()
    except Exception as e:
        log.error("Cold start failed (service stays up): %s", e)
        _state["last_error"] = str(e)
        _state["init_ok"] = False

    if App.config.get("trade_model", {}).get("trader_binance"):
        try:
            App.loop.run_until_complete(trader_funcs["update_trade_status"]())
        except Exception as e:
            log.error("Trade status sync failed: %s", e)

    App.sched = AsyncIOScheduler()
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    trigger = freq_to_CronTrigger(freq)
    App.sched.add_job(_wrapped_main_task, trigger=trigger, id="main_task")
    App.sched._eventloop = App.loop
    App.sched.start()
    _state["running"] = True
    _state["started_at"] = datetime.now(timezone.utc).isoformat()
    log.info("Trader scheduler started.")


def _run_bg_loop(config_file: str):
    global _bg_loop
    _bg_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_bg_loop)
    try:
        _init_trader(config_file)
        _bg_loop.run_forever()
    except Exception as e:
        log.exception("Trader background loop crashed")
        _state["last_error"] = str(e)
        _state["running"] = False
    finally:
        try:
            from service.App import App
            if App.sched and App.sched.running:
                App.sched.shutdown(wait=False)
        except Exception:
            pass


def start_engine(config_file: str | None = None) -> str:
    global _bg_thread
    with _engine_lock:
        if _state["running"]:
            return "already running"
        config_file = config_file or str(get_config_path())
        _bg_thread = threading.Thread(target=_run_bg_loop, args=(config_file,), daemon=True, name="trader-engine")
        _bg_thread.start()
        # Give it a moment to set running flag
        import time
        for _ in range(50):
            if _state["running"] or _state["last_error"]:
                break
            time.sleep(0.1)
        return "started"


def stop_engine() -> str:
    global _bg_loop, _bg_thread
    with _engine_lock:
        if not _state["running"] and _bg_loop is None:
            return "already stopped"
        try:
            from service.App import App
            from common.types import Venue

            if App.sched and getattr(App.sched, "running", False):
                App.sched.shutdown(wait=False)
            venue = App.config.get("venue")
            try:
                venue = Venue(venue) if venue else None
            except Exception:
                venue = None
            if venue == Venue.BINANCE:
                try:
                    from inputs.collector_binance import close_client
                    close_client()
                except Exception:
                    pass
            if venue == Venue.MT5:
                try:
                    from inputs.collector_mt5 import close_client
                    close_client()
                except Exception:
                    pass
        except Exception as e:
            log.error("Error during stop: %s", e)

        if _bg_loop and _bg_loop.is_running():
            _bg_loop.call_soon_threadsafe(_bg_loop.stop)
        _state["running"] = False
        _state["paused"] = False
        _bg_loop = None
        _bg_thread = None
        return "stopped"


@app.on_event("startup")
async def on_startup():
    autostart = os.environ.get("TRADER_AUTOSTART", "false").lower() in ("1", "true", "yes")
    if autostart:
        log.info("TRADER_AUTOSTART enabled — starting engine")
        start_engine()


@app.on_event("shutdown")
async def on_shutdown():
    stop_engine()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "running": _state["running"],
        "paused": _state["paused"],
        "init_ok": _state["init_ok"],
    }


@app.get("/status")
def status():
    return _get_status_payload()


@app.post("/control/{action}", response_model=ControlResponse)
def control(action: str):
    action = action.lower()
    if action == "start":
        msg = start_engine()
    elif action == "stop":
        msg = stop_engine()
    elif action == "pause":
        _state["paused"] = True
        msg = "paused"
    elif action == "resume":
        _state["paused"] = False
        msg = "resumed"
    elif action in ("reload-config", "reload_config"):
        was_running = _state["running"]
        if was_running:
            stop_engine()
        msg = start_engine() if was_running else "config will apply on next start"
        if not was_running:
            # Touch config load for validation
            from service.App import load_config
            load_config(str(get_config_path()))
            msg = "config reloaded (engine not running)"
    else:
        raise HTTPException(400, f"Unknown action: {action}")
    return ControlResponse(ok=True, message=msg, state=_get_status_payload())
