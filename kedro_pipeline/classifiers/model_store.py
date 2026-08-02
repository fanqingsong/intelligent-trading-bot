"""Model store backed by the MLflow Model Registry + Tracking.

Replaces the former joblib/pickle ``MODELS/<key>.pickle`` + ``.scaler`` files.
The public API used by the rest of the codebase is preserved:

* :class:`ModelStore(config)` + :meth:`load_models`
* :meth:`put_model_pair(column_name, (model, scaler))`  (train / predict_rolling)
* :meth:`get_model_pair(column_name) -> PairPythonModel`  (predict_feature_set)
* attribute ``model_path`` (kept for logging / local artifact cache)

Each ``<label>_<algo>`` key maps to one **registered model** named
``<mlflow_registry_prefix><column_name>``. ``put_model_pair`` wraps the
``(model, scaler)`` tuple into a :class:`PairPythonModel` (a standard MLflow
pyfunc) and logs it with ``registered_model_name`` — so every model is a
first-class MLflow artifact with signature / params / metrics, loadable by any
MLflow client via ``mlflow.pyfunc.load_model``. The latest version is also
promoted to the ``Production`` alias (best-effort: file-store registries may not
support aliases, in which case we fall back to "latest version").

``get_model_pair`` returns the same :class:`PairPythonModel` whether the pair
came from the in-memory cache (train→predict within one process) or from the
registry, so callers are agnostic to the source.
"""
from __future__ import annotations

import itertools
import logging
import math
import os
from contextlib import contextmanager
from pathlib import Path

import mlflow
import pandas as pd
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

log = logging.getLogger("model_store")


label_algo_separator = "_"

_autolog_enabled = False


def _default_tracking_uri() -> str:
    return os.environ.get("MLFLOW_TRACKING_URI") or "http://localhost:5000"


def enable_autolog() -> None:
    """Enable sklearn / lightgbm / tensorflow autolog into the active run.

    ``log_models=False`` so we keep registering our custom ``PairPythonModel``
    pyfunc only (no duplicate native-flavor artifacts).
    """
    global _autolog_enabled
    if _autolog_enabled:
        return
    mlflow.sklearn.autolog(log_models=False, silent=True)
    mlflow.lightgbm.autolog(log_models=False, silent=True)
    try:
        mlflow.tensorflow.autolog(log_models=False, silent=True)
    except Exception as exc:  # tensorflow/keras may be unavailable
        log.debug("Skipping tensorflow autolog: %s", exc)
    _autolog_enabled = True


def disable_autolog() -> None:
    """Turn off framework autologging (used by tests to avoid stray runs)."""
    global _autolog_enabled
    mlflow.sklearn.autolog(disable=True)
    mlflow.lightgbm.autolog(disable=True)
    try:
        mlflow.tensorflow.autolog(disable=True)
    except Exception:
        pass
    _autolog_enabled = False


def _flatten_params(d: dict, prefix: str = "") -> dict:
    """Flatten a (possibly nested) dict into ``{"a.b": value}`` for log_params.

    MLflow params must be scalar strings; lists/dicts are JSON-encoded so they
    survive a round-trip and stay readable in the UI.
    """
    import json

    flat: dict[str, str] = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict):
            flat.update(_flatten_params(v, key))
        elif isinstance(v, (list, tuple)):
            flat[key] = json.dumps(list(v))
        elif v is None:
            flat[key] = ""
        else:
            flat[key] = str(v)
    return flat


# --------------------------------------------------------------------------- #
# pyfunc wrapper — the single inference implementation
# --------------------------------------------------------------------------- #


class PairPythonModel(mlflow.pyfunc.PythonModel):
    """A standard MLflow pyfunc wrapping the ``(model, scaler)`` pair.

    Dispatches ``predict`` to the existing ``predict_<algo>`` functions, so the
    output format is byte-for-byte identical to the legacy in-process path.
    The same instance is returned by :meth:`ModelStore.get_model_pair` whether
    the pair was just trained (in-memory cache) or loaded from the registry,
    which keeps :func:`predict_feature_set` agnostic to the model's source.
    """

    def __init__(self, pair: tuple, algo_type: str, algo_name: str | None = None,
                 params: dict | None = None):
        self.pair = pair              # (model, scaler)
        self.algo_type = algo_type    # gb | nn | lc | svc
        self.algo_name = algo_name or algo_type
        # Full params dict — predict_svc reads params.is_regression at inference.
        self.params = params or {}

    # -- the two faces of the same call ----------------------------------- #

    def predict(self, context, model_input: pd.DataFrame, params=None) -> pd.DataFrame:  # noqa: D401
        if not isinstance(model_input, pd.DataFrame):
            model_input = pd.DataFrame(model_input)
        return self.predict_df(model_input).to_frame("y_hat")

    def predict_df(self, df_X: pd.DataFrame) -> pd.Series:
        """Unified inference entry used by the pipeline (and by ``predict``)."""
        model_config = {
            "name": self.algo_name,
            "algo": self.algo_type,
            "params": self.params,
        }
        if self.algo_type == "gb":
            from kedro_pipeline.classifiers.classifier_gb import predict_gb
            return predict_gb(self.pair, df_X, model_config)
        if self.algo_type == "nn":
            from kedro_pipeline.classifiers.classifier_nn import predict_nn
            return predict_nn(self.pair, df_X, model_config)
        if self.algo_type == "lc":
            from kedro_pipeline.classifiers.classifier_lc import predict_lc
            return predict_lc(self.pair, df_X, model_config)
        if self.algo_type == "svc":
            from kedro_pipeline.classifiers.classifier_svc import predict_svc
            return predict_svc(self.pair, df_X, model_config)
        raise ValueError(f"Unknown algorithm type {self.algo_type!r}.")


# --------------------------------------------------------------------------- #
# ModelStore
# --------------------------------------------------------------------------- #


class ModelStore:
    """Persistent model store backed by MLflow Model Registry + Tracking."""

    def __init__(self, config):
        self.config = config
        self.symbol = config["symbol"]

        # Local cache / temp root (kept for log compatibility with the old steps).
        data_path = Path(config["data_folder"]) / self.symbol
        model_path = Path(config.get("model_folder", "MODELS"))
        if not model_path.is_absolute():
            model_path = data_path / model_path
        self.model_path = model_path.resolve()

        self.registry_prefix = config.get("mlflow_registry_prefix", f"itb_{self.symbol}_")
        self.experiment_name = config.get("mlflow_experiment_name", f"itb_{self.symbol}")
        self.default_alias = config.get("mlflow_default_alias", "Production")
        self.log_input_example = bool(config.get("mlflow_log_input_example", True))

        # Env wins over config so docker compose (MLFLOW_TRACKING_URI=http://mlflow:5000)
        # is not overridden by App/parameters defaults of http://localhost:5000.
        uri = os.environ.get("MLFLOW_TRACKING_URI") or config.get("mlflow_tracking_uri") or _default_tracking_uri()
        mlflow.set_tracking_uri(uri)
        self._client = MlflowClient(tracking_uri=uri)

        # In-memory cache of loaded PairPythonModel (train→predict within one process).
        self.model_pairs: dict[str, PairPythonModel] = {}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def load_models(self):
        """Pre-load all label×algo pairs declared in ``train_feature_sets``.

        Mirrors the legacy eager-load semantics. Missing models are skipped
        (as the old file-based loader did) so predict can run after a partial
        train.
        """
        self.model_pairs = self._load_models_for_generators()
        return self.model_pairs

    def get_model_pair(self, column_name: str, *, alias: str | None = None,
                       version: str | int | None = None) -> PairPythonModel:
        """Return the :class:`PairPythonModel` for *column_name*.

        Served from the in-memory cache when available (e.g. predict called
        right after train in the same process); otherwise loaded from the
        registry — by alias (default ``Production``) or explicit version.
        """
        if column_name in self.model_pairs:
            return self.model_pairs[column_name]

        pair = self._load_pair_from_registry(column_name, alias=alias, version=version)
        self.model_pairs[column_name] = pair
        return pair

    def put_model_pair(
        self,
        column_name: str,
        model_pair: tuple,
        *,
        tags: dict | None = None,
        metrics: dict | None = None,
        params: dict | None = None,
        sample_X=None,
        algo: dict | None = None,
        into_active_run: bool = False,
    ) -> PairPythonModel:
        """Wrap & persist a ``(model, scaler)`` pair as a new MLflow model version.

        * tags        — merged into the run + model-version tags (symbol/label/...).
        * metrics     — dict of numeric metrics logged to the run (auc, f1, ...).
        * params      — dict of hyperparams / provenance logged to the run.
        * sample_X    — a small feature DataFrame used to infer the model
                        signature + input example.
        * algo        — the algorithm config dict (``{name, algo, params, train}``);
                        used to set ``algo_type``/``params`` on the wrapper and to
                        derive default params. Falls back to parsing *column_name*.
        * into_active_run — when True, log into the current active run (no new
                        ``start_run``). Use with :meth:`training_run` so autolog
                        from ``fit``/``train`` lands in the same run as the pyfunc.
        """
        algo_name, algo_type, algo_params = self._resolve_algo(column_name, algo)
        pm = PairPythonModel(
            pair=model_pair,
            algo_type=algo_type,
            algo_name=algo_name,
            params=algo_params,
        )

        self._log_and_register_pair(
            column_name=column_name,
            pm=pm,
            tags=tags,
            metrics=metrics,
            params=self._default_params(column_name, algo_name, algo, params),
            sample_X=sample_X,
            into_active_run=into_active_run,
        )
        self.model_pairs[column_name] = pm
        return pm

    @contextmanager
    def training_run(self, column_name: str, *, tags: dict | None = None):
        """Start an MLflow run for train + register of one label×algo pair.

        Call :meth:`put_model_pair` with ``into_active_run=True`` inside the
        block so framework autolog and our pyfunc share one run.
        """
        enable_autolog()
        label, algo = score_to_label_algo_pair(column_name)
        reg_name = self._reg_name(column_name)
        self._ensure_experiment()
        self._ensure_registered(reg_name)

        run_tags = {
            "symbol": self.symbol,
            "label": label,
            "algo": algo,
            "column": column_name,
        }
        if tags:
            run_tags.update({k: str(v) for k, v in tags.items()})

        nested = mlflow.active_run() is not None
        with mlflow.start_run(run_name=reg_name, tags=run_tags, nested=nested):
            yield

    # ------------------------------------------------------------------ #
    # Algorithm resolution
    # ------------------------------------------------------------------ #

    def _resolve_algo(self, column_name: str, algo: dict | None):
        """Return ``(algo_name, algo_type, params_dict)`` for the wrapper."""
        if algo:
            name = algo.get("name") or column_name.rsplit(label_algo_separator, 1)[-1]
            algo_type = algo.get("algo", name)
            params = dict(algo.get("params") or {})
            return name, algo_type, params
        # Fall back to parsing the column name.
        _, algo_name = score_to_label_algo_pair(column_name)
        return algo_name, algo_name, {}

    def _default_params(self, column_name: str, algo_name: str,
                        algo: dict | None, extra: dict | None) -> dict:
        params: dict = {}
        if algo:
            params.update(algo.get("params") or {})
            params.update(algo.get("train") or {})
        if extra:
            params.update(extra)
        params.setdefault("symbol", self.symbol)
        params.setdefault("label", score_to_label_algo_pair(column_name)[0])
        params.setdefault("algo", algo_name)
        return params

    # ------------------------------------------------------------------ #
    # MLflow persistence
    # ------------------------------------------------------------------ #

    def _reg_name(self, column_name: str) -> str:
        return f"{self.registry_prefix}{column_name}"

    @staticmethod
    def _is_already_exists(exc: BaseException) -> bool:
        msg = str(exc)
        return "RESOURCE_ALREADY_EXISTS" in msg or "already exists" in msg.lower()

    def _ensure_experiment(self):
        """Activate the named experiment, creating or restoring it if needed.

        ``set_experiment`` creates when absent, but refuses a soft-deleted name
        (``Cannot set a deleted experiment``). Restore that experiment first so
        retrain after UI/manual delete keeps working.
        Parallel label×algo workers may race on first create — treat
        ``RESOURCE_ALREADY_EXISTS`` as success and retry the set.
        """
        exp = self._client.get_experiment_by_name(self.experiment_name)
        if exp is not None and getattr(exp, "lifecycle_stage", None) == "deleted":
            self._client.restore_experiment(exp.experiment_id)
        try:
            mlflow.set_experiment(self.experiment_name)
        except MlflowException as exc:
            msg = str(exc)
            if "deleted experiment" in msg.lower():
                exp = self._client.get_experiment_by_name(self.experiment_name)
                if exp is None:
                    raise
                self._client.restore_experiment(exp.experiment_id)
                mlflow.set_experiment(self.experiment_name)
                return
            if not self._is_already_exists(exc):
                raise
            mlflow.set_experiment(self.experiment_name)

    def _ensure_registered(self, name: str):
        try:
            self._client.create_registered_model(name)
        except MlflowException as exc:
            # Already exists — expected on every put after the first.
            msg = str(exc)
            if "RESOURCE_ALREADY_EXISTS" not in msg and "already exists" not in msg.lower():
                raise

    def _log_and_register_pair(
        self,
        *,
        column_name: str,
        pm: PairPythonModel,
        tags: dict | None,
        metrics: dict | None,
        params: dict | None,
        sample_X,
        into_active_run: bool = False,
    ):
        label, algo = score_to_label_algo_pair(column_name)
        reg_name = self._reg_name(column_name)
        self._ensure_experiment()
        self._ensure_registered(reg_name)

        run_tags = {
            "symbol": self.symbol,
            "label": label,
            "algo": algo,
            "column": column_name,
        }
        if tags:
            run_tags.update({k: str(v) for k, v in tags.items()})

        signature = None
        input_example = None
        if sample_X is not None and len(sample_X):
            try:
                y_sample = pm.predict_df(sample_X.head(5))
                from mlflow.models import infer_signature

                signature = infer_signature(sample_X.head(5), pd.DataFrame({"y_hat": y_sample}))
                if self.log_input_example:
                    input_example = sample_X.head(3)
            except Exception as exc:  # signature is best-effort; never block training
                log.warning("Could not infer MLflow signature for '%s': %s", column_name, exc)

        if into_active_run:
            if mlflow.active_run() is None:
                raise RuntimeError(
                    "put_model_pair(into_active_run=True) requires an active MLflow run "
                    "(use ModelStore.training_run)."
                )
            self._write_pair_to_active_run(
                reg_name=reg_name,
                pm=pm,
                run_tags=run_tags,
                metrics=metrics,
                params=params,
                signature=signature,
                input_example=input_example,
            )
            return

        nested = mlflow.active_run() is not None
        with mlflow.start_run(run_name=reg_name, tags=run_tags, nested=nested):
            self._write_pair_to_active_run(
                reg_name=reg_name,
                pm=pm,
                run_tags=run_tags,
                metrics=metrics,
                params=params,
                signature=signature,
                input_example=input_example,
            )

    def _write_pair_to_active_run(
        self,
        *,
        reg_name: str,
        pm: PairPythonModel,
        run_tags: dict,
        metrics: dict | None,
        params: dict | None,
        signature,
        input_example,
    ):
        if params:
            flat = _flatten_params(params)
            # Autolog may already have written estimator hyperparams into this run.
            active = mlflow.active_run()
            if active is not None:
                existing = set(self._client.get_run(active.info.run_id).data.params)
                flat = {k: v for k, v in flat.items() if k not in existing}
            if flat:
                mlflow.log_params(flat)
        if metrics:
            self._log_metrics(metrics)

        # MLflow 3+: use ``name`` (not deprecated ``artifact_path``) so the
        # experiment Models tab gets a first-class LoggedModel entry.
        # ``registered_model_name`` still publishes to the Model Registry.
        mlflow.pyfunc.log_model(
            python_model=pm,
            name="model",
            registered_model_name=reg_name,
            signature=signature,
            input_example=input_example,
        )
        # pyfunc.log_model auto-creates a version but does not propagate run
        # tags to the model-version tags — set them explicitly so the
        # Registry UI / API surfaces symbol/label/algo/rolling_step/...
        latest = self._latest_version(reg_name)
        if latest is not None:
            for k, v in run_tags.items():
                self._client.set_model_version_tag(reg_name, latest.version, k, v)
            self._promote_to_alias(reg_name, latest.version)

    def _log_metrics(self, metrics: dict):
        clean = {}
        for k, v in (metrics or {}).items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isnan(fv) or math.isinf(fv):
                continue
            clean[k] = fv
        if clean:
            mlflow.log_metrics(clean)

    def _promote_to_alias(self, reg_name: str, version: str):
        """Point the default alias (Production) at the given version.

        Best-effort: some registry backends (notably the local file store used
        in tests) do not support aliases; in that case we log and rely on
        "latest version" semantics downstream.
        """
        try:
            self._client.set_registered_model_alias(reg_name, self.default_alias, version)
        except MlflowException as exc:  # backend without alias support
            log.warning(
                "Could not set alias '%s' on '%s' v%s (%s). Falling back to latest-version lookup.",
                self.default_alias, reg_name, version, exc,
            )

    def _latest_version(self, reg_name: str) -> str | None:
        versions = self._client.search_model_versions(f"name='{reg_name}'")
        if not versions:
            return None
        return max(versions, key=lambda v: int(v.version))

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #

    def _resolve_version(self, reg_name: str, alias: str | None,
                         version: str | int | None) -> str:
        if version is not None:
            return str(version)
        target_alias = alias or self.default_alias
        try:
            mv = self._client.get_model_version_by_alias(reg_name, target_alias)
            return str(mv.version)
        except MlflowException:
            # Alias unsupported / not set → newest version.
            versions = self._client.search_model_versions(f"name='{reg_name}'")
            if not versions:
                raise KeyError(f"No MLflow model versions registered for '{reg_name}'")
            latest = max(versions, key=lambda v: int(v.version))
            return str(latest.version)

    def _load_pair_from_registry(self, column_name: str, *, alias: str | None = None,
                                 version: str | int | None = None) -> PairPythonModel:
        reg_name = self._reg_name(column_name)
        ver = self._resolve_version(reg_name, alias, version)
        loaded = mlflow.pyfunc.load_model(f"models:/{reg_name}/{ver}")
        # Unwrap to our PairPythonModel so callers get predict_df / .pair.
        try:
            return loaded.unwrap_python_model()
        except AttributeError:
            # Foreign flavor (shouldn't happen for our own models) — wrap as-is.
            return PairPythonModel(pair=(loaded, None), algo_type="pyfunc", algo_name=reg_name)

    # ------------------------------------------------------------------ #
    # Eager loading (legacy label×algo resolution)
    # ------------------------------------------------------------------ #

    def _load_models_for_generators(self) -> dict:
        """Load all model pairs really used, per the algorithm/train config."""
        labels_default = self.config.get("labels", [])
        algorithms_default = self.config.get("algorithms")

        train_feature_sets = self.config.get("train_feature_sets", [])
        models: dict[str, PairPythonModel] = {}
        for fs in train_feature_sets:
            labels = fs.get("config", {}).get("labels", []) or labels_default

            algorithm_names = fs.get("config", {}).get("functions", []) or fs.get("config", {}).get("algorithms", [])
            algorithms = resolve_algorithms_for_generator(algorithm_names, algorithms_default)

            for label, algo in itertools.product(labels, algorithms):
                score_column_name = label + label_algo_separator + algo["name"]
                try:
                    models[score_column_name] = self.get_model_pair(score_column_name)
                except Exception:
                    log.error(
                        "Cannot load model '%s' from MLflow registry. Skip.", score_column_name
                    )
        return models


def resolve_algorithms_for_generator(algorithm_names: list, algorithms_default: list):
    """Resolve algorithm configs for a list of algorithm names/dicts."""
    algorithms = []
    for alg in algorithm_names:
        if isinstance(alg, str):
            alg = find_algorithm_by_name(algorithms_default, alg)
        elif not isinstance(alg, dict):
            raise ValueError("Algorithm has to be either dict or name")
        algorithms.append(alg)
    if not algorithms:
        algorithms = algorithms_default
    return algorithms


def find_algorithm_by_name(algorithms: list, name: str):
    """Find the algorithm config entry with the given name."""
    return next(x for x in algorithms if x.get("name") == name)


def score_to_label_algo_pair(score_column_name: str):
    """Parse a score column name into ``(label_name, algo_name)``.

    Splits from the right because label names may themselves contain underscores.
    """
    label_name, algo_name = score_column_name.rsplit(label_algo_separator, 1)
    return label_name, algo_name
