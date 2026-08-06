# Prefect + Kedro

Prefect owns **job queueing, schedules, concurrency, recovery, and cross-team routing**.  
Kedro owns the **step DAG and data catalog**.  
The product UI keeps Redis as an **SSE log bridge**; Prefect is the ops source of truth.

## Services

| Service | Role |
|---------|------|
| `prefect-server` | Self-hosted Prefect 3 UI/API (`http://localhost:4200`) |
| `prefect-worker` | Serves team Kedro deployments + `itb-daily-predict/default` |
| `pipeline` | FastAPI enqueue API; `POST /internal/jobs` → Prefect |
| `api` | Schedule sync, watchlist sync, RBAC, Prefect deep-links |
| Redis | Optional job mirror + SSE log lines for the Web UI |

## Deployments

| Deployment | Trigger | Behavior |
|------------|---------|----------|
| `itb-kedro-job/{team}` | UI / API / watchlist | Fine-grained: **one Prefect task per Kedro node** |
| `itb-daily-predict/default` | Cron | Calls `predict_symbols(note="scheduled")` |

Teams: `ITB_TEAMS=default,alpha,beta`.

## Fine-grained DAG

```text
Flow itb-kedro-job
  ├─ task kedro-download
  ├─ task kedro-node:merge
  ├─ …
  └─ task kedro-node:signals|output|…
```

Set `ITB_KEDRO_EXECUTION=coarse` to fall back to a single `session.run()`.

## Phase 4 — deep links, hybrid job source, RBAC

### Prefect deep links

Job responses include:

* `prefect_flow_run_id`
* `prefect_ui_url` → `{PREFECT_UI_URL}/runs/flow-run/{id}`

Exposed via:

* `GET /api/prefect/info`
* Pipeline / Models / Backtest / Dashboard UI links

### Job source (weaken Redis dependency)

| `ITB_JOB_SOURCE` | Behavior |
|------------------|----------|
| `hybrid` (default) | Prefer Redis recent list; enrich with Prefect URLs; if Redis empty → Prefect |
| `prefect` | List recent `tag:itb` flow runs from Prefect only |
| `redis` | Classic Redis-only list |

`ITB_REDIS_JOB_MIRROR=0` disables reading Redis for job lists (SSE log bridge may still write). Keep `=1` for product SSE.

### Team RBAC (BFF edge)

Disabled by default (`ITB_RBAC_ENABLED=0`).

When enabled, mutating job endpoints require:

| Header | Meaning |
|--------|---------|
| `X-ITB-User` | Caller identity |
| `X-ITB-Teams` | Comma-separated teams allowed for this caller |
| `X-ITB-Admin: 1` | Optional bypass |

Protected: `POST /api/pipeline/jobs`, `POST /api/watchlist/*/train`, `POST /api/watchlist/predict`.

Frontend can set `VITE_ITB_USER` / `VITE_ITB_TEAMS` / `VITE_ITB_ADMIN`.

## Tags

| Tag | Example |
|-----|---------|
| `team:{name}` | `team:default` |
| `env:{name}` | `env:dev` |
| `kind:{train\|predict\|backtest}` | `kind:train` |
| `symbol:{code}` | `symbol:600519` |
| `job:{uuid}` | Redis/UI correlation |

## Concurrency

| Limit | Default | Meaning |
|-------|---------|---------|
| `itb-train` | `ITB_TRAIN_CONCURRENCY=1` | Max concurrent train jobs |
| `itb-predict` | `ITB_PREDICT_CONCURRENCY=10` | Max concurrent predict jobs |
| `itb-symbol:{code}` | `1` | Same symbol never overlaps |

## Config cheat-sheet

| Env | Default | Meaning |
|-----|---------|---------|
| `PREFECT_API_URL` | `http://prefect-server:4200/api` | Prefect API |
| `PREFECT_UI_URL` | `http://localhost:4200` | Browser-facing Prefect UI |
| Prefect package | `prefect==3.6.4` | Must match compose `prefect-server` image (mirror `:3-latest`) |
| `ITB_JOB_SOURCE` | `hybrid` | Job list source |
| `ITB_REDIS_JOB_MIRROR` | `1` | Use Redis for job list/SSE mirror |
| `ITB_RBAC_ENABLED` | `0` | BFF team RBAC |
| `ITB_KEDRO_EXECUTION` | `fine` | `fine` / `coarse` |
| `ITB_TEAMS` | `default` | Deployments to serve |
| `ITB_TEAM` | `default` | Default enqueue team |
