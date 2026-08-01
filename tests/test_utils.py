import numpy as np
import numpy.testing as npt
import pandas as pd

from kedro_pipeline.signals.gen_signals import generate_signals
from kedro_pipeline.common.utils import add_area_ratio, add_linear_trends


def test_signal_generation():
	data = [
		(222, 1, 2),
		(333, 2, 1),
		(444, 0, 1),
	]
	df = pd.DataFrame(data, columns=["aaa", "high_60_20", "low_60_04"])

	models = {
		"buy": {"high_60_20": 1, "low_60_04": 1},
		"sell": {"high_60_20": 1, "low_60_04": 1},
	}

	generate_signals(df, models)

	assert "buy" in df.columns.to_list()
	assert "sell" in df.columns.to_list()
	assert [1, 1, 0] == list(df["buy"])
	assert [0, 0, 1] == list(df["sell"])


def test_area_ratio():
	price = [10, 20, 30, 20, 10, 20, 30]
	df = pd.DataFrame(data={"price": price})

	add_area_ratio(df, is_future=False, column_name="price", windows=4)
	assert df[df.columns[1]].iloc[-1] == -1
	assert df[df.columns[1]].iloc[-2] == 0

	add_area_ratio(df, is_future=True, column_name="price", windows=4)
	assert df[df.columns[1]].iloc[0] == 1
	assert df[df.columns[1]].iloc[1] == 0


def test_linear_trends():
	price = [10, 20, 40, 40, 30, 10]
	df = pd.DataFrame(data={"price": price})

	add_linear_trends(df, is_future=False, column_name="price", windows=2)
	npt.assert_almost_equal(df["price_trend_2"].values, np.array([0, 10, 20, 0, -10, -20]))

	add_linear_trends(df, is_future=True, column_name="price", windows=2)
	npt.assert_almost_equal(df["price_trend_2"].values, np.array([10, 20, 0, -10, -20, np.nan]))

	add_linear_trends(df, is_future=False, column_name="price", windows=6)
	npt.assert_almost_equal(df["price_trend_6"].values, np.array([0, 10, 15, 11, 6, 0.857143]))
