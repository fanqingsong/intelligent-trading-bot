"""Read-only MLflow query helpers for the API control plane.

Thin wrapper over :class:`mlflow.tracking.MlflowClient` that resolves the
tracking URI / registry prefix the same way :class:`ModelStore` does, so the
API surfaces exactly what the pipeline logs. The backend never loads models —
it only lists runs / registered models / versions / metrics / params.
"""
from __future__ import annotations

import os
from typing import Any

import mlflow
from mlflow.tracking import MlflowClient

from shared import load_config_dict


def _resolve_uri(config: dict[str, Any] | None) -> str:
    cfg = config or load_config_dict()
    return (
        os.environ.get("MLFLOW_TRACKING_URI")
        or cfg.get("mlflow_tracking_uri")
        or "http://localhost:5000"
    )


def _prefix(config: dict[str, Any] | None, symbol: str | None) -> str:
    cfg = config or load_config_dict()
    sym = symbol or cfg.get("symbol", "")
    return cfg.get("mlflow_registry_prefix") or f"itb_{sym}_"


def _client(uri: str) -> MlflowClient:
    mlflow.set_tracking_uri(uri)
    return MlflowClient(tracking_uri=uri)


def mlflow_info(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tracking URI, UI URL and active registry prefix."""
    cfg = config or load_config_dict()
    uri = _resolve_uri(cfg)
    return {
        "tracking_uri": uri,
        "ui_url": uri.rstrip("/") if uri.startswith("http") else None,
        "registry_prefix": cfg.get("mlflow_registry_prefix", "itb_"),
    }


def _version_payload(client: MlflowClient, mv) -> dict[str, Any]:
    run = None
    try:
        run = mv.run_id and client.get_run(mv.run_id)
    except Exception:
        run = None
    data = run.data if run else None
    return {
        "version": int(mv.version),
        "run_id": mv.run_id,
        "status": mv.status,
        "aliases": list(mv.aliases or []),
        "tags": dict(mv.tags or {}),
        "metrics": dict(data.metrics or {}) if data else {},
        "params": dict(data.params or {}) if data else {},
        "created_at": mv.creation_timestamp,
    }


def list_registered_models(
    config: dict[str, Any] | None = None,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    """List registered models matching the symbol's registry prefix."""
    cfg = config or load_config_dict()
    uri = _resolve_uri(cfg)
    client = _client(uri)
    prefix = _prefix(cfg, symbol)

    out: list[dict[str, Any]] = []
    # search_registered_models supports a name_like filter on the prefix.
    for rm in client.search_registered_models(f"name LIKE '%{prefix}%'"):
        versions = client.search_model_versions(f"name='{rm.name}'")
        if not versions:
            continue
        latest = max(versions, key=lambda v: int(v.version))
        # Pull run data (metrics/params) for the latest version.
        run_data = None
        try:
            run_data = client.get_run(latest.run_id).data
        except Exception:
            pass
        out.append({
            "name": rm.name,
            "column": rm.name[len(prefix):] if rm.name.startswith(prefix) else rm.name,
            "latest_version": int(latest.version),
            "aliases": list(latest.aliases or []),
            "metrics": dict(run_data.metrics or {}) if run_data else {},
            "params": dict(run_data.params or {}) if run_data else {},
            "run_id": latest.run_id,
            "n_versions": len(versions),
        })
    return out


def list_model_versions(
    name: str,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Full version history for one registered model."""
    cfg = config or load_config_dict()
    client = _client(_resolve_uri(cfg))
    versions = client.search_model_versions(f"name='{name}'")
    versions.sort(key=lambda v: int(v.version), reverse=True)
    return [_version_payload(client, v) for v in versions]


def list_runs(
    config: dict[str, Any] | None = None,
    symbol: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Recent runs for the symbol's experiment."""
    cfg = config or load_config_dict()
    uri = _resolve_uri(cfg)
    client = _client(uri)
    sym = symbol or cfg.get("symbol", "")
    experiment_name = cfg.get("mlflow_experiment_name") or f"itb_{sym}"

    exp = client.get_experiment_by_name(experiment_name)
    if exp is None:
        return []
    runs = client.search_runs(
        [exp.experiment_id], max_results=limit, order_by=["attributes.start_time DESC"]
    )
    out: list[dict[str, Any]] = []
    for r in runs:
        out.append({
            "run_id": r.info.run_id,
            "run_name": r.data.tags.get("mlflow.runName") if r.data else None,
            "status": r.info.status,
            "start_time": r.info.start_time,
            "metrics": dict(r.data.metrics or {}) if r.data else {},
            "params": dict(r.data.params or {}) if r.data else {},
            "tags": {
                k: v for k, v in (r.data.tags or {}).items()
                if not k.startswith("mlflow.")
            } if r.data else {},
        })
    return out
