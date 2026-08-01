"""Kedro node functions (one per legacy pipeline step).

* ``inference`` — download → merge → features → labels → train → predict → signals → output
* ``backtest`` — predict_rolling → simulate

Each node is a pure function ``(df/None, params, ...) -> df/None`` with no
dependency on the ``App.config`` / ``App.model_store`` globals. Shared sidecar
and model-store helpers live in :mod:`kedro_pipeline.nodes.helpers`.
"""
