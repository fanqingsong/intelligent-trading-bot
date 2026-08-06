"""Smoke tests for the Kedro pipeline registry and backtest nodes.

Does not run a full rolling train/predict — only verifies wiring.
"""
from __future__ import annotations


def test_register_pipelines_keys():
    from kedro_pipeline.pipeline_registry import register_pipelines

    pipes = register_pipelines()
    assert "inference" in pipes
    assert "backtest" in pipes
    assert "__default__" in pipes
    assert pipes["__default__"] is pipes["inference"]


def test_backtest_pipeline_node_names():
    from kedro_pipeline.pipeline_registry import register_pipelines

    backtest = register_pipelines()["backtest"]
    names = {n.name for n in backtest.nodes}
    assert names == {"predict_rolling", "simulate"}


def test_backtest_nodes_importable():
    from kedro_pipeline.nodes.backtest import predict_rolling, simulate

    assert callable(predict_rolling)
    assert callable(simulate)


def test_optional_memory_dataset_loads_default_when_empty():
    from kedro_pipeline.catalog.datasets import OptionalMemoryDataset

    ds = OptionalMemoryDataset(default={})
    assert ds.exists()
    assert ds.load() == {}
    ds.save({"a": 1})
    assert ds.load() == {"a": 1}


def test_daily_predict_nodes_include_predict_without_train():
    """Daily predict skips train; predict must still be runnable via catalog."""
    from shared import DAILY_PREDICT_STEPS
    from kedro_pipeline.pipeline_registry import register_pipelines

    assert "train" not in DAILY_PREDICT_STEPS
    assert "predict" in DAILY_PREDICT_STEPS
    pipe = register_pipelines()["inference"].only_nodes(*DAILY_PREDICT_STEPS)
    names = [n.name for n in pipe.nodes]
    assert "predict" in names
    assert "train" not in names
    predict_node = next(n for n in pipe.nodes if n.name == "predict")
    assert "trained_models" in predict_node.inputs
