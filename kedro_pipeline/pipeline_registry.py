"""Kedro pipeline registry.

Two modular pipelines mirror the legacy step groups in ``shared/__init__.py``:

* ``inference`` — download → merge → features → labels → train → predict →
  signals → output  (``PIPELINE_STEPS``). Implemented in phase 3.
* ``backtest``  — predict_rolling → simulate  (``BACKTEST_STEPS``).
  Implemented in phase 4.

The worker selects one of them and filters by ``node_names`` (= the requested
step subset), so ``predict`` / ``predict_rolling`` (both producing
``predictions``) live in separate pipelines to avoid an output conflict.
"""
from __future__ import annotations

from kedro.pipeline import Pipeline, node, pipeline

from .nodes.inference import (
    download,
    features,
    labels,
    merge,
    output,
    predict,
    signals,
    train,
)
from .nodes.backtest import predict_rolling, simulate


def _create_inference_pipeline() -> Pipeline:
    return pipeline(
        [
            node(download, inputs=["params:config"], outputs="raw_sources", name="download"),
            node(merge, inputs=["raw_sources", "params:config"], outputs="merged_data", name="merge"),
            node(features, inputs=["merged_data", "params:config"], outputs="features_data", name="features"),
            node(labels, inputs=["features_data", "params:config"], outputs="matrix_data", name="labels"),
            node(train, inputs=["matrix_data", "params:config"], outputs="trained_models", name="train"),
            node(
                predict,
                inputs=["matrix_data", "trained_models", "params:config"],
                outputs="predictions",
                name="predict",
            ),
            node(signals, inputs=["predictions", "params:config"], outputs="signals_data", name="signals"),
            node(output, inputs=["signals_data", "params:config"], outputs=None, name="output"),
        ]
    )


def _create_backtest_pipeline() -> Pipeline:
    return pipeline(
        [
            node(
                predict_rolling,
                inputs=["matrix_data", "params:config"],
                outputs="predictions",
                name="predict_rolling",
            ),
            node(
                simulate,
                inputs=["signals_data", "params:config"],
                outputs=None,
                name="simulate",
            ),
        ]
    )


def register_pipelines() -> dict[str, Pipeline]:
    inference = _create_inference_pipeline()
    backtest = _create_backtest_pipeline()
    return {
        "inference": inference,
        "backtest": backtest,
        "__default__": inference,
    }
