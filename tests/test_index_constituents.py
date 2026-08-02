"""Unit tests for index constituent fetch / preset resolve."""
from __future__ import annotations

import pandas as pd
import pytest

from shared.collectors import collector_ashare as ashare


def test_resolve_index_preset():
    assert ashare.resolve_index_preset("sse50")["code"] == "000016"
    assert ashare.resolve_index_preset("CSI300")["name"] == "沪深300"
    assert ashare.resolve_index_preset("000016")["name"] == "上证50"
    with pytest.raises(ValueError):
        ashare.resolve_index_preset("csi500")


def test_fetch_index_constituents_normalizes(monkeypatch: pytest.MonkeyPatch):
    raw = pd.DataFrame(
        {
            "成分券代码": ["600519", "000001", "830001"],
            "成分券名称": ["贵州茅台", "平安银行", "北交所样例"],
        }
    )

    monkeypatch.setattr(
        ashare,
        "_with_retries",
        lambda _label, _fn, **_kw: raw,
    )

    items = ashare.fetch_index_constituents("csi300")
    assert [i["code"] for i in items] == ["600519", "000001"]
    assert items[0]["name"] == "贵州茅台"
    assert items[0]["exchange"] == "SH"
    assert items[1]["exchange"] == "SZ"
