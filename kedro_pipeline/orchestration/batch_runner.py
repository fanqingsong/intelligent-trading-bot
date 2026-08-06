"""Batch watchlist jobs: one Prefect/local job covering many symbols.

``config_overrides`` must include:
* ``batch_mode``: ``data`` | ``predict`` | ``full``
* ``batch_symbols``: list of 6-digit codes

``data`` / ``full`` run a single multi-``data_sources`` download.
``predict`` / ``full`` then run ``INFER_STEPS`` per symbol (continue on failure).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from shared import DATA_UPDATE_STEPS, INFER_STEPS


def _use_fine_execution() -> bool:
    mode = (os.environ.get("ITB_KEDRO_EXECUTION") or "fine").strip().lower()
    return mode != "coarse"


def _download_overrides(symbols: list[str], base: dict[str, Any]) -> dict[str, Any]:
    codes = [str(s).zfill(6) for s in symbols]
    out = dict(base)
    out["symbol"] = "_watchlist"
    out["description"] = f"Watchlist batch download ({len(codes)} symbols)"
    out["data_sources"] = [{"folder": c, "file": "klines", "column_prefix": ""} for c in codes]
    out["train"] = False
    out["mlflow_registry_prefix"] = "itb_watchlist_"
    out["mlflow_experiment_name"] = "itb_watchlist"
    return out


def _progress(done: int, total: int) -> str:
    if total <= 0:
        return "100"
    return str(max(0, min(100, int(100 * done / total))))


def execute_batch_job(
    job_id: str,
    config_path: str,
    config_overrides: dict[str, Any] | None = None,
    *,
    team: str = "default",
    tags: list[str] | None = None,
    fine: bool | None = None,
) -> None:
    from kedro_pipeline.orchestration.kedro_runner import (
        _jobs_lock,
        _running_jobs,
        append_log,
        execute_job,
        job_was_cancelled,
        update_job,
    )
    from kedro_pipeline.orchestration.prefect_bridge import execute_job_fine

    overrides = dict(config_overrides or {})
    batch_mode = str(overrides.get("batch_mode") or "").strip().lower()
    symbols = [str(s).zfill(6) for s in (overrides.get("batch_symbols") or []) if str(s).strip()]
    if batch_mode not in ("data", "predict", "full"):
        raise ValueError(f"Invalid batch_mode: {batch_mode!r}")
    if not symbols:
        raise ValueError("batch_symbols is empty")

    use_fine = _use_fine_execution() if fine is None else fine
    do_download = batch_mode in ("data", "full")
    do_infer = batch_mode in ("predict", "full")
    sync_kind = "download" if batch_mode == "data" else "predict"

    units = (1 if do_download else 0) + (len(symbols) if do_infer else 0)
    done = 0
    failures: list[str] = []

    if job_was_cancelled(job_id):
        append_log(job_id, "Skip start: job already cancelled")
        return

    with _jobs_lock:
        _running_jobs.add(job_id)
    update_job(
        job_id,
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
        current_step="",
        progress="0",
        execution="batch-fine" if use_fine else "batch-coarse",
        team=team,
    )
    append_log(
        job_id,
        f"Batch job started mode={batch_mode} symbols={len(symbols)} fine={use_fine} team={team}",
    )
    if tags:
        append_log(job_id, f"Tags: {tags}")

    def _run_segment(steps: list[str], segment_overrides: dict[str, Any], label: str) -> None:
        append_log(job_id, f"=== Batch segment: {label} steps={steps} ===")
        if use_fine:
            execute_job_fine(
                job_id,
                steps,
                config_path,
                segment_overrides,
                team=team,
                tags=tags,
                finalize=False,
            )
        else:
            execute_job(
                job_id,
                steps,
                config_path,
                segment_overrides,
                finalize=False,
            )

    def _sync(symbol: str, status: str, error: str = "") -> None:
        try:
            from backend.watchlist_service import sync_job_status

            sync_job_status(symbol, job_id, sync_kind, status, error)
        except Exception as e:
            append_log(job_id, f"WARN: sync_job_status {symbol} {status} failed: {e}")

    try:
        if do_download:
            if job_was_cancelled(job_id):
                append_log(job_id, "Aborting before download: cancelled")
                return
            update_job(job_id, current_step="download", progress=_progress(done, units))
            dl_overrides = _download_overrides(symbols, overrides)
            try:
                _run_segment(list(DATA_UPDATE_STEPS), dl_overrides, "download-all")
            except Exception as e:
                if job_was_cancelled(job_id):
                    append_log(job_id, f"Cancelled during download: {e}")
                    return
                err = str(e)
                append_log(job_id, f"ERROR: batch download failed: {err}")
                for sym in symbols:
                    failures.append(sym)
                    _sync(sym, "failed", err)
                update_job(
                    job_id,
                    status="failed",
                    error=f"batch download failed: {err}",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
                return
            done += 1
            update_job(job_id, progress=_progress(done, units))
            if batch_mode == "data":
                for sym in symbols:
                    _sync(sym, "completed")

        if do_infer:
            from backend.watchlist_service import build_overrides

            for sym in symbols:
                if job_was_cancelled(job_id):
                    append_log(job_id, f"Aborting before infer:{sym}: cancelled")
                    return
                update_job(
                    job_id,
                    current_step=f"infer:{sym}",
                    progress=_progress(done, units),
                )
                try:
                    _run_segment(
                        list(INFER_STEPS),
                        build_overrides(sym, train=False),
                        f"infer:{sym}",
                    )
                    _sync(sym, "completed")
                except Exception as e:
                    if job_was_cancelled(job_id):
                        append_log(job_id, f"Cancelled during infer:{sym}: {e}")
                        return
                    err = str(e)
                    append_log(job_id, f"ERROR: infer:{sym} failed: {err}")
                    failures.append(sym)
                    _sync(sym, "failed", err)
                done += 1
                update_job(job_id, progress=_progress(done, units))

        if job_was_cancelled(job_id):
            append_log(job_id, "Job cancelled during batch; not marking completed")
            return

        if failures:
            summary = f"{len(failures)}/{len(symbols)} symbols failed: {', '.join(failures[:20])}"
            update_job(
                job_id,
                status="failed",
                error=summary,
                current_step="",
                progress="100",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            append_log(job_id, f"Batch finished with failures: {summary}")
        else:
            update_job(
                job_id,
                status="completed",
                error="",
                current_step="",
                progress="100",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            append_log(job_id, "Batch job completed successfully.")
    except Exception as e:
        if job_was_cancelled(job_id):
            append_log(job_id, f"Job cancelled (ignoring error): {e}")
            return
        update_job(
            job_id,
            status="failed",
            error=str(e),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        append_log(job_id, f"ERROR: batch job failed: {e}")
        raise
    finally:
        with _jobs_lock:
            _running_jobs.discard(job_id)
