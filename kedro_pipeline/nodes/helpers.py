"""Shared helpers used by inference and backtest nodes."""
from __future__ import annotations

from kedro_pipeline.classifiers.model_store import ModelStore
from kedro_pipeline.catalog.paths import resolve_data_path


def append_feature_list(config: dict, file_key: str, features: list[str]) -> None:
    """Append the derived feature/label names to the ``<file>.txt`` sidecar."""
    out_path = resolve_data_path(config, file_key).with_suffix(".txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a+", encoding="utf-8") as f:
        f.write(", ".join(f'"{name}"' for name in features) + "\n\n")


def store_scores(config: dict, file_key: str, score_lines: list[str]) -> None:
    """Append prediction score lines to the ``<file>.txt`` sidecar."""
    out_path = resolve_data_path(config, file_key).with_suffix(".txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a+", encoding="utf-8") as f:
        f.write("\n".join(str(x) for x in score_lines) + "\n\n")


def new_model_store(config: dict) -> ModelStore:
    store = ModelStore(config)
    store.load_models()
    return store
