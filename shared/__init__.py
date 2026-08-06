"""Cross-service contract: config helpers, step presets, paths."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def get_config_path() -> Path:
    rel = os.environ.get("CONFIG_PATH", "configs/config-dev.jsonc")
    path = Path(rel)
    if not path.is_absolute():
        path = PACKAGE_ROOT / path
    return path


def read_config_text(path: Path | None = None) -> str:
    path = path or get_config_path()
    return path.read_text(encoding="utf-8")


def strip_jsonc(text: str) -> str:
    return re.sub(r"//.*$", "", text, flags=re.M)


def parse_config_text(text: str) -> dict[str, Any]:
    return json.loads(strip_jsonc(text))


def load_config_dict(path: Path | None = None) -> dict[str, Any]:
    return parse_config_text(read_config_text(path))


def write_config_text(text: str, path: Path | None = None) -> None:
    path = path or get_config_path()
    # Validate JSONC before writing
    parse_config_text(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def get_data_folder(config: dict[str, Any] | None = None) -> Path:
    if config and config.get("data_folder"):
        return Path(config["data_folder"])
    env = os.environ.get("DATA_FOLDER")
    if env:
        return Path(env)
    cfg = load_config_dict()
    return Path(cfg.get("data_folder", PACKAGE_ROOT / "data"))


PIPELINE_STEPS = [
    "download",
    "merge",
    "features",
    "labels",
    "train",
    "predict",
    "signals",
    "output",
]

# Manual model update: full refresh including train, then predict/signals.
TRAIN_UPDATE_STEPS = [
    "download",
    "merge",
    "features",
    "labels",
    "train",
    "predict",
    "signals",
]

# Refresh market data only (no inference).
DATA_UPDATE_STEPS = [
    "download",
]

# Inference without re-download (reuse existing klines).
INFER_STEPS = [
    "merge",
    "features",
    "labels",
    "predict",
    "signals",
]

# Daily / scheduled inference: download + infer, skip train, reuse MLflow models.
DAILY_PREDICT_STEPS = [
    *DATA_UPDATE_STEPS,
    *INFER_STEPS,
]

BACKTEST_STEPS = [
    "predict_rolling",
    "simulate",
]

ALL_STEPS = PIPELINE_STEPS + BACKTEST_STEPS


def symbol_config_overrides(symbol: str) -> dict:
    """Per-symbol runtime overrides (no mutation of the shared jsonc file)."""
    code = str(symbol).zfill(6)
    return {
        "symbol": code,
        "description": f"A-share {code} watchlist run",
        "data_sources": [{"folder": code, "file": "klines", "column_prefix": ""}],
        "mlflow_registry_prefix": f"itb_{code}_",
        "mlflow_experiment_name": f"itb_{code}",
    }
