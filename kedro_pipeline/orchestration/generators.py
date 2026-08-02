from typing import Tuple
import asyncio

import pandas as pd
from joblib import Parallel, delayed

from kedro_pipeline.common.utils import *
from kedro_pipeline.classifiers.model_store import *
from kedro_pipeline.features.gen_features import *
from kedro_pipeline.labels.gen_labels_highlow import generate_labels_highlow, generate_labels_highlow2
from kedro_pipeline.labels.gen_labels_topbot import generate_labels_topbot, generate_labels_topbot2
from kedro_pipeline.signals.gen_signals import (
    generate_smoothen_scores, generate_combine_scores,
    generate_threshold_rule, generate_threshold_rule2,
    generate_majority_vote,
)

def generate_feature_set(df: pd.DataFrame, fs: dict, config: dict, model_store: ModelStore, last_rows: int) -> Tuple[pd.DataFrame, list]:
    """
    Apply the specified resolved feature generator to the input data set.
    """

    #
    # Select columns from the data set to be processed by the feature generator
    #
    cp = fs.get("column_prefix")
    if cp:
        cp = cp + "_"
        f_cols = [col for col in df if col.startswith(cp)]
        f_df = df[f_cols]  # Alternatively: f_df = df.loc[:, df.columns.str.startswith(cf)]
        # Remove prefix because feature generators are generic (a prefix will be then added to derived features before adding them back to the main frame)
        f_df = f_df.rename(columns=lambda x: x[len(cp):] if x.startswith(cp) else x)  # Alternatively: f_df.columns = f_df.columns.str.replace(cp, "")
    else:
        f_df = df[df.columns.to_list()]  # We want to have a different data frame object to add derived features and then join them back to the main frame with prefix

    #
    # Resolve and apply feature generator functions from the configuration
    #
    generator = fs.get("generator")
    gen_config = fs.get('config', {})
    if generator == "itblib":
        features = generate_features_itblib(f_df, gen_config, last_rows=last_rows)
    elif generator == "depth":
        features = generate_features_depth(f_df)
    elif generator == "tsfresh":
        features = generate_features_tsfresh(f_df, gen_config, last_rows=last_rows)
    elif generator == "talib":
        features = generate_features_talib(f_df, gen_config, last_rows=last_rows)
    elif generator == "itbstats":
        features = generate_features_itbstats(f_df, gen_config, last_rows=last_rows)

    # Labels
    elif generator == "highlow":
        horizon = gen_config.get("horizon")

        # Binary labels whether max has exceeded a threshold or not
        print(f"Generating 'highlow' labels with horizon {horizon}...")
        features = generate_labels_highlow(f_df, horizon=horizon)

        print(f"Finished generating 'highlow' labels. {len(features)} labels generated.")
    elif generator == "highlow2":
        print(f"Generating 'highlow2' labels...")
        f_df, features = generate_labels_highlow2(f_df, gen_config)
        print(f"Finished generating 'highlow2' labels. {len(features)} labels generated.")
    elif generator == "topbot":
        column_name = gen_config.get("columns", "close")

        top_level_fracs = [0.01, 0.02, 0.03, 0.04, 0.05]
        bot_level_fracs = [-x for x in top_level_fracs]

        f_df, features = generate_labels_topbot(f_df, column_name, top_level_fracs, bot_level_fracs)
    elif generator == "topbot2":
        f_df, features = generate_labels_topbot2(f_df, gen_config)

    # Signals
    elif generator == "smoothen":
        f_df, features = generate_smoothen_scores(f_df, gen_config)
    elif generator == "combine":
        f_df, features = generate_combine_scores(f_df, gen_config)
    elif generator == "threshold_rule":
        f_df, features = generate_threshold_rule(f_df, gen_config)
    elif generator == "threshold_rule2":
        f_df, features = generate_threshold_rule2(f_df, gen_config)
    elif generator == "majority_vote":
        f_df, features = generate_majority_vote(f_df, gen_config)

    else:
        # Resolve generator name to a function reference
        generator_fn = resolve_generator_name(generator)
        if generator_fn is None:
            raise ValueError(f"Unknown feature generator name or name cannot be resolved: {generator}")

        # Call this function
        f_df, features = generator_fn(f_df, gen_config, config, model_store)

    #
    # Add generated features to the main data frame with all other columns and features
    #
    f_df = f_df[features]
    fp = fs.get("feature_prefix")
    if fp:
        f_df = f_df.add_prefix(fp + "_")

    new_features = f_df.columns.to_list()

    # Delete new columns if they already exist
    df.drop(list(set(df.columns) & set(new_features)), axis=1, inplace=True)

    df = df.join(f_df)  # Attach all derived features to the main frame

    return df, new_features

def predict_feature_set(df, fs, config, model_store: ModelStore) -> Tuple[pd.DataFrame, list]:

    train_features, labels, algorithms = get_features_labels_algorithms(fs, config)

    train_df = df[train_features]

    features = []
    out_df = pd.DataFrame(index=train_df.index)  # Collect predictions

    for label in labels:
        for model_config in algorithms:

            algo_name = model_config.get("name")
            algo_type = model_config.get("algo")
            score_column_name = label + label_algo_separator + algo_name

            # Trained model from model registry (PairPythonModel — uniform whether
            # it came from the in-memory cache or MLflow).
            model = model_store.get_model_pair(score_column_name)

            print(f"Predict '{score_column_name}'. Algorithm {algo_name}. Label: {label}. Train length {len(train_df)}. Train columns {len(train_df.columns)}")

            df_y_hat = model.predict_df(train_df)
            out_df[score_column_name] = df_y_hat
            features.append(score_column_name)

    return out_df, features

def _as_classifier_y(series: pd.Series) -> pd.Series:
    """Ensure binary labels are int 0/1 for sklearn/lightgbm classifiers.

    Uses nullable ``Int64`` when NaNs are present (pre-dropna); plain ``int``
    once the series is complete.
    """
    numeric = pd.to_numeric(series, errors="coerce")
    non_null = numeric.dropna()
    if non_null.empty:
        return series
    uniq = set(non_null.round().unique().tolist())
    if uniq <= {0.0, 1.0}:
        rounded = numeric.round()
        if rounded.isna().any():
            return rounded.astype("Int64")
        return rounded.astype(int)
    return series


def _prepare_train_xy(df, train_features, label, model_config):
    """Slice training X/y for one label×algo according to algo params."""
    algo_every_nth_row = model_config.get("params", {}).get("every_nth_row")
    train_df = df.iloc[::algo_every_nth_row, :] if algo_every_nth_row else df
    algo_train_length = model_config.get("params", {}).get("length")
    if algo_train_length:
        train_df = train_df.tail(algo_train_length)
    return train_df[train_features], _as_classifier_y(train_df[label])


def _train_label_algo_job(
    df,
    train_features: list,
    label: str,
    model_config: dict,
    config: dict,
    persist_tags: dict | None,
    *,
    persist_in_worker: bool = False,
):
    """Train (+ optional MLflow persist) one label×algo pair. Loky-picklable.

    When *persist_in_worker* is True, the job builds a fresh ``ModelStore`` and
    trains inside ``training_run`` so framework autolog and pyfunc share a run.
    """
    algo_name = model_config.get("name")
    algo_type = model_config.get("algo")
    score_column_name = label + label_algo_separator + algo_name
    df_X, df_y = _prepare_train_xy(df, train_features, label, model_config)

    print(
        f"Train '{score_column_name}'. Algorithm {algo_name}. Label: {label}. "
        f"Train length {len(df_X)}. Train columns {len(df_X.columns)}"
    )

    if persist_in_worker:
        from kedro_pipeline.classifiers.model_store import ModelStore

        store = ModelStore(config)
        with store.training_run(score_column_name, tags=persist_tags):
            model_pair = _train_one(algo_type, df_X, df_y, model_config)
            meta = _build_model_meta(
                model_pair, model_config, df_X, df_y, label, config,
            )
            store.put_model_pair(
                score_column_name,
                meta["pair"],
                tags=persist_tags,
                metrics=meta.get("metrics"),
                params=meta.get("params"),
                sample_X=meta.get("sample_X"),
                algo=meta.get("algo"),
                into_active_run=True,
            )
    else:
        model_pair = _train_one(algo_type, df_X, df_y, model_config)
        meta = _build_model_meta(
            model_pair, model_config, df_X, df_y, label, config,
        )

    return score_column_name, meta


def cache_pairs_from_meta(model_store, models: dict) -> None:
    """Populate an in-memory ModelStore cache from worker-returned meta dicts."""
    if model_store is None:
        return
    for score_column_name, meta in models.items():
        algo = meta.get("algo") or {}
        model_store.model_pairs[score_column_name] = PairPythonModel(
            pair=meta["pair"],
            algo_type=algo.get("algo"),
            algo_name=algo.get("name"),
            params=dict(algo.get("params") or {}),
        )


def train_feature_set(df, fs, config, model_store=None, *, persist_tags: dict | None = None) -> dict:
    """Train every label×algo combo and return per-model metadata for MLflow.

    Each entry is::

        {"pair": (model, scaler), "metrics": {...}, "params": {...},
         "sample_X": df_X.head(...), "algo": model_config}

    When *model_store* is provided, each model is trained inside
    :meth:`ModelStore.training_run` and persisted via
    ``put_model_pair(..., into_active_run=True)`` so framework autolog and the
    custom pyfunc share one MLflow run. *persist_tags* are attached to that run
    (e.g. ``rolling_step``).

    Parallelism is controlled by ``config["train_parallel"]``::

        {"use_multiprocessing": true, "max_workers": 4}
    """

    train_features, labels, algorithms = get_features_labels_algorithms(fs, config)

    # Only for train mode
    df = df.dropna(subset=train_features).reset_index(drop=True)
    for label in labels:
        df[label] = _as_classifier_y(df[label])
    df = df.dropna(subset=labels).reset_index(drop=True)
    for label in labels:
        # After dropna, force numpy-friendly int dtypes for classifiers.
        df[label] = _as_classifier_y(df[label])

    jobs = [(label, model_config) for label in labels for model_config in algorithms]
    tp = config.get("train_parallel") or {}
    use_mp = bool(tp.get("use_multiprocessing", False)) and len(jobs) > 1
    max_workers = tp.get("max_workers") or None

    models: dict = {}

    if use_mp:
        n_jobs = max_workers if max_workers is not None else len(jobs)
        print(f"Parallel train: {len(jobs)} label×algo jobs (n_jobs={n_jobs}).")
        # Create the shared MLflow experiment in the parent before workers race it.
        if model_store is not None:
            model_store._ensure_experiment()
        results = Parallel(n_jobs=n_jobs, backend="loky", verbose=10)(
            delayed(_train_label_algo_job)(
                df, train_features, label, model_config, config, persist_tags,
                persist_in_worker=model_store is not None,
            )
            for label, model_config in jobs
        )
        for score_column_name, meta in results:
            models[score_column_name] = meta
        cache_pairs_from_meta(model_store, models)
        return models

    for label, model_config in jobs:
        algo_name = model_config.get("name")
        algo_type = model_config.get("algo")
        score_column_name = label + label_algo_separator + algo_name
        df_X, df_y = _prepare_train_xy(df, train_features, label, model_config)

        print(
            f"Train '{score_column_name}'. Algorithm {algo_name}. Label: {label}. "
            f"Train length {len(df_X)}. Train columns {len(df_X.columns)}"
        )

        if model_store is not None:
            with model_store.training_run(score_column_name, tags=persist_tags):
                model_pair = _train_one(algo_type, df_X, df_y, model_config)
                meta = _build_model_meta(
                    model_pair, model_config, df_X, df_y, label, config,
                )
                model_store.put_model_pair(
                    score_column_name,
                    meta["pair"],
                    tags=persist_tags,
                    metrics=meta.get("metrics"),
                    params=meta.get("params"),
                    sample_X=meta.get("sample_X"),
                    algo=meta.get("algo"),
                    into_active_run=True,
                )
        else:
            model_pair = _train_one(algo_type, df_X, df_y, model_config)
            meta = _build_model_meta(
                model_pair, model_config, df_X, df_y, label, config,
            )

        models[score_column_name] = meta

    return models


def _train_one(algo_type: str, df_X, df_y, model_config: dict) -> tuple:
    """Dispatch to the algorithm-specific trainer."""
    if algo_type == "gb":
        from kedro_pipeline.classifiers.classifier_gb import train_gb
        return train_gb(df_X, df_y, model_config)
    if algo_type == "nn":
        from kedro_pipeline.classifiers.classifier_nn import train_nn
        return train_nn(df_X, df_y, model_config)
    if algo_type == "lc":
        from kedro_pipeline.classifiers.classifier_lc import train_lc
        return train_lc(df_X, df_y, model_config)
    if algo_type == "svc":
        from kedro_pipeline.classifiers.classifier_svc import train_svc
        return train_svc(df_X, df_y, model_config)
    raise ValueError(f"Unknown algorithm type {algo_type}. Check algorithm list.")


def _build_model_meta(model_pair, model_config, df_X, df_y, label, config) -> dict:
    """Compute in-sample metrics + provenance params for one trained model."""
    from kedro_pipeline.classifiers.model_store import PairPythonModel

    algo_type = model_config.get("algo")
    pm = PairPythonModel(
        pair=model_pair,
        algo_type=algo_type,
        algo_name=model_config.get("name"),
        params=dict(model_config.get("params") or {}),
    )

    metrics: dict = {}
    try:
        y_hat = pm.predict_df(df_X)
        import pandas.api.types as ptypes
        if ptypes.is_float_dtype(df_y) and ptypes.is_float_dtype(y_hat):
            metrics = compute_scores_regression(df_y, y_hat)
        else:
            metrics = compute_scores(df_y.astype(int), y_hat)
    except Exception as exc:
        print(f"WARNING: could not compute in-sample metrics for '{label}_{model_config.get('name')}': {exc}")

    params = {
        **(model_config.get("params") or {}),
        **(model_config.get("train") or {}),
        "n_rows": int(len(df_X)),
        "n_features": int(len(df_X.columns)),
        "label": label,
        "algo": model_config.get("name"),
        "eval_split": config.get("mlflow_eval_split", "in_sample"),
    }

    sample_n = min(5, len(df_X))
    sample_X = df_X.head(sample_n) if sample_n else None

    return {
        "pair": model_pair,
        "metrics": metrics,
        "params": params,
        "sample_X": sample_X,
        "algo": model_config,
    }

def get_features_labels_algorithms(fs, config) -> Tuple[list, list, list]:
    """
    Get three lists by combining the entries from default lists in the config file
    and lists in the generator config. The function will return a list from the specific
    generator config if it is available and the default list otherwise.
    For the algorithm list, it will resolve the algorithm names into their definitions if necessary.
    """
    train_features_all = config.get("train_features", [])
    train_features = fs.get("config").get("columns", [])
    if not train_features:
        train_features = fs.get("config").get("features", [])
    if not train_features:
        train_features = train_features_all

    labels_all = config.get("labels", [])
    labels = fs.get("config").get("labels", [])
    if not labels:
        labels = labels_all

    algorithms_all = config.get("algorithms")
    algorithms_str = fs.get("config").get("functions", [])
    if not algorithms_str:
        algorithms_str = fs.get("config").get("algorithms", [])
    # The algorithms can be either strings (names) or dicts (definitions) so we resolve the names
    algorithms = []
    for alg in algorithms_str:
        if isinstance(alg, str):  # Find in the list of algorithms
            alg = find_algorithm_by_name(algorithms_all, alg)
        elif not isinstance(alg, dict):
            raise ValueError(f"Algorithm has to be either dict or name")
        algorithms.append(alg)
    if not algorithms:
        algorithms = algorithms_all

    return train_features, labels, algorithms

async def output_feature_set(df, fs: dict, config: dict, model_store: ModelStore) -> None:
    from kedro_pipeline.backtesting.trades import trader_simulation

    #
    # Resolve and apply feature generator functions from the configuration
    #
    generator = fs.get("generator")
    gen_config = fs.get('config', {})

    if generator == "trader_simulation":
        generator_fn = trader_simulation
    else:
        # Resolve generator name to a function reference
        generator_fn = resolve_generator_name(generator)
        if generator_fn is None:
            raise ValueError(f"Unknown feature generator name or name cannot be resolved: {generator}")

    # Call the resolved function
    if asyncio.iscoroutinefunction(generator_fn):
        if asyncio.get_running_loop():
            await generator_fn(df, gen_config, config, model_store)
        else:
            asyncio.run(generator_fn(df, gen_config, config, model_store))
    else:
        generator_fn(df, gen_config, config, model_store)
