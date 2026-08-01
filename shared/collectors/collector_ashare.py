"""Download A-share (Shanghai / Shenzhen) daily OHLCV via akshare."""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime

import pandas as pd

# Leading digit → exchange (for validation / messaging only; akshare takes 6-digit codes)
_SH_PREFIXES = ("6", "9")
_SZ_PREFIXES = ("0", "3")

_STOCK_LIST_LOCK = threading.Lock()
_STOCK_LIST_DF: pd.DataFrame | None = None
_STOCK_LIST_LOADED_AT: float = 0.0
_STOCK_LIST_TTL_SEC = 24 * 3600


def _clean_stock_name(name: str) -> str:
    """Normalize display/search names (fullwidth / spaces)."""
    if name is None:
        return ""
    text = str(name)
    # fullwidth digits/letters → ascii
    trans = str.maketrans({
        **{chr(ord("０") + i): str(i) for i in range(10)},
        **{chr(ord("Ａ") + i): chr(ord("A") + i) for i in range(26)},
        **{chr(ord("ａ") + i): chr(ord("a") + i) for i in range(26)},
        "　": " ",
    })
    text = text.translate(trans)
    text = re.sub(r"\s+", "", text)
    return text.strip()


def normalize_ashare_symbol(raw: str) -> str:
    """
    Accept common user inputs and return a 6-digit A-share code.

    Examples: 600519, sh600519, 600519.SS, SZ000001 → 600519 / 000001
    """
    if raw is None:
        raise ValueError("Stock code is required")
    text = str(raw).strip().upper()
    # Prefer a contiguous 6-digit sequence
    match = re.search(r"(\d{6})", text)
    if not match:
        raise ValueError(f"Invalid A-share code: {raw!r} (expected 6 digits)")
    code = match.group(1)
    if code[0] not in _SH_PREFIXES + _SZ_PREFIXES:
        raise ValueError(
            f"Unsupported A-share code: {code} "
            "(Shanghai: 6/9xxxxx, Shenzhen: 0/3xxxxx)"
        )
    return code


def ashare_exchange(code: str) -> str:
    code = normalize_ashare_symbol(code)
    return "SH" if code[0] in _SH_PREFIXES else "SZ"


def get_ashare_stock_list(force_refresh: bool = False) -> pd.DataFrame:
    """
    Cached A-share code/name table from akshare (沪深，过滤北交所等).
    Columns: code, name, name_key, exchange
    """
    global _STOCK_LIST_DF, _STOCK_LIST_LOADED_AT

    now = time.time()
    with _STOCK_LIST_LOCK:
        if (
            not force_refresh
            and _STOCK_LIST_DF is not None
            and (now - _STOCK_LIST_LOADED_AT) < _STOCK_LIST_TTL_SEC
        ):
            return _STOCK_LIST_DF

        import akshare as ak

        raw = ak.stock_info_a_code_name()
        if raw is None or raw.empty:
            raise RuntimeError("Failed to load A-share stock list")

        df = raw.rename(columns={c: str(c).lower() for c in raw.columns}).copy()
        if "code" not in df.columns or "name" not in df.columns:
            raise RuntimeError(f"Unexpected stock list columns: {list(raw.columns)}")

        df["code"] = df["code"].astype(str).str.zfill(6)
        df = df[df["code"].str[0].isin(_SH_PREFIXES + _SZ_PREFIXES)].copy()
        df["name"] = df["name"].astype(str)
        df["name_key"] = df["name"].map(_clean_stock_name)
        df["exchange"] = df["code"].map(lambda c: "SH" if c[0] in _SH_PREFIXES else "SZ")
        df = df.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)

        _STOCK_LIST_DF = df
        _STOCK_LIST_LOADED_AT = now
        return df


def search_ashare_stocks(query: str, limit: int = 15) -> list[dict]:
    """
    Fuzzy suggest by code prefix or name substring.
    Returns list of {code, name, exchange, label}.
    """
    q = (query or "").strip()
    if not q:
        return []

    limit = max(1, min(int(limit), 50))
    df = get_ashare_stock_list()
    q_code = re.sub(r"\D", "", q)
    q_name = _clean_stock_name(q).lower()

    scored: list[tuple[int, dict]] = []
    for row in df.itertuples(index=False):
        code = row.code
        name = row.name
        name_key = (row.name_key or "").lower()
        exchange = row.exchange
        score = 0
        if q_code and code.startswith(q_code):
            score = 100 - (len(code) - len(q_code))  # longer prefix match ranks higher
            if code == q_code.zfill(6) or code == q_code:
                score = 200
        elif q_name and q_name in name_key:
            score = 80 if name_key.startswith(q_name) else 50
            if name_key == q_name:
                score = 150
        else:
            continue
        scored.append((
            score,
            {
                "code": code,
                "name": name,
                "exchange": exchange,
                "label": f"{code} {name}",
            },
        ))

    scored.sort(key=lambda x: (-x[0], x[1]["code"]))
    return [item for _, item in scored[:limit]]


def resolve_ashare_query(raw: str) -> str:
    """
    Resolve user input (code or name) to a single 6-digit code.

    - Exact / embedded 6-digit code wins when valid
    - Exact name match (ignoring spaces/fullwidth) if unique
    - Single search hit if unambiguous
    """
    if raw is None or not str(raw).strip():
        raise ValueError("请输入股票代码或名称")

    text = str(raw).strip()
    # Prefer explicit code if present
    try:
        return normalize_ashare_symbol(text)
    except ValueError:
        pass

    df = get_ashare_stock_list()
    key = _clean_stock_name(text).lower()
    exact = df[df["name_key"].str.lower() == key]
    if len(exact) == 1:
        return str(exact.iloc[0]["code"])
    if len(exact) > 1:
        opts = ", ".join(f"{r.code} {r.name}" for r in exact.itertuples(index=False))
        raise ValueError(f"名称「{text}」对应多只股票，请选择：{opts}")

    hits = search_ashare_stocks(text, limit=10)
    if len(hits) == 1:
        return hits[0]["code"]
    if not hits:
        raise ValueError(f"未找到股票：{text}（请输入代码如 600519，或名称如 贵州茅台）")
    opts = "；".join(h["label"] for h in hits[:5])
    raise ValueError(f"匹配到多只股票，请从提示中选择：{opts}")


def _prefixed_symbol(symbol: str) -> str:
    """akshare sina/tencent style: sh600519 / sz000001."""
    code = normalize_ashare_symbol(symbol)
    return ("sh" if code[0] in _SH_PREFIXES else "sz") + code


def _normalize_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        raise RuntimeError("Empty A-share dataframe")

    rename_cn = {
        "日期": "timestamp",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
    }
    if all(c in raw.columns for c in rename_cn):
        df = raw.rename(columns=rename_cn)
    else:
        lower_map = {c: str(c).lower() for c in raw.columns}
        df = raw.rename(columns=lower_map)
        if "date" in df.columns and "timestamp" not in df.columns:
            df = df.rename(columns={"date": "timestamp"})
        if "vol" in df.columns and "volume" not in df.columns:
            df = df.rename(columns={"vol": "volume"})

    keep = [c for c in ("timestamp", "open", "high", "low", "close", "volume") if c in df.columns]
    if "timestamp" not in keep or "close" not in keep:
        raise RuntimeError(f"Unexpected columns from provider: {list(raw.columns)}")

    df = df[keep].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce").dt.date
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["timestamp", "close"])
    df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
    return df.reset_index(drop=True)


def _with_retries(label: str, fn, retries: int = 3, delay: float = 1.5):
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            print(f"{label} attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(delay * attempt)
    assert last_err is not None
    raise last_err


def _fetch_via_sina(symbol: str, start_date: str | None, end_date: str) -> pd.DataFrame:
    import akshare as ak

    prefixed = _prefixed_symbol(symbol)
    kwargs: dict = {"symbol": prefixed, "adjust": "qfq"}
    if start_date:
        kwargs["start_date"] = start_date
        kwargs["end_date"] = end_date

    def _call():
        return ak.stock_zh_a_daily(**kwargs)

    raw = _with_retries(f"sina({prefixed})", _call)
    return _normalize_ohlcv(raw)


def _fetch_via_em_chunks(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """East Money full-history requests often drop; fetch year by year."""
    import akshare as ak

    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    frames: list[pd.DataFrame] = []
    year = start.year
    while year <= end.year:
        y_start = max(start, pd.Timestamp(f"{year}-01-01")).strftime("%Y%m%d")
        y_end = min(end, pd.Timestamp(f"{year}-12-31")).strftime("%Y%m%d")

        def _call(ys=y_start, ye=y_end):
            return ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=ys,
                end_date=ye,
                adjust="qfq",
            )

        try:
            raw = _with_retries(f"em({symbol},{y_start}-{y_end})", _call, retries=2)
            if raw is not None and not raw.empty:
                frames.append(_normalize_ohlcv(raw))
        except Exception as e:
            print(f"WARNING: skip EM chunk {y_start}-{y_end}: {e}")
        year += 1
        time.sleep(0.2)

    if not frames:
        raise RuntimeError(f"No A-share data from East Money for {symbol}")
    return (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset=["timestamp"], keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )


def _fetch_daily(symbol: str, start_date: str | None = None) -> pd.DataFrame:
    """
    Prefer Sina full history (stable). Fall back to East Money year chunks.
    """
    end_date = datetime.now().strftime("%Y%m%d")
    start = start_date or "19900101"

    try:
        print(f"Fetching via Sina ({_prefixed_symbol(symbol)}) {start}->{end_date} ...")
        return _fetch_via_sina(symbol, start_date=start, end_date=end_date)
    except Exception as e:
        print(f"Sina fetch failed ({e}); falling back to East Money chunks...")

    return _fetch_via_em_chunks(symbol, start, end_date)


def download_klines(config, data_sources):
    """
    Batch download daily klines for each data source.

    ``data_sources[].folder`` must be a 6-digit A-share code.
    Rows are stored in Postgres ``market_frames`` (kind=klines).
    """
    from shared.db.frames import load_frame, save_frame

    time_column = config["time_column"]
    download_max_rows = config.get("download_max_rows", 0)

    for ds in data_sources:
        quote = ds.get("folder")
        if not quote:
            print("ERROR. Folder is not specified.")
            continue

        try:
            symbol = normalize_ashare_symbol(quote)
        except ValueError as e:
            print(f"ERROR. {e}")
            continue

        print(f"Start downloading A-share '{symbol}' ({ashare_exchange(symbol)}) ...")

        df = load_frame(symbol, "klines", time_column=time_column)
        if not df.empty:
            df[time_column] = pd.to_datetime(df[time_column], errors="coerce", utc=True)
            last_date = df.iloc[-1][time_column]
            start = (pd.Timestamp(last_date) - pd.Timedelta(days=10)).strftime("%Y%m%d")
            new_df = _fetch_daily(symbol, start_date=start)
            if time_column != "timestamp":
                new_df = new_df.rename(columns={"timestamp": time_column})
            new_df[time_column] = pd.to_datetime(new_df[time_column], errors="coerce", utc=True)
            df = pd.concat([df, new_df])
            df = df.drop_duplicates(subset=[time_column], keep="last")
        else:
            print("No existing klines in DB. Full fetch...")
            df = _fetch_daily(symbol)
            if time_column != "timestamp":
                df = df.rename(columns={"timestamp": time_column})
            df[time_column] = pd.to_datetime(df[time_column], errors="coerce", utc=True)
            print("Full fetch finished.")

        df = df.sort_values(by=time_column)
        if download_max_rows:
            df = df.tail(download_max_rows)

        n = save_frame(symbol, "klines", df, time_column=time_column, replace=True)
        print(f"Finished downloading '{symbol}'. Stored {n} rows in Postgres (klines)")
