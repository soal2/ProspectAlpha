---
name: skill4-valuation-checker
description: 将IPO发行人的估值倍数（P/E、P/S、EV/EBITDA、P/B）与14只港股同行按行业对标比较，输出多维加权评分和明确操作建议。当需要判断新股定价是否合理时使用。
---

# 估值检查器（Valuation Checker）

将 IPO 发行人的估值倍数与港股已上市同行进行对标比较。支持 **4 种估值倍数**（P/E、P/S、EV/EBITDA、P/B），覆盖 **5 个行业** 共 14 只对标股票，输出**多维加权评分**和**明确操作建议**。

## 设计理念

传统估值检查只看 P/E 一个指标，但不同行业的估值逻辑完全不同——地产看 P/B、SaaS 看 P/S、金融看 P/E。本 skill 的设计：

| 层次 | 能力 | 说明 |
|---|---|---|
| **数据层** | 14 只按行业组织的同行库 + 行业中位数 | 解决"和谁比"的问题 |
| **计算层** | P/E + P/S + EV/EBITDA + P/B 四维计算 | 解决"怎么比"的问题 |
| **评分层** | 加权评分（P/E 50% + P/S 25% + EV/EBITDA 25%），缺失权重按比例重分配 | 解决"综合来看"的问题 |
| **结论层** | 结论式输出 + 操作建议 + 同行估值区间 | 解决"到底贵不贵"的问题 |

## 触发场景

- 用户询问某只新股的估值是否偏贵、是否值得申购
- 需要查阅同行业已上市公司的估值倍数作为参照（含分布区间）
- 需要对发行人做多维估值对标（不只 P/E，含 P/S、EV/EBITDA）
- 需要获取明确的操作建议（建议申购 / 可选择性参与 / 谨慎参与 / 建议回避）
- 作为 orchestrator 管线中的估值维度输出加权分数

## 快速开始

```bash
# 1. 结论式对标（推荐）—— 使用行业中位数自动选择同行
python "<skill-dir>/scripts/checker.py" --check --mc 50000 --rev 8000 --ni 1200 --ebitda 2500 --industry saas

# 2. 结论式对标（指定同行 ticker）
python "<skill-dir>/scripts/checker.py" 0700.HK --check --mc 50000 --rev 8000 --ni 1200 --ebitda 2500

# 3. 传统单同行比较
python "<skill-dir>/scripts/checker.py" 0700.HK --compare --mc 50000 --rev 8000 --ni 1200 --ebitda 2500

# 4. 列出所有同行（全部行业）
python "<skill-dir>/scripts/checker.py" --list

# 5. 列出指定行业同行
python "<skill-dir>/scripts/checker.py" --list --industry 医药

# 6. JSON 输出
python "<skill-dir>/scripts/checker.py" --check --mc 50000 --rev 8000 --ni 1200 --ebitda 2500 --industry saas --json
```

> `<skill-dir>` 指本 `SKILL.md` 所在的目录。

## 同行数据库

### SaaS / 科技（4 只）

| Ticker | 名称 | P/E | P/S | EV/EBITDA | P/B |
|---|---|---|---|---|---|
| 0700.HK | 腾讯控股 | 22.5 | 5.2 | 18.3 | 4.1 |
| 2688.HK | 新奥能源 | 35.1 | 8.7 | 28.9 | 6.5 |
| 2013.HK | 微盟集团 | 45.0 | 10.5 | 35.0 | 8.2 |
| 9923.HK | 移卡 | — | 3.8 | 20.5 | 2.1 |

### 医药 / 生物科技（3 只）

| Ticker | 名称 | P/E | P/S | EV/EBITDA | P/B |
|---|---|---|---|---|---|
| 1801.HK | 信达生物 | — | 12.3 | — | 3.2 |
| 2269.HK | 药明生物 | 48.2 | 6.1 | 32.4 | 5.8 |
| 6160.HK | 百济神州 | — | 8.5 | 45.0 | 2.8 |

### 金融（3 只）

| Ticker | 名称 | P/E | P/S | EV/EBITDA | P/B |
|---|---|---|---|---|---|
| 0005.HK | 汇丰控股 | 9.8 | 2.1 | 7.5 | 0.8 |
| 0011.HK | 恒生银行 | 12.0 | 3.4 | 10.2 | 1.1 |
| 0388.HK | 香港交易所 | 35.0 | 14.0 | 25.0 | 7.5 |

### 地产（2 只）

| Ticker | 名称 | P/E | P/S | EV/EBITDA | P/B |
|---|---|---|---|---|---|
| 0016.HK | 新鸿基地产 | 8.5 | 2.8 | 12.0 | 0.45 |
| 0012.HK | 恒基兆业地产 | 7.2 | 2.0 | 10.5 | 0.35 |

### 消费（2 只）

| Ticker | 名称 | P/E | P/S | EV/EBITDA | P/B |
|---|---|---|---|---|---|
| 0968.HK | 信义光能 | 18.5 | 2.5 | 12.0 | 3.2 |
| 2020.HK | 安踏体育 | 25.0 | 4.2 | 16.0 | 5.5 |

### 行业中位数

| 行业 | P/E | P/S | EV/EBITDA | P/B |
|---|---|---|---|---|
| SaaS | 30.0 | 6.5 | 25.0 | 5.0 |
| 医药 | 35.0 | 9.0 | 30.0 | 4.0 |
| 金融 | 12.0 | 3.0 | 10.0 | 1.0 |
| 地产 | 8.0 | 2.5 | 11.0 | 0.5 |
| 消费 | 20.0 | 3.5 | 14.0 | 4.0 |
| 通用 | 15.0 | 3.5 | 12.0 | 2.0 |

## 评分逻辑

### 加权公式

最终分数 = P/E得分 × 0.50 + P/S得分 × 0.25 + EV/EBITDA得分 × 0.25

缺失倍数的权重按比例重新分配给可用倍数。

### 单倍数打分

| 发行人/同行比值 | 判定 | 得分区间 |
|---|---|---|
| < 0.7 | 显著折价 | 80–100 |
| 0.7–0.9 | 小幅折价 | 70–80 |
| 0.9–1.1 | 基本持平 | 40–60 |
| 1.1–1.5 | 小幅溢价 | 20–50 |
| 1.5–2.0 | 明显溢价 | 10–40 |
| > 2.0 | 严重溢价 | 0–20 |

### 结论映射

| 综合得分 | 结论 | 建议 |
|---|---|---|
| ≥ 80 | 估值具备吸引力，多指标显示折价 | **建议申购** |
| 65–79 | 估值略低于同行，定价偏合理 | **建议申购** |
| 45–64 | 估值与同行基本持平，定价中性 | 可选择性参与 |
| 30–44 | 估值高于同行，存在一定溢价 | 谨慎参与 |
| < 30 | 估值显著高于同行，多指标严重溢价 | **建议回避** |

## API

### `ValuationChecker()`

| 方法 | 返回值 | 说明 |
|---|---|---|
| `check_valuation(issuer_metrics, peer_ticker=None, industry=None)` | `dict` | **推荐**。完整对标 + 结论输出，返回 `verdict`、`recommendation`、`score`、`comparisons`、`peer_range` |
| `get_peer_valuation(ticker, industry=None)` | `dict` | 查询同行估值倍数，不在库中回退到行业中位数 |
| `get_issuer_valuation(metrics)` | `dict` | 根据发行人财务数据计算 P/E、P/S、EV/EBITDA、P/B |
| `get_peer_range(industry=None)` | `dict` | 获取某行业所有同行的 min/max/median 区间 |

`metrics` 字段：`market_cap`、`revenue`、`net_income`、`ebitda`（必填），`book_value`、`growth_rate`（可选）。单位：百万港元。

## 输出示例

### check_valuation() — 估值偏高

```json
{
  "verdict": "估值高于同行，存在一定溢价",
  "recommendation": "谨慎参与",
  "score": 35,
  "issuer": {"pe": 41.67, "ps": 6.25, "ev_ebitda": 20.0},
  "peer": {"ticker": "saas_median", "name": "saas中位数",
           "pe": 30.0, "ps": 6.5, "ev_ebitda": 25.0, "source": "computed_median"},
  "comparisons": [
    {"multiple": "P/E", "issuer_value": 41.67, "peer_value": 30.0,
     "ratio": 1.39, "level": "小幅溢价", "score": 30, "weight": 0.50},
    {"multiple": "P/S", "issuer_value": 6.25, "peer_value": 6.5,
     "ratio": 0.96, "level": "基本持平", "score": 54, "weight": 0.25},
    {"multiple": "EV/EBITDA", "issuer_value": 20.0, "peer_value": 25.0,
     "ratio": 0.80, "level": "小幅折价", "score": 75, "weight": 0.25}
  ],
  "peer_range": {
    "pe": {"min": 22.5, "max": 45.0, "median": 30.0, "count": 4},
    "ps": {"min": 3.8, "max": 10.5, "median": 6.5, "count": 4},
    "ev_ebitda": {"min": 18.3, "max": 35.0, "median": 25.0, "count": 4}
  }
}
```

## 当前局限

同行库包含 14 只港股，手动维护。后续可接入 Yahoo Finance / Wind API 实现实时数据更新。行业中性回退值仅供参考。

## 资源

- 脚本：[scripts/checker.py](scripts/checker.py)（~420 行，含 14 只同行 + 5 行业中位数 + 多维加权评分）
