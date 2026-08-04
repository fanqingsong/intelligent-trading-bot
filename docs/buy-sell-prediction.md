# 买卖点预测设计

说明默认 A 股日线流水线如何把特征变成 BUY / SELL / HOLD。
模板：[`configs/config-ashare-1d.jsonc`](../configs/config-ashare-1d.jsonc)。

这**不是**端到端输出买卖点的单一网络，也不是序列模型（LSTM）或强化学习智能体。
本质是对「未来涨跌机会」做**逐点二分类**，再经概率差分、阈值过滤与多数投票得到信号。

```
K 线 → 12 维 TA 特征 → 各算法的 P(high_30)、P(low_30)
                     → trade_score = P(涨) − P(跌)
                     → 阈值 → 各算法买卖信号
                     → 多数投票 → BUY / SELL / HOLD
```

不下实盘；输出仅供本地 `trader_simulation`。另见 [ashare.md](ashare.md)、[labels.md](labels.md)、[trainable-features.md](trainable-features.md)。

---

## 1. 标签 — 模型学什么

默认生成器：`highlow2`（相对极值的 `topbot2` 见 [labels.md](labels.md)，A 股模板未启用）。

| 标签 | 含义 |
|------|------|
| `high_30` | 未来 `horizon` 根 K 线内，**上涨**目标在不利方向（下跌容差）**之前**先被触及 |
| `low_30` | 对称：先触及**下跌**目标，且此前未突破上涨容差 |

A 股日线配置：

```jsonc
"label_sets": [
  {"generator": "highlow2", "config": {
    "columns": ["close", "high", "low"],
    "function": "high",
    "thresholds": [3.0], "tolerance": 0.2, "horizon": 5,
    "names": ["high_30"]
  }},
  {"generator": "highlow2", "config": {
    "columns": ["close", "high", "low"],
    "function": "low",
    "thresholds": [3.0], "tolerance": 0.2, "horizon": 5,
    "names": ["low_30"]
  }}
]
```

### 参数含义

| 参数 | 取值 | 含义 |
|------|------|------|
| `horizon` | `5` | 只看未来 5 根 K（`freq` 为 `1D` 时即约 5 个交易日） |
| `thresholds` | `[3.0]` | 相对当日 `close` 的目标涨跌幅 **3%** |
| `tolerance` | `0.2` | 允许的不利波动 = `0.2 × 3%` = **0.6%**（须在目标之前不突破） |
| `columns` | `close`, `high`, `low` | 参考价；探测上涨；探测下跌 |
| `label_horizon` | `5` | 训练时丢掉末尾 5 行，避免用未完成未来窗口的标签 |

语义类似止盈 / 止损：**谁先触及决定真假**。若不利方向先突破，即便之后才碰到目标，标签仍为 **false**。

### 数值示例（`high_30`）

当日 `close = 100`：

| 水平 | 公式 | 价格 |
|------|------|------|
| 止盈（上涨） | `100 × (1 + 3/100)` | **103** — 用未来 `high` 探测 |
| 止损 / 噪声（下跌） | `100 × (1 − 0.6/100)` | **99.4** — 用未来 `low` 探测 |
| 窗口 | K 线 `t+1 … t+5` | — |

**True**：目标先到：

```
第1天: high=101, low=99.8   → 两边都未触及
第2天: high=102, low=99.5   → 两边都未触及
第3天: high=103.2, low=100  → high 先碰到 103 → high_30 = True
```

**False**：不利方向先到（即使后面继续大涨也不改）：

```
第1天: high=101, low=99.2   → low 先破 99.4 → high_30 = False
（第4天 high=105 不能挽救该标签）
```

`low_30` 对称：目标价 `97`，不利价 `100.6`，先用 `low`、再用 `high`。

实现：`generate_labels_highlow2` → `first_cross_labels` → `first_location_of_crossing_threshold`
（`kedro_pipeline/labels/gen_labels_highlow.py`、`kedro_pipeline/common/utils.py`）。

### 分类器在估计什么

- `P(high_30)` — 未来 5 根 K 内「先出现约 3% 干净上涨机会」的概率  
- `P(low_30)` — 对称的下跌机会概率  

它们**不**预测完整价格路径，只评估这类「先触达」事件是否可能发生。

---

## 2. 特征 — 模型输入

默认使用 **相对 / 无量纲** TA-Lib 特征（避免绝对价格水平的 SMA/STDDEV），共 12 列：

| 类别 | 列名 | 说明 |
|------|------|------|
| 动量 | `close_RSI_6/14/24` | 0–100，与股价无关 |
| 收益率 | `close_ROC_5/10/20` | 百分比变化率 |
| 趋势震荡 | `close_PPO` | 百分比价格震荡（类 MACD，相对值） |
| 波动 | `high_low_close_NATR_14` | 归一化 ATR（%） |
| 趋势强度 | `high_low_close_ADX_14` | 0–100 |
| 均线结构 | `close_SMA_1/5/20` | `(短窗−长窗)/长窗` 百分比差；`SMA_60` 仅作基准，不入模 |

列在配置的 `train_features` 中；由 `feature_sets` 的 `talib` 生成器产出。
`features_horizon: 90` 覆盖最长回看与 ADX 不稳定期。

---

## 3. 算法 — 概率如何得到

每个 **标签 × 算法** 训练一个二分类器，输出正类概率 ∈ `[0, 1]`。

各算法默认训练长度：最近 **750** 行（`params.length`）。

| 名称 | 实现 | 模板默认要点 |
|------|------|----------------|
| `svc` | `sklearn.svm.SVC` | `probability=True`，`C=1.0`，特征缩放 |
| `gb` | LightGBM | `objective=binary`，`num_iterations=80`，不缩放 |
| `nn` | Keras `Sequential` MLP | `Dense(32, sigmoid)` → `Dense(1, sigmoid)`；Adam `lr=0.001`，约 8 epoch，batch 64 |
| `lc` | `LogisticRegression` | `C=1.0`，`max_iter=500`，特征缩放 |

预测列：`high_30_{algo}`、`low_30_{algo}`（共 8 列）。

模型存 MLflow，名 `itb_{symbol}_{label}_{algo}`（别名 Production）。
训练可并行（`train_parallel`，默认 `max_workers=4`）。

分类器：`kedro_pipeline/classifiers/classifier_{svc,gb,nn,lc}.py`。
编排：`kedro_pipeline/orchestration/generators.py`，节点在 `kedro_pipeline/nodes/inference.py`。

---

## 4. 信号 — 从概率到 BUY / SELL / HOLD

由 `signal_sets` 配置：

1. **合并（差分）**  
   `trade_score_{algo} = high_30_{algo} − low_30_{algo}`  
   大致在 `[-1, +1]`：正偏向上涨机会，负偏向下跌机会。

2. **阈值规则**（默认 ±0.08）  
   - `trade_score > +0.08` → `buy_signal_{algo}`  
   - `trade_score < −0.08` → `sell_signal_{algo}`

3. **多数投票**（`min_votes: 2`）  
   `{svc, gb, nn, lc}` 中至少两票同向：  
   `buy_signal_vote` / `sell_signal_vote` / `vote_label` ∈ `{BUY, SELL, HOLD}`。

4. **输出**  
   `trader_simulation` 消费投票列，仅写本地模拟成交。

离线搜阈值见 `simulate_model`；滚动预测见 `rolling_predict`。

信号生成：`kedro_pipeline/signals/gen_signals.py`。

---

## 5. 标签 vs 预测 — 如何指导下一交易日

常见疑问：*标签依赖未来 K 线，那昨天的买卖标记怎么指导今天的交易？*

**标签与信号是不同列，时间语义不同。**

| | 标签（`high_30` / `low_30`） | 预测 / 信号（`vote_label` 等） |
|--|------------------------------|--------------------------------|
| 何时可算 | 必须等未来 `horizon` 根 K 走完 | 该根 K 的特征齐备即可（如当日收盘后） |
| 输入 | 未来的 `high` / `low` | 仅历史与当前特征（12 维 TA） |
| 用途 | **训练** / 评估的标准答案 | 对后续交易日的**前瞻**参考 |
| 含义 | 「事后看：谁先触达？」 | 「往前看约 5 根 K：哪种机会更可能？」 |

训练会丢掉末尾 `label_horizon` 行，因为那些行的**标签**不完整。
**预测不会丢掉这些行**——最新一根 K 仍会打分并出投票信号；自选股 / API 展示的正是这一行。

### 典型 A 股日线节奏

1. **T 日收盘** → T 日特征齐全。  
2. 跑 `daily_predict`（或盘后 cron）→ 在 **T 日这一行** 写入概率与 `vote_label`。  
3. **T+1（今天）** — 依据挂在 T 上的信号操作：含义是模型对大约 **未来五个交易日** 偏多/偏空，**不是**「事后已证明 T 日是买点」。

```
时间轴:   …  T-2   T-1    T（昨天）      T+1（今天）  …  T+5
特征:     已知   已知   已知             ← 预测用 T 的特征
标签:     可知   可知   要等 T+1…T+5 走完才知真假
信号:     ……    ……   ★ BUY / SELL?    ← 收盘后算出；指导 T+1 起
```

### 如何看盘

- **最新行 BUY**：模型认为约 5 根 K 内更可能先出现约 3% 的干净上涨 → 当作**前瞻偏向**，不保证低点已过。  
- **图上历史买卖标记**：多为**当时那根 K 上的预测**（推理不偷看未来）。事后确认的标签是另一套，不是下单依据。  
- 等五天「确认」标签再进场永远慢半拍；交易跟的是**预报**，不是回顾标签。  
- 信号锚定在**已收盘**的 K 上；未完成的盘中日线不是正式日线特征，盘中仍依赖上一交易日收盘后的预测。

**一句话：** 标签向后看（教模型）；信号向前看（指导下几日）。今天用的是昨天收盘时对未来窗口的预测，不是昨天的事后标签。

UI/API：最新行见 `GET /api/watchlist/{symbol}/signals`（`backend/watchlist_service.symbol_signals` 读取 signals 表尾部）。

---

## 6. 流水线位置

| 预设 | 步骤 |
|------|------|
| `train_update` | download → merge → features → labels → **train** → predict → signals → output |
| `daily_predict` | 同上但**跳过 train**（使用 Production 模型） |

预测跑仍会算标签，便于分析与落库；推理本身不依赖标签。

---

## 7. 关键文件

| 路径 | 职责 |
|------|------|
| `configs/config-ashare-1d.jsonc` | 特征 / 标签 / 算法 / 信号模板 |
| `kedro_pipeline/labels/gen_labels_highlow.py` | `highlow2` / 先触达标签 |
| `kedro_pipeline/labels/gen_labels_topbot.py` | 备选 `topbot2` 极值标签 |
| `kedro_pipeline/features/gen_features.py` | TA-Lib 等特征生成 |
| `kedro_pipeline/classifiers/` | 各算法训练 / 预测 |
| `kedro_pipeline/classifiers/model_store.py` | MLflow + `PairPythonModel` |
| `kedro_pipeline/signals/gen_signals.py` | 合并、阈值、多数投票 |
| `kedro_pipeline/backtesting/trades.py` | `trader_simulation` |
| `kedro_pipeline/nodes/inference.py` | Kedro 流水线节点 |
| `docs/labels.md` | 标签生成器完整说明 |
| `docs/ashare.md` | 自选股 / 任务 / API 流程 |

---

## 小结

多标签逐点二分类：用多尺度均线 / 斜率 / 波动特征估计未来 **5** 根 K 内是否出现「先触达 **3%**、不利方向仅容 **0.6%**」的机会；各算法用 `P(涨) − P(跌)` 做交易分，再经阈值与 **四选二** 投票得到买卖点。

交易跟**最新预测信号**（前瞻）；不要等**标签**兑现——那要等完整窗口走完，只用于训练与评估，不是入场时机。
