"""Kedro project settings — single hook registration point."""
from __future__ import annotations

from .hooks import RedisProgressHook

HOOKS = (RedisProgressHook(),)

# Model persistence goes through kedro_pipeline.classifiers.model_store (MLflow),
# not kedro-mlflow. Keep the disable list so an accidental install cannot attach
# the plugin hook (which would require mlflow.yml).
DISABLE_HOOKS_FOR_PLUGINS = ("kedro_mlflow", "kedro-mlflow")
