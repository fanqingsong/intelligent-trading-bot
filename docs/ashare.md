# A-share watchlist workflow

China A-share path: **watchlist → manual model update → daily / scheduled prediction**, with four algorithms and majority vote.

Default template: [`configs/config-ashare-1d.jsonc`](../configs/config-ashare-1d.jsonc).
Tabular data lives in Postgres `market_frames`; models in MLflow (`itb_{symbol}_*`).

## Scope

| Item | Value |
|------|--------|
| Market | Shanghai / Shenzhen only (`6`/`9` → SH, `0`/`3` → SZ) |
| Frequency | Daily (`freq: "1D"`) |
| Data | akshare → Postgres `klines` / `data` / `features` / `matrix` / `predictions` / `signals` |
| Algorithms | `svc`, `gb`, `nn`, `lc` (all trained & predicted) |
| Trading | Not enabled — local signals + `trader_simulation` only |
| UI | Watchlist home page |

## Quick start

```bash
bin/start_dev.sh
```

1. Open http://localhost:5174 (Watchlist)
2. Add `600519` or `贵州茅台`
3. Click **更新模型** (first-time train; not automatic on add)
4. Click **一键预测** for inference-only refresh, or enable post-market cron

Migrate old CSV directories (optional):

```bash
DATABASE_URL=postgresql+psycopg://itb:itb@localhost:5432/itb \
  python3 scripts/migrate_csv_to_postgres.py --seed-watchlist
```

## Job presets

```mermaid
flowchart TD
  WL[Watchlist] --> Train[更新模型 train_update]
  WL --> Pred[一键预测 daily_predict]
  Sched[APScheduler cron] --> Pred
  Train --> DL1[download merge features labels]
  DL1 --> TR[train all algos]
  TR --> PR1[predict signals]
  Pred --> DL2[download merge features labels]
  DL2 --> PR2[predict signals skip train]
  PR1 --> Vote[per-algo + majority vote]
  PR2 --> Vote
```

| Kind | Steps | Trigger |
|------|--------|---------|
| `train_update` | download → merge → features → labels → train → predict → signals | Per-symbol「更新模型」 |
| `daily_predict` | download → merge → features → labels → predict → signals | 「一键预测」/ schedule |

Untrained symbols are **skipped** on predict with an error note.

## Signals

For each algorithm `algo ∈ {svc,gb,nn,lc}`:

* Scores: `high_30_{algo}`, `low_30_{algo}`
* Combined: `trade_score_{algo} = high − low`
* Threshold (default ±0.08): `buy_signal_{algo}` / `sell_signal_{algo}`

Majority vote (`min_votes=2`):

* `buy_signal_vote` / `sell_signal_vote` / `vote_label` ∈ {BUY, SELL, HOLD}

## Storage

| Store | Content |
|-------|---------|
| Postgres `watchlist_items` | Symbols, train/predict status |
| Postgres `schedule_settings` | Post-market cron |
| Postgres `market_frames` | All pipeline tables (JSONB payload) |
| MLflow | Models named `itb_{symbol}_{label}_{algo}` |
| Redis | Job progress / logs |

## API (selected)

| Method | Path | Purpose |
|--------|------|---------|
| GET/POST/DELETE | `/api/watchlist` | List / add / delete |
| POST | `/api/watchlist/{symbol}/train` | Train update |
| POST | `/api/watchlist/predict` | Batch daily predict |
| GET | `/api/watchlist/{symbol}/signals` | Latest multi-algo summary |
| GET/PUT | `/api/schedule` | Enable cron / edit schedule |
| GET | `/api/data/files?symbol=` | List Postgres frames |
| GET | `/api/data/preview?file=signals&symbol=` | Preview frame |

Pipeline jobs accept `config_overrides` so multi-symbol runs do **not** rewrite the global JSONC.
