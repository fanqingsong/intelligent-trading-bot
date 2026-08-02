"""Tests for the MLflow-backed ModelStore (pyfunc flavor + Tracking + Registry).

Uses a per-test sqlite tracking/registry backend so no server is required.
Exercises:
* put→get round-trip for an sklearn pair (lc-style) and a LightGBM Booster
  pair (gb-style), verifying the PairPythonModel survives the registry and
  ``predict_df`` produces the right shape;
* that ``load_models`` eagerly resolves the label×algo grid declared in
  ``train_feature_sets``;
* that params + metrics are logged to the run;
* that a standard ``mlflow.pyfunc.load_model`` works (platform consumption);
* Production-alias promotion + alias-based lookup.
"""
from __future__ import annotations

import pytest

from kedro_pipeline.classifiers.model_store import ModelStore


def _config(tmp_path, prefix="itb_test_"):
    # MLflow 3 disables the filesystem tracking backend by default; use sqlite
    # (same class of store as the docker tracking server).
    db = (tmp_path / "mlflow.db").resolve()
    return {
        "data_folder": str(tmp_path),
        "symbol": "TEST",
        "model_folder": "MODELS",
        # Absolute path → four slashes (sqlite:////abs/path) for SQLAlchemy.
        "mlflow_tracking_uri": f"sqlite:///{db.as_posix()}",
        "mlflow_experiment_name": f"itb_test_{tmp_path.name}",
        "mlflow_registry_prefix": prefix,
        "labels": ["high_30"],
        "algorithms": [{"name": "svc"}],
        "train_feature_sets": [
            {"generator": "train_features", "config": {}}
        ],
    }


@pytest.fixture(autouse=True)
def _isolate_mlflow(monkeypatch, tmp_path):
    """Pin tracking/registry URI per test; clear leaked active runs."""
    import mlflow
    from kedro_pipeline.classifiers.model_store import disable_autolog

    cfg = _config(tmp_path)
    uri = cfg["mlflow_tracking_uri"]
    monkeypatch.setenv("MLFLOW_TRACKING_URI", uri)
    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(uri)
    if mlflow.active_run() is not None:
        mlflow.end_run()
    # Autolog is process-global; keep helpers' .fit() from spawning stray runs.
    disable_autolog()
    yield
    if mlflow.active_run() is not None:
        mlflow.end_run()
    disable_autolog()


def _features_df():
    import pandas as pd

    return pd.DataFrame({"f1": [0.0, 1.0, 2.0, 3.0], "f2": [1.0, 2.0, 3.0, 4.0]})


def _make_sklearn_pair():
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    import numpy as np

    X = np.array([[0.0, 1.0], [1.0, 2.0], [2.0, 3.0], [3.0, 4.0]])
    y = np.array([0, 0, 1, 1])
    scaler = StandardScaler().fit(X)
    model = LogisticRegression().fit(scaler.transform(X), y)
    return (model, scaler)


def _make_lgbm_pair():
    import lightgbm as lgbm
    from sklearn.preprocessing import StandardScaler
    import numpy as np

    X = np.array([[0.0, 1.0], [1.0, 2.0], [2.0, 3.0], [3.0, 4.0]])
    y = np.array([0, 0, 1, 1])
    scaler = StandardScaler().fit(X)
    model = lgbm.train(
        {"objective": "cross_entropy", "num_boost_round": 2, "verbose": -1},
        train_set=lgbm.Dataset(scaler.transform(X), y),
    )
    return (model, scaler)


# --------------------------------------------------------------------------- #
# Round-trip
# --------------------------------------------------------------------------- #

def test_put_get_sklearn_roundtrip(tmp_path):
    store = ModelStore(_config(tmp_path))
    store.put_model_pair("high_30_svc", _make_sklearn_pair(), sample_X=_features_df())

    other = ModelStore(_config(tmp_path))  # fresh process: no in-memory cache
    loaded = other.get_model_pair("high_30_svc")
    assert loaded.pair[1] is not None  # scaler present
    out = loaded.predict_df(_features_df())
    assert len(out) == 4


def test_put_get_lgbm_roundtrip(tmp_path):
    store = ModelStore(_config(tmp_path))
    store.put_model_pair("high_30_gb", _make_lgbm_pair(), sample_X=_features_df())

    other = ModelStore(_config(tmp_path))
    loaded = other.get_model_pair("high_30_gb")
    assert loaded.pair[1] is not None
    out = loaded.predict_df(_features_df())
    assert len(out) == 4


def test_get_missing_raises(tmp_path):
    store = ModelStore(_config(tmp_path))
    with pytest.raises(KeyError):
        store.get_model_pair("does_not_exist")


def test_load_models_resolves_grid(tmp_path):
    cfg = _config(tmp_path)
    store = ModelStore(cfg)
    store.put_model_pair("high_30_svc", _make_sklearn_pair(), sample_X=_features_df())

    other = ModelStore(cfg)
    loaded = other.load_models()
    assert "high_30_svc" in loaded


# --------------------------------------------------------------------------- #
# Tracking: params + metrics
# --------------------------------------------------------------------------- #

def test_put_logs_params_and_metrics(tmp_path):
    import mlflow

    cfg = _config(tmp_path)
    store = ModelStore(cfg)
    store.put_model_pair(
        "high_30_svc",
        _make_sklearn_pair(),
        sample_X=_features_df(),
        metrics={"auc": 0.91, "f1": 0.77},
        params={"C": 1.0, "n_rows": 4, "is_scale": True},
    )

    versions = store._client.search_model_versions("name='itb_test_high_30_svc'")
    assert versions
    run_id = max(versions, key=lambda v: int(v.version)).run_id
    mlflow.set_tracking_uri(cfg["mlflow_tracking_uri"])

    data = store._client.get_run(run_id).data
    assert data.params["C"] == "1.0"
    assert data.params["n_rows"] == "4"
    assert data.metrics["auc"] == pytest.approx(0.91)
    assert data.metrics["f1"] == pytest.approx(0.77)


def test_put_run_lands_in_named_experiment(tmp_path):
    """Runs/metrics must attach to mlflow_experiment_name, not Default.

    Regression: _ensure_experiment used to create the named experiment but
    never call set_experiment, so the UI showed an empty itb_{symbol}
    while Model Registry still filled.
    """
    cfg = _config(tmp_path)
    store = ModelStore(cfg)
    store.put_model_pair(
        "high_30_svc",
        _make_sklearn_pair(),
        sample_X=_features_df(),
        metrics={"auc": 0.88},
    )

    versions = store._client.search_model_versions("name='itb_test_high_30_svc'")
    assert versions
    run_id = max(versions, key=lambda v: int(v.version)).run_id
    run = store._client.get_run(run_id)
    exp = store._client.get_experiment_by_name(cfg["mlflow_experiment_name"])
    assert exp is not None
    assert run.info.experiment_id == exp.experiment_id
    assert exp.name != "Default"


def test_ensure_experiment_restores_soft_deleted(tmp_path):
    """Retrain must work after the named experiment was soft-deleted in MLflow UI."""
    cfg = _config(tmp_path)
    store = ModelStore(cfg)
    store._ensure_experiment()
    exp = store._client.get_experiment_by_name(cfg["mlflow_experiment_name"])
    assert exp is not None
    store._client.delete_experiment(exp.experiment_id)
    deleted = store._client.get_experiment_by_name(cfg["mlflow_experiment_name"])
    assert deleted is not None
    assert deleted.lifecycle_stage == "deleted"

    store.put_model_pair(
        "high_30_svc",
        _make_sklearn_pair(),
        sample_X=_features_df(),
        metrics={"auc": 0.9},
    )
    restored = store._client.get_experiment_by_name(cfg["mlflow_experiment_name"])
    assert restored is not None
    assert restored.lifecycle_stage == "active"
    versions = store._client.search_model_versions("name='itb_test_high_30_svc'")
    assert versions


def test_put_creates_logged_model_in_experiment(tmp_path):
    """MLflow 3 experiment Models tab is backed by LoggedModels (name= API)."""
    import mlflow

    if tuple(int(x) for x in mlflow.__version__.split(".")[:2]) < (3, 1):
        pytest.skip("LoggedModels require MLflow >= 3.1")

    cfg = _config(tmp_path)
    store = ModelStore(cfg)
    store.put_model_pair(
        "high_30_svc",
        _make_sklearn_pair(),
        sample_X=_features_df(),
        metrics={"auc": 0.88},
    )

    exp = store._client.get_experiment_by_name(cfg["mlflow_experiment_name"])
    assert exp is not None
    result = store._client.search_logged_models(experiment_ids=[exp.experiment_id])
    models = list(getattr(result, "models", result))
    assert models, "expected at least one LoggedModel for the experiment Models tab"
    names = {getattr(m, "name", None) for m in models}
    assert "model" in names


# --------------------------------------------------------------------------- #
# Registry: tags + alias
# --------------------------------------------------------------------------- #

def test_put_model_pair_extra_tags(tmp_path):
    """Optional tags (e.g. rolling_step) land on the latest model version."""
    store = ModelStore(_config(tmp_path))
    store.put_model_pair(
        "high_30_svc", _make_sklearn_pair(), sample_X=_features_df(),
        tags={"rolling_step": "3"},
    )

    reg_name = store._reg_name("high_30_svc")
    versions = store._client.search_model_versions(f"name='{reg_name}'")
    assert versions
    latest = max(versions, key=lambda v: int(v.version))
    tags = latest.tags if isinstance(latest.tags, dict) else {}
    assert tags.get("rolling_step") == "3"
    assert tags.get("column") == "high_30_svc"


def test_production_alias_set(tmp_path):
    """Latest version is promoted to the Production alias (when supported)."""
    store = ModelStore(_config(tmp_path))
    store.put_model_pair("high_30_svc", _make_sklearn_pair(), sample_X=_features_df())

    reg_name = store._reg_name("high_30_svc")
    try:
        mv = store._client.get_model_version_by_alias(reg_name, "Production")
    except Exception:
        pytest.skip("file-store registry does not support aliases")
    assert mv is not None


def test_get_by_alias(tmp_path):
    """get_model_pair(alias='Production') resolves (falls back to latest)."""
    store = ModelStore(_config(tmp_path))
    store.put_model_pair("high_30_svc", _make_sklearn_pair(), sample_X=_features_df())

    other = ModelStore(_config(tmp_path))
    loaded = other.get_model_pair("high_30_svc", alias="Production")
    out = loaded.predict_df(_features_df())
    assert len(out) == 4


# --------------------------------------------------------------------------- #
# Platform consumption: standard pyfunc load
# --------------------------------------------------------------------------- #

def test_pyfunc_load_roundtrip(tmp_path):
    """Any MLflow client can load the registered model via pyfunc."""
    import mlflow
    import pandas as pd

    cfg = _config(tmp_path)
    store = ModelStore(cfg)
    feats = _features_df()
    store.put_model_pair("high_30_svc", _make_sklearn_pair(), sample_X=feats)

    # In-process wrapper prediction ...
    pm = store.get_model_pair("high_30_svc")
    inner = pm.predict_df(feats)

    # ... must match a fresh pyfunc load via the Registry URI (external
    # consumption path). MLflow 3 model-version ``source`` may be a LoggedModel
    # id (models:/m-...); Registry name/version is the stable public URI.
    mlflow.set_tracking_uri(cfg["mlflow_tracking_uri"])
    versions = store._client.search_model_versions("name='itb_test_high_30_svc'")
    latest = max(versions, key=lambda v: int(v.version))
    loaded = mlflow.pyfunc.load_model(f"models:/{latest.name}/{latest.version}")
    outer = loaded.predict(feats)
    outer_series = outer["y_hat"] if isinstance(outer, pd.DataFrame) else outer

    assert list(inner.values) == pytest.approx(list(outer_series.values), abs=1e-6)


def test_put_model_pair_under_active_run(tmp_path):
    """put_model_pair nests when an outer MLflow run is already active."""
    import mlflow

    store = ModelStore(_config(tmp_path))
    mlflow.set_tracking_uri(store._client.tracking_uri)
    mlflow.set_experiment(store.experiment_name)
    with mlflow.start_run(run_name="outer"):
        store.put_model_pair("high_30_svc", _make_sklearn_pair(), sample_X=_features_df())

    other = ModelStore(_config(tmp_path))
    loaded = other.get_model_pair("high_30_svc")
    assert loaded.pair[1] is not None


def test_training_run_autolog_same_run(tmp_path):
    """fit() inside training_run shares one run with pyfunc + registry version."""
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    cfg = _config(tmp_path)
    store = ModelStore(cfg)
    feats = _features_df()
    X = feats.values
    y = np.array([0, 0, 1, 1])
    scaler = StandardScaler().fit(X)

    with store.training_run("high_30_lc"):
        # Autolog hooks LogisticRegression.fit into the active training_run.
        model = LogisticRegression(C=2.5, max_iter=200).fit(scaler.transform(X), y)
        store.put_model_pair(
            "high_30_lc",
            (model, scaler),
            sample_X=feats,
            metrics={"auc": 0.85},
            params={"n_rows": 4, "label": "high_30", "algo": "lc"},
            algo={"name": "lc", "algo": "lc", "params": {}, "train": {"C": 2.5}},
            into_active_run=True,
        )

    versions = store._client.search_model_versions(f"name='{store._reg_name('high_30_lc')}'")
    assert versions
    run_id = max(versions, key=lambda v: int(v.version)).run_id
    data = store._client.get_run(run_id).data

    # Custom metrics/params from put_model_pair
    assert data.metrics["auc"] == pytest.approx(0.85)
    assert data.params["n_rows"] == "4"
    # Sklearn autolog should have captured estimator hyperparams on the same run
    assert "C" in data.params
    assert float(data.params["C"]) == pytest.approx(2.5)

    loaded = ModelStore(cfg).get_model_pair("high_30_lc")
    assert len(loaded.predict_df(feats)) == 4
