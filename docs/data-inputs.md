# Data sources and data collectors

## Defining data sources

The intelligent trading bot operates in **batch (offline) mode** for analyzing historical A-share data and generating signals.

Data sources are specified in the `data_sources` section. Each entry describes a single source used to retrieve data.

```jsonc
"data_sources": [
  {...}, // First data source
  {...}  // Optional additional sources
]
```

A data source description includes the following attributes:

```jsonc
{
  "folder": "600519", // Quote / folder name
  "file": "klines", // Filename for the source data
  "column_prefix": "" // Prefix added to all columns from this data source
}
```

The attributes of a data source are interpreted as follows:

-   `folder`: Local folder name and the quote code used to request data from the provider.
-   `file`: The name of the file containing the retrieved data (e.g. `klines`). If not specified, it defaults to the symbol name in `folder`.
-   `column_prefix`: When retrieving multiple symbols, column names often overlap. This prefix is applied to every column name from this source during merge.

When retrieving data, you must specify the frequency in the `freq` attribute. Values follow the `pandas` offset alias convention. For A-shares use `"1D"`.

The data provider is specified in the `venue` attribute. Currently supported:

-   `ashare`: China A-shares (Shanghai / Shenzhen daily bars via [akshare](https://github.com/akfamily/akshare))

### A-share (`ashare`) notes

-   **Market:** Shanghai / Shenzhen only (codes starting with `6`/`9` → SH, `0`/`3` → SZ). Beijing Stock Exchange is not included.
-   **Symbols:** Store 6-digit codes in `symbol` and `data_sources[].folder` (e.g. `600519`, `000001`).
-   **Name resolution:** Helpers in `shared/collectors/collector_ashare.py` load a cached code/name table (`ak.stock_info_a_code_name`, 24h TTL; memory → Postgres `ashare_stocks` → network; API startup warms the cache):
    -   `search_ashare_stocks(query)` — typeahead by code prefix or name substring
    -   `resolve_ashare_query(query)` — resolve a code or unique Chinese name to one code
    -   Web API: `GET /api/watchlist/suggest`, `POST /api/watchlist` (accepts code or name)
-   **Download:** Batch only (`download_klines`). Prefers Sina daily history (`stock_zh_a_daily`); falls back to East Money year-by-year chunks if needed. Rows are stored in Postgres `market_frames` (`kind=klines`).
-   **Frequency:** Use `"1D"`. Set `"merge_trading_days_only": true` so weekends/holidays introduced by a calendar `date_range` are dropped after merge.
-   **Sample config:** `configs/config-ashare-1d.jsonc` (local `trader_simulation` output).
-   **UI:** Watchlist home page — add symbols, **更新模型**, **一键预测**.
-   **Guide:** [ashare.md](ashare.md)

## Downloader

The `download` step retrieves data from the configured data sources and upserts into Postgres. If klines already exist for the symbol, only the latest window is fetched and appended; existing timestamps are overwritten on overlap. If none exist, the maximum available history is retrieved. The maximum stored size is controlled by the `download_max_rows` attribute.

Run **download** from the Web UI **Pipeline**, or via Watchlist train/predict jobs.

## Merging data sources

Downloaded data from different sources is merged into a single table. The merge procedure:

-   Generates a continuous time raster based on the configured frequency
-   Appends all source columns by aligning rows with the generated raster

For daily A-share data (`freq: "1D"`), the calendar raster also includes weekends and public holidays. Set `"merge_trading_days_only": true` so rows with an empty primary `close` are dropped after the join (see `merge_data_sources` in `kedro_pipeline/common/utils.py`). The A-share sample config enables this by default.

Run **merge** from the Web UI **Pipeline**. The result is saved as a single file; the output filename is specified in `merge_file_name` (e.g. `"merge_file_name": "data.parquet"`).

## Implementing a custom data collector

To implement a new custom data collector:

-   Add a new entry to the `Venue` enumerator in `shared/types.py`
-   Implement `download_klines` for batch download
-   Return it from `get_download_functions` in `shared/collectors/__init__.py`
