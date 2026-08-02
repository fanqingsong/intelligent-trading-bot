# 更新全部模型（断点续作）

Models 页的 **更新全部模型** 会按关注列表顺序，对每只股票串行跑完整训练流水线（`TRAIN_UPDATE_STEPS`）。  
整批进度落在 Postgres，API 宕机重启后可从第一只未完成的股票继续。

单只「更新模型」仍走 `POST /api/watchlist/{symbol}/train`，不经过本批处理逻辑。

## 入口

| 层 | 位置 |
|----|------|
| UI | Models → 运维操作 → **更新全部模型** |
| API | `POST /api/watchlist/train` |
| 进度查询 | `GET /api/watchlist/train/active` |
| 编排 | `backend/watchlist_service.py` → `train_symbols` / `_process_train_batch` |

请求体（可选）：

```json
{ "symbols": ["600519", "000001"], "team": "default" }
```

- `symbols` 省略：训练整份 watchlist（按 `created_at` 升序）
- 已有未完成的 train batch 时：不新建，返回现有进度并确保处理器在跑（`resumed` / `deduped`）

## 执行流程

```text
POST /api/watchlist/train
  │
  ├─ 已有 open BatchRun(kind=train, status∈queued|running)
  │     → 拉起处理器 → 返回进度（不新建）
  │
  └─ 否则
        1. 写入 batch_runs + 全部 symbol_run_links(status=queued)
        2. 同步 watchlist_items.train_status=queued
        3. asyncio 后台任务 _process_train_batch
              按 link.id 顺序：
                queued 且无 job_id → enqueue TRAIN_UPDATE_STEPS
                                  → link/job = running
                轮询 pipeline job 至 completed|failed
                → 下一只
              全部终态 → batch_runs.status = completed|failed
```

单只训练步骤与单股「更新模型」相同：`download → merge → features → labels → train → predict → signals`。

## 状态落库（断点）

**以 Postgres 为准**；内存里的 `asyncio.Task` 只负责推进，进程退出后可重建。

| 表 | 作用 |
|----|------|
| `batch_runs` | 整批：`kind=train`，`status`，`finished_at`，`note` |
| `symbol_run_links` | **每只股票的检查点**：`status`、`job_id`、`error` |
| `watchlist_items` | UI 展示：`train_status`、`last_train_job_id`、`last_error` |

ORM：`backend/db/models.py`（`BatchRun` / `SymbolRunLink` / `WatchlistItem`）。

### `symbol_run_links.status`

| 状态 | 含义 |
|------|------|
| `queued` | 已计划，尚未（或重新）入队 |
| `running` | 已拿到 `job_id`，pipeline/Prefect 执行中 |
| `completed` | 该股训练成功 |
| `failed` | 入队失败或 job 失败 |
| `skipped` | 预留（train-all 当前不跳过） |

### `batch_runs.status`

| 状态 | 含义 |
|------|------|
| `queued` / `running` | 未结束（可续跑） |
| `completed` | 至少有成功，或全部为 completed/skipped |
| `failed` | 全部失败 |

全部 link 进入终态（`completed` / `failed` / `skipped`）后由 `_maybe_finish_batch` 收束整批。

## 宕机续作

1. **API 启动**：`on_startup` → `resume_open_train_batches()`
2. **状态同步循环**：`refresh_running_statuses()` 末尾也会调用，防止处理器意外退出后无人拉起
3. 处理器每次从该 batch 中 **第一条** `status ∈ {queued, running}` 的 link 继续

特殊情况：

| 场景 | 行为 |
|------|------|
| 有 `job_id`，pipeline 仍可查到 job | 继续轮询直到终态 |
| 有 `job_id`，job 记录丢失（404） | 清空 `job_id`，该股改回 `queued` 并重新入队 |
| 处理器异常 | batch 保持 open，`note` 追加错误；后续 resume 再拉起 |

同一时间只允许一个未完成的 train batch；重复点击会返回现有进度而不是开第二批。

## UI 行为

- 批量进行中：按钮文案变为「批量更新进行中…」并禁用
- 进度文案：`批量训练 #id：已完成 N/M · 当前 symbol`
- 关注列表行上的 `train_status` / `JobProgress` 与单股训练相同（轮询 `GET /api/watchlist`）

## 相关 API / 代码

| 符号 | 文件 |
|------|------|
| `train_symbols` | `backend/watchlist_service.py` |
| `_process_train_batch` | 同上 |
| `resume_open_train_batches` | 同上 |
| `sync_job_status` | 同上（同步 watchlist + link） |
| `POST /api/watchlist/train` | `backend/main.py` |
| `GET /api/watchlist/train/active` | `backend/main.py` |
| `api.watchlistTrainAll` | `frontend/src/api.ts` |
| Models 页按钮 | `frontend/src/pages/Models.tsx` |

环境变量（可选）：

| 变量 | 默认 | 含义 |
|------|------|------|
| `ITB_TRAIN_BATCH_POLL_S` | `5` | 串行处理器轮询单股 job 的间隔（秒） |

单股 job 并发仍受 Prefect `itb-train` / `itb-symbol:*` 限制；本批逻辑本身保证「一次只推进一只股票」。

## 与一键预测的差异

| | 更新全部模型 | 一键预测 |
|--|-------------|---------|
| Batch `kind` | `train` | `predict` |
| 计划落库时机 | **启动前**写满全部 links | 入队时才建 link |
| 推进方式 | API 内后台串行（等一只完再下一只） | 启动时尽量全部 enqueue |
| 重启续作 | 有（open batch + processor） | 依赖已入队 job + 状态同步 |
| 跳过未训练 | 否 | 是（`skipped` / untrained） |
