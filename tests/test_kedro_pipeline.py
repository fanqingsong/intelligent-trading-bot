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
