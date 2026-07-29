---
name: skill3-sentiment-scorer
description: 基于2016–2026年历史数据（1,184条记录）的5维加权IPO情绪评分模型——含结论式输出、置信度评分、行业情绪叠加、历史准确率验证。当需要评估新股申购热度、市场情绪或判断是否会被超额认购时使用。
---

# 情绪打分器（Sentiment Scorer）

5 维加权评分模型，所有维度均在招股认购期间可观测，**不依赖上市后数据**。提供四大增强能力：**结论式输出**、**置信度评分**、**行业情绪叠加**、**历史准确率验证**。可选接入 Claude LLM 做定性因素融合。

## 设计理念

传统情绪评分只给一个数字（如 0.72），用户不知道这个数字意味着什么、可信度多高。本 skill 的设计理念：

| 层次 | 能力 | 对应方法 | 解决的问题 |
|---|---|---|---|
| **基础层** | 5维加权评分 | `get_sentiment_score()` | 这个 IPO 热度是多少？ |
| **结论层** | 结论式输出 + 操作建议 | `check_sentiment()` | 应该申购还是回避？ |
| **可信层** | 置信度评分 | `_compute_confidence()` | 这个分数靠谱吗？ |
| **验证层** | 历史准确率统计 | `get_accuracy_stats()` | 模型历史上预测准不准？ |

## 触发场景

- 用户询问某只新股的申购热度如何、是否值得申购
- 需要判断 IPO 是否可能被超额认购，获取明确的操作建议
- 需要对比多只新股的申购情绪并排名
- 需要了解模型的历史预测准确率，评估打分的可信度
- 作为 orchestrator 管线中的市场情绪维度输出加权分数

## 快速开始

```bash
# 1. 结论式输出（推荐）—— 一键获取操作建议 + 置信度
python "<skill-dir>/scripts/scorer.py" 6871.HK --check

# 2. 结论 + 详细维度分解
python "<skill-dir>/scripts/scorer.py" 6871.HK --check --verbose

# 3. 基本打分
python "<skill-dir>/scripts/scorer.py" 6871.HK

# 4. 批量对比 + 排名
python "<skill-dir>/scripts/scorer.py" --batch 6871.HK 7666.HK 7630.HK --rank

# 5. 规则引擎 + LLM 融合分析（需设置 ANTHROPIC_API_KEY）
python "<skill-dir>/scripts/scorer.py" 6871.HK --analyze

# 6. 查看历史准确率统计
python "<skill-dir>/scripts/scorer.py" --accuracy

# 7. 仅查看参考数据
python "<skill-dir>/scripts/scorer.py" 6871.HK --ref-only --json
```

> `<skill-dir>` 指本 `SKILL.md` 所在的目录。

## 评分模型

### 5 维权重

| 维度 | 权重 | 计分逻辑 | 说明 |
|---|---|---|---|
| 认购倍数 | 42% | 7档阶梯：>1000x→+1.00 到 <1x→-0.50 | 核心市场温度，与首日收益 Pearson r≈0.34 |
| 中签率 | 21% | 8档阶梯：<0.05%→+1.00 到 >80%→-0.60 | 稀缺性反向指标，中签率越低越热 |
| 申购参与度 | 16% | 0.5×申购人数分 + 0.5×乙组占比分 | 人数>10万→+1.00；乙组>80%→+1.00 |
| 保荐人质量 | 11% | Tier1→+1.00, Tier2→+0.60, Tier3→0.00, Tier4→-0.30 | 4级保荐人分类 + 基石投资者检测(+0.05) |
| 发行结构 | 10% | 0.6×上市板分 + 0.4×募资规模分 | 主板→+0.20；GEM→-0.50；募资>100亿→+0.80 |

### 信号映射

| 分数区间 | 信号 | 结论 | 建议 |
|---|---|---|---|
| > 0.50 | `strongly_bullish` | 申购情绪极热，市场追捧度极高 | **强烈建议申购** |
| 0.25 ~ 0.50 | `bullish` | 申购情绪偏热，市场需求旺盛 | **建议申购** |
| -0.25 ~ 0.25 | `neutral` | 申购情绪中性 | 可选择性参与 / 谨慎参与 |
| -0.50 ~ -0.25 | `bearish` | 申购情绪偏冷，市场关注度低 | 谨慎参与 |
| < -0.50 | `strongly_bearish` | 申购情绪极冷，市场冷淡 | **建议回避** |

### 动态权重重分配

当某个维度缺失数据时，其权重按比例分配给其余可用维度。但缺失维度会降低置信度。

### 置信度评分（新增）

| 等级 | 条件 | 含义 |
|---|---|---|
| **high** (高) | 4-5 个维度可用 | 分数可靠，可放心参考 |
| **medium** (中) | 2-3 个维度可用 | 分数有一定参考价值，但权重重分配可能放大偏差 |
| **low** (低) | 1 个维度可用 | 分数可靠性低，仅作粗略参考 |
| **none** (无) | ticker 不在数据集中 | 使用行业中性回退值，不可用于决策 |

### 行业情绪叠加（新增）

根据同行业近期 IPO 的首日表现，对基础分数做小幅调整（范围：-0.08 ~ +0.08）。检测逻辑：

- 计算该行业近期 IPO（尾 20 条）的平均首日涨幅 vs 该行业长期均值
- 偏离 > +15% → +0.08（行业风口，正面情绪）
- 偏离 > +5% → +0.04
- 偏离 < -15% → -0.08（行业寒冬，负面情绪）
- 偏离 < -5% → -0.04

### 基石投资者检测（新增）

保荐人/主承销商字段中如果识别到顶级基石投资者（高瓴、淡马锡、GIC、红杉、社保基金等），在保荐人分数上额外 +0.05。

### LLM 融合（可选）

当设置了 `ANTHROPIC_API_KEY` 且使用 `--analyze` 时：最终分数 = 0.7 × 规则引擎 + 0.3 × Claude。LLM 评估保荐人阵容强弱、基石投资者质量、行业当前情绪等定性因素。

## 历史准确率验证（新增）

`get_accuracy_stats()` 将全部 1,184 条历史记录逐一打分，与真实首日收益对比，输出：

| 统计项 | 说明 |
|---|---|
| 相关系数 (Pearson r) | 模型分数与实际首日收益的相关性。> 0.30 = 良好，> 0.10 = 有一定参考价值 |
| 按信号分层准确率 | 每个信号等级(strongly_bullish~strongly_bearish)的实际首日表现（均涨幅、上涨率、最佳/最差） |
| 评分分布 | 全部 ticker 的分数均值和范围 |
| 信号分布 | 各等级信号的数量分布 |

**CLI**：`--accuracy`

## API

### `SentimentScorer(data_path=None)`

| 方法 | 返回值 | 说明 |
|---|---|---|
| `check_sentiment(ticker)` | `dict` | **推荐**。结论式输出，返回 `conclusion`、`recommendation`、`score`、`signal`、`confidence`、`industry_overlay` |
| `get_sentiment_score(ticker)` | `dict` | 5 维加权评分，返回 `score`、`signal`、`dimensions`、`active_weights` |
| `analyze(ticker)` | `dict` | 规则引擎 + LLM 融合分析 |
| `get_accuracy_stats()` | `dict` | 历史准确率统计（模型验证） |
| `get_reference_data(ticker)` | `dict \| None` | 从 Excel 查找 ticker 的申购数据 |
| `score_text(text)` | `float` | 对任意文本做基于词库的情感打分 |

## CLI 参数一览

| 参数 | 说明 |
|---|---|
| `ticker` | 股票代码（如 6871.HK） |
| `--check` | **推荐**。结论式输出，含操作建议 + 置信度 |
| `--verbose` / `-v` | 显示每维度得分 + 自然语言描述（配合 `--check` 使用效果最佳） |
| `--analyze` / `-a` | LLM 融合分析（需 ANTHROPIC_API_KEY） |
| `--batch` / `-b` | 批量打分 |
| `--rank` | 按分数降序排列批量结果 |
| `--accuracy` | 历史准确率统计 |
| `--text` / `-t` | 对自定义文本做词库情感打分 |
| `--ref-only` / `-r` | 仅查看参考数据 |
| `--json` / `-j` | JSON 输出 |
| `--data` / `-d` | 指定 Excel 数据路径 |

## 输出示例

### check_sentiment() — 极热信号

```json
{
  "ticker": "6871.HK",
  "has_signal": true,
  "conclusion": "申购情绪极热，市场追捧度极高",
  "recommendation": "强烈建议申购",
  "score": 0.74,
  "signal": "strongly_bullish",
  "confidence": {
    "level": "high",
    "label": "高置信度",
    "available_dimensions": 5,
    "total_dimensions": 5,
    "missing_dimensions": []
  },
  "industry_overlay": {
    "bonus": 0.08,
    "note": "药品及生物科技近期IPO首日表现强劲（均45.2%），行业情绪正面"
  },
  "dimensions": {
    "subscription_ratio": {"score": 0.85, "weight": 0.42, "value": 1200.0, "status": "ok"},
    "lottery_rate": {"score": 0.80, "weight": 0.21, "value": 0.03, "status": "ok"},
    "participation": {"score": 0.60, "weight": 0.16, "status": "ok"},
    "sponsor_quality": {"score": 1.00, "weight": 0.11, "tier_label": "Tier 1", "cornerstone_bonus": 0.0, "status": "ok"},
    "deal_structure": {"score": 0.20, "weight": 0.10, "status": "ok"}
  }
}
```

### check_sentiment() — 中性偏冷 + 低置信度

```json
{
  "ticker": "NEW.HK",
  "has_signal": false,
  "conclusion": "申购情绪中性偏冷（低置信度），缺乏明确方向",
  "recommendation": "谨慎参与",
  "score": -0.15,
  "signal": "neutral",
  "confidence": {
    "level": "low",
    "label": "低置信度",
    "available_dimensions": 2,
    "total_dimensions": 5,
    "missing_dimensions": ["participation", "sponsor_quality", "deal_structure"],
    "reason": "仅 2/5 维度可用，评分可靠性低"
  }
}
```

### get_accuracy_stats() — 历史验证

```json
{
  "total_validated": 856,
  "correlation_with_first_day": 0.3421,
  "tier_accuracy": {
    "strongly_bullish": {"count": 128, "avg_first_day": 45.3, "up_ratio": 78.9},
    "bullish": {"count": 310, "avg_first_day": 18.2, "up_ratio": 65.5},
    "neutral": {"count": 280, "avg_first_day": -2.1, "up_ratio": 42.1},
    "bearish": {"count": 98, "avg_first_day": -15.8, "up_ratio": 22.4},
    "strongly_bearish": {"count": 40, "avg_first_day": -28.5, "up_ratio": 10.0}
  },
  "signal_distribution": {
    "strongly_bullish": 128, "bullish": 310,
    "neutral": 280, "bearish": 98, "strongly_bearish": 40
  }
}
```

## 依赖

- `pandas` + `openpyxl` — Excel 数据读取
- `anthropic`（可选）— LLM 融合分析

## 资源

- 脚本：[scripts/scorer.py](scripts/scorer.py)（~1050 行，含 5 维评分 + 结论输出 + 置信度 + 行业叠加 + 历史验证）
- 数据：[references/IPO_data.xlsx](references/IPO_data.xlsx)（1,184 条记录，2016–2026）
- LLM 提示词：[references/sentiment_prompt.md](references/sentiment_prompt.md)
