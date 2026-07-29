---
name: skill5-backtest
description: 回测港股IPO上市后表现——首日收益率、择时环境分析和多维度打分。当需要评估历史IPO表现、分析上市时机（月份/年份效应）或对比预测情绪与实际交易结果时使用。
---

# 回测器（Backtest）

对港股 IPO 上市后表现进行评分和回测。基于 `IPO_data.xlsx` 中的 1,184 条记录，提供首日快照和月份/年份环境基准。当前为 **2 维模式**（无需 OHLCV 日线数据）。

## 触发场景

- 用户询问某只已上市 IPO 的首日表现如何
- 需要分析历史 IPO 的整体分布（涨跌比例、收益排名）
- 需要评估上市时机——当前月份/年份是否为 IPO 的有利窗口
- 需要按行业筛选回测数据

## 快速开始

```bash
# 1. 查看单只股票的首日表现
python "<skill-dir>/scripts/backtester.py" 6871.HK

# 2. 完整回测报告（分布、分层、时序、Top/Bottom 5）
python "<skill-dir>/scripts/backtester.py" --summary --json

# 3. 按行业筛选
python "<skill-dir>/scripts/backtester.py" --summary --industry "药品及生物科技"

# 4. 择时环境热力图
python "<skill-dir>/scripts/backtester.py" --timing

# 5. 对特定 ticker 的上市时机打分
python "<skill-dir>/scripts/backtester.py" --timing-score 6871.HK

# 6. 批量打分
python "<skill-dir>/scripts/backtester.py" --batch 6871.HK 7666.HK --json
```

> `<skill-dir>` 指本 `SKILL.md` 所在的目录。

## 评分模型（当前 2 维）

| 维度 | 权重 | 数据来源 |
|---|---|---|
| 首日表现 | 70% | `IPO_data.xlsx` |
| 择时环境 | 30% | `IPO_data.xlsx` |

### 首日评分规则

- **价格变动（70%）** ：7 档，从 >+100% → +1.00 到 < -30% → -0.80
- **换手率（30%）** ：10%-50% 区间视为健康活跃（+0.40），极端值惩罚

### 择时分析

从全部 1,184 条 IPO 记录预计算的月份/年份环境基准：
- **月份效应**：偏离全局均值的程度（如 2026-04 偏离 +109.9%，2024-04 偏离 -39.7%）
- **年份效应**：2026（+50.5%）和 2025（+37.0%）为牛市年份；2023（+2.9%）为最差年份

### 信号映射

| 分数区间 | 信号 |
|---|---|
| > 0.50 | `strongly_bullish` |
| 0.25 ~ 0.50 | `bullish` |
| -0.25 ~ 0.25 | `neutral` |
| -0.50 ~ -0.25 | `bearish` |
| < -0.50 | `strongly_bearish` |

## API

### `Backtester(data_path=None)`

| 方法 | 返回值 | 说明 |
|---|---|---|
| `score_first_day(ticker)` | `dict \| None` | 首日涨跌 + 换手率综合评分 |
| `get_first_day_data(ticker)` | `dict \| None` | 原始首日字段（涨跌幅、换手率、开盘涨跌等） |
| `get_timing_data(ticker)` | `dict \| None` | 上市月份/年份的环境基准对比 |
| `timing_analysis()` | `dict` | 完整的择时热力图（月度 + 年度） |
| `run_backtest(industry=None)` | `dict` | 批量回测报告（分布、分层、Top/Bottom 5） |
| `get_all_with_first_day(industry=None)` | `list` | 所有有首日数据的 ticker 列表 |

## 输出示例

```json
{
  "ticker": "6871.HK",
  "score": 0.65,
  "signal": "strongly_bullish",
  "first_day_chg": 85.5,
  "first_day_turnover": 35.2,
  "sub_score_chg": 0.85,
  "sub_score_turnover": 0.40
}
```

> **后续计划**：V2.0 将升级为 5 维模型（首日 25%、多期收益 30%、风险指标 20%、成交量/流动性 15%、择时 10%），届时需要 OHLCV 日线数据。

## 资源

- 脚本：[scripts/backtester.py](scripts/backtester.py)
- 数据：[references/IPO_data.xlsx](references/IPO_data.xlsx)（1,184 条记录，2016–2026）
