from datetime import timedelta, datetime
from pathlib import Path

import pandas as pd
import pandas.api.types as ptypes

from kedro_pipeline.worker.app import *
from kedro_pipeline.common.utils import *
from kedro_pipeline.classifiers.model_store import *

import logging
log = logging.getLogger('notifier')


async def trader_simulation(df, model: dict, config: dict, model_store: ModelStore):
    try:
        transaction = await generate_trader_transaction(df, model, config)
    except Exception as e:
        log.error(f"Error in trader_simulation function: {e}")
        return
    if not transaction:
        return

    profit, profit_percent, _, _ = await generate_transaction_stats()
    log.info(
        "Trade simulation recorded locally: %s profit=%.4f (%.2f%%)",
        transaction.get("status"),
        profit,
        profit_percent,
    )


async def generate_trader_transaction(df, model: dict, config: dict):
    """
    Very simple trade strategy where we only buy and sell using the whole available amount
    """
    transaction_path = get_transaction_path()

    buy_signal_column = model.get("buy_signal_column")
    sell_signal_column = model.get("sell_signal_column")

    signal = get_signal(df, buy_signal_column, sell_signal_column)
    signal_side = signal.get("side")
    close_price = signal.get("close_price")
    close_time = signal.get("close_time")

    # Previous transaction: BUY (we are currently selling) or SELL (we are currently buying)
    if not App.transaction:
        t_status = None
        t_price = None
    else:
        t_status = App.transaction.get("status")
        t_price = App.transaction.get("price")
    if signal_side == "BUY" and (not t_status or t_status == "SELL"):
        profit = t_price - close_price if t_price else 0.0
        t_dict = dict(timestamp=str(close_time), price=close_price, profit=profit, status="BUY")
    elif signal_side == "SELL" and (not t_status or t_status == "BUY"):
        profit = close_price - t_price if t_price else 0.0
        t_dict = dict(timestamp=str(close_time), price=close_price, profit=profit, status="SELL")
    else:
        return None

    # Save this transaction
    App.transaction = t_dict
    with open(transaction_path, 'a+') as f:
        f.write(",".join([f"{v:.6f}" if isinstance(v, float) else str(v) for v in t_dict.values()]) + "\n")

    log.info(f"Trade simulator transaction: {t_dict}")

    return t_dict


async def generate_transaction_stats():
    """Here we assume that the latest transaction is saved in the file and this function computes various properties."""
    transaction_path = get_transaction_path()

    df = pd.read_csv(transaction_path, parse_dates=[0], header=None, names=["timestamp", "close", "profit", "status"], date_format="ISO8601")

    mask = (df['timestamp'] >= (datetime.now() - timedelta(weeks=4)))
    df = df[max(mask.idxmax()-1, 0):]  # We add one previous row to use the previous close

    df["prev_close"] = df["close"].shift()
    df["profit_percent"] = df.apply(lambda x: (100.0 * x["profit"] / x["prev_close"]) if x["prev_close"] else 0.0, axis=1)

    df = df.iloc[1:]  # Remove the first row which was added to compute relative profit

    long_df = df[df["status"] == "SELL"]
    short_df = df[df["status"] == "BUY"]

    last_transaction = df.iloc[-1]
    transaction_type = last_transaction["status"]
    profit = last_transaction["profit"]
    profit_percent = last_transaction["profit_percent"]

    if transaction_type == "SELL":
        df2 = long_df
    elif transaction_type == "BUY":
        df2 = short_df
    else:
        df2 = df.iloc[0:0]

    profit_descr = df2["profit"].describe()  # count, mean, std, min, 50% max
    profit_percent_descr = df2["profit_percent"].describe()  # count, mean, std, min, 50% max

    return profit, profit_percent, profit_descr, profit_percent_descr


def get_signal(df, buy_signal_column, sell_signal_column):
    """From the last row, produce and return an object with parameters important for trading."""
    freq = App.config["freq"]

    row = df.iloc[-1]  # Last row stores the latest values we need

    interval_length = freq_to_timedelta(freq)

    if not ptypes.is_datetime64_any_dtype(df.index):  # Alternatively df.index.inferred_type == "datetime64"
        raise ValueError(f"Index of the data frame must be of datetime type.")
    close_time = row.name + interval_length  # Add interval length because timestamp is start of the interval

    close_price = row["close"]

    buy_signal = row[buy_signal_column]
    sell_signal = row[sell_signal_column]

    if buy_signal and sell_signal:  # Both signals are true - should not happen
        signal_side = "BOTH"
    elif buy_signal:
        signal_side = "BUY"
    elif sell_signal:
        signal_side = "SELL"
    else:
        signal_side = ""

    signal = {"side": signal_side, "close_price": close_price, "close_time": close_time}

    return signal


def get_transaction_path():
    return Path(App.config["data_folder"]) / App.config["symbol"] / "transactions.txt"
