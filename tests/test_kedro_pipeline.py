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


def test_infer_steps_merge_works_without_download():
    """INFER_STEPS skips download; merge must still be runnable via catalog.

    raw_sources is a plain MemoryDataset sentinel from download→merge ordering.
    When download is skipped (INFER_STEPS), raw_sources must be OptionalMemoryDataset
    so merge can load a default instead of raising 'Data has not been saved yet'.
    """
    from shared import INFER_STEPS
    from kedro_pipeline.pipeline_registry import register_pipelines

    assert "download" not in INFER_STEPS
    assert "merge" in INFER_STEPS
    pipe = register_pipelines()["inference"].only_nodes(*INFER_STEPS)
    merge_node = next(n for n in pipe.nodes if n.name == "merge")
    assert "raw_sources" in merge_node.inputs


def test_optional_memory_dataset_with_bool_default():
    """OptionalMemoryDataset works with non-dict defaults (e.g. raw_sources=true)."""
    from kedro_pipeline.catalog.datasets import OptionalMemoryDataset

    ds = OptionalMemoryDataset(default=True)
    assert ds.exists()
    assert ds.load() is True
    ds.save(False)
    assert ds.load() is False
