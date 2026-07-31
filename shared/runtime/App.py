from pathlib import Path
import json
import re

from shared.domain.model_store import *


PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent


class App:
    """Globally visible variables."""

    # System
    loop = None  # asyncio main loop
    sched = None  # Scheduler

    analyzer = None  # Store and analyze data

    #
    # State of the server (updated after each interval)
    #
    # State 0 or None or empty means ok. String and other non empty objects mean error
    error_status = 0  # Networks, connections, exceptions etc. what does not allow us to work at all
    server_status = 0  # If server allow us to trade (maintenance, down etc.)
    account_status = 0  # If account allows us to trade (funds, suspended etc.)
    trade_state_status = 0  # Something wrong with our trading logic (wrong use, inconsistent state etc. what we cannot recover)

    df = None  # Data from the latest analysis

    # Trade simulator
    transaction = None

    model_store: ModelStore = None

    #
    # Constant configuration parameters
    #
    config = {
        # Venue
        "venue": "ashare",

        #
        # Conventions for the file and column names
        #
        "merge_file_name": "data.csv",
        "feature_file_name": "features.csv",
        "matrix_file_name": "matrix.csv",
        "predict_file_name": "predictions.csv",  # predict, predict-rolling
        "signal_file_name": "signals.csv",
        "signal_models_file_name": "signal_models",

        "model_folder": "MODELS",

        "time_column": "timestamp",

        # File locations
        "data_folder": "/app/data",

        # ==============================================
        # === DOWNLOADER, MERGER and (online) READER ===

        # Symbol determines sub-folder and used in other identifiers
        "symbol": "600519",

        # This parameter determines time raster (granularity) for the data
        # It is pandas frequency
        "freq": "1D",

        # This list is used for downloading and then merging data
        # "folder" is symbol name for downloading. prefix will be added column names during merge
        "data_sources": [],

        # ==========================
        # === FEATURE GENERATION ===

        # What columns to pass to which feature generator and how to prefix its derived features
        # Each executes one feature generation function applied to columns with the specified prefix
        "feature_sets": [],

        # ========================
        # === LABEL GENERATION ===

        "label_sets": [],

        # ===========================
        # === MODEL TRAIN/PREDICT ===
        #     predict off-line and on-line

        "label_horizon": 0,  # This number of tail rows will be excluded from model training
        "train_length": 0,  # train set maximum size. algorithms may decrease this length

        # List all features to be used for training/prediction by selecting them from the result of feature generation
        # The list of features can be found in the output of the feature generation (but not all must be used)
        # Currently the same feature set for all algorithms
        "train_features": [],

        # Labels to be used for training/prediction by all algorithms
        # List of available labels can be found in the output of the label generation (but not all must be used)
        "labels": [],

        # Algorithms and their configurations to be used for training/prediction
        "algorithms": [],

        # ===========================
        # ONLINE (PREDICTION) PARAMETERS
        # Minimum history length required to compute derived features
        "features_horizon": 10,

        # ===============
        # === SIGNALS ===

        "signal_sets": [],

        # ===============
        # === TRADING ===
        "trade_model": {},

        "simulate_model": {},
    }


def data_provider_problems_exist():
    if App.error_status != 0:
        return True
    if App.server_status != 0:
        return True
    return False


def problems_exist():
    if App.error_status != 0:
        return True
    if App.server_status != 0:
        return True
    if App.account_status != 0:
        return True
    if App.trade_state_status != 0:
        return True
    return False


def load_config(config_file):
    if config_file:
        config_file_path = Path(config_file)
        if not config_file_path.is_absolute():
            config_file_path = PACKAGE_ROOT / config_file
        with open(config_file_path, encoding='utf-8') as json_file:
            conf_str = json_file.read()

            # Remove everything starting with // and till the line end
            conf_str = re.sub(r"//.*$", "", conf_str, flags=re.M)

            conf_json = json.loads(conf_str)
            App.config.update(conf_json)


if __name__ == "__main__":
    pass
