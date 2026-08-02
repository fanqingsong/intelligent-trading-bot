"""Unit tests for A-share stock list memory/db cache."""
from __future__ import annotations

import time

import pandas as pd
import pytest

from shared.collectors import collector_ashare as ashare


@pytest.fixture(autouse=True)
def _reset_stock_list_cache(monkeypatch: pytest.MonkeyPatch):
    store: dict = {"df": None, "at": 0.0}

    def fake_read():
        if store["df"] is None:
            return None, 0.0
        return store["df"].copy(), store["at"]

    def fake_write(df: pd.DataFrame) -> None:
        store["df"] = df.copy()
        store["at"] = time.time()

    monkeypatch.setattr(ashare, "_read_db_cache", fake_read)
    monkeypatch.setattr(ashare, "_write_db_cache", fake_write)
    ashare._STOCK_LIST_DF = None
    ashare._STOCK_LIST_LOADED_AT = 0.0
    ashare._REFRESH_THREAD = None
    yield store
    ashare._STOCK_LIST_DF = None
    ashare._STOCK_LIST_LOADED_AT = 0.0
    ashare._REFRESH_THREAD = None


def _sample_raw() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": ["600519", "000001", "830001"],  # 830001 BJ → filtered
            "name": ["贵州茅台", "平安银行", "北交所样例"],
        }
    )


def test_db_cache_avoids_network_on_second_process(
    monkeypatch: pytest.MonkeyPatch,
    _reset_stock_list_cache: dict,
):
    calls = {"n": 0}

    def fake_fetch() -> pd.DataFrame:
        calls["n"] += 1
        return ashare._normalize_stock_list(_sample_raw())

    monkeypatch.setattr(ashare, "_fetch_stock_list_from_akshare", fake_fetch)

    df1 = ashare.get_ashare_stock_list()
    assert calls["n"] == 1
    assert list(df1["code"]) == ["600519", "000001"]
    assert _reset_stock_list_cache["df"] is not None

    # Simulate process restart: clear memory, keep db.
    ashare._STOCK_LIST_DF = None
    ashare._STOCK_LIST_LOADED_AT = 0.0

    df2 = ashare.get_ashare_stock_list()
    assert calls["n"] == 1  # served from db
    assert list(df2["code"]) == ["600519", "000001"]


def test_stale_cache_returns_immediately_and_refreshes(
    monkeypatch: pytest.MonkeyPatch,
    _reset_stock_list_cache: dict,
):
    calls = {"n": 0}

    def fake_fetch() -> pd.DataFrame:
        calls["n"] += 1
        if calls["n"] == 1:
            return ashare._normalize_stock_list(_sample_raw())
        return ashare._normalize_stock_list(
            pd.DataFrame({"code": ["600519"], "name": ["贵州茅台"]})
        )

    monkeypatch.setattr(ashare, "_fetch_stock_list_from_akshare", fake_fetch)

    ashare.get_ashare_stock_list()
    assert calls["n"] == 1

    # Expire memory + db TTL
    past = time.time() - ashare._STOCK_LIST_TTL_SEC - 10
    ashare._STOCK_LIST_DF = None
    ashare._STOCK_LIST_LOADED_AT = 0.0
    _reset_stock_list_cache["at"] = past

    t0 = time.perf_counter()
    df = ashare.get_ashare_stock_list()
    elapsed = time.perf_counter() - t0

    assert list(df["code"]) == ["600519", "000001"]
    assert elapsed < 0.5  # must not block on network

    deadline = time.time() + 2
    while time.time() < deadline and calls["n"] < 2:
        time.sleep(0.05)
    assert calls["n"] >= 2


def test_search_uses_cached_list(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        ashare,
        "_fetch_stock_list_from_akshare",
        lambda: ashare._normalize_stock_list(_sample_raw()),
    )
    hits = ashare.search_ashare_stocks("茅台", limit=5)
    assert hits and hits[0]["code"] == "600519"
