"""Backward-compatible entrypoint for ``uvicorn kedro_pipeline.main:app``."""
from kedro_pipeline.worker.api import app

__all__ = ["app"]
