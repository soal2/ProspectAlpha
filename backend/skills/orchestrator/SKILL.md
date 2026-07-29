---
name: skill6-orchestrator
description: 顶层编排器，串联全部港股IPO分析技能——文档检索、估值检查、现金流分析、情绪打分——输出加权评估报告（含置信度评估和跨维度风险汇总）。当需要对招股书进行完整的端到端IPO分析时使用。
---

# 编排器（Orchestrator）

顶层管线，协调全部原子技能（skill1-skill4），按行业自动分配权重，输出一个 **0–100 分的加权 IPO 评估报告**，附带**置信度评估**和**跨维度风险汇总**。

## 设计理念

四个原子 skill 各自回答了"某一个维度"的问题。编排器的价值在于：

| 层次 | 能力 | 说明 |
|---|---|---|
| **路由层** | 行业自动检测 | 从招股书文本中识别行业，匹配差异化权重策略 |
| **执行层** | 调用各 skill 的最新 check_xxx 接口 | 不再只拿原始数据，而是拿结论 + 置信度 + 风险信号 |
| **聚合层** | 加权评分 + 风险汇总 | 将四个维度的分数加权融合，同时汇总所有维度的风险信号 |
| **可信层** | 置信度评估 | 判断文档质量、数据完整度、情绪可靠性，标注整个报告的可信度 |

## 架构

```
main.py
  └── orchestrator.py (IPOAnalysisOrchestrator)
        ├── skill2_document_retrieval  → DocRetriever
        │     └── get_summary() + detect_sections() + find_financial_sections()
        ├── skill4_valuation_checker   → ValuationChecker
        │     └── check_valuation(issuer_metrics, industry=...)
        ├── skill1_cashflow_analyzer   → CashflowAnalyzer
        │     └── check_risk(cashflow_data)
        └── skill3_sentiment_scorer    → SentimentScorer
              └── check_sentiment(ticker)
```

全部 skill 通过 `importlib.util` 从编号目录中动态加载。每个 skill 都调用其最新的结论式接口（`check_xxx`），而非原始的裸数据接口。

## 触发场景

- 用户提供了一份招股书 PDF，要求"全面分析这只新股能不能打"
- 需要一键运行所有分析流程，输出综合评分和申购建议
- 需要根据行业自动调整估值/现金流/情绪等维度的权重
- 需要看到跨维度的风险汇总（哪些维度同时亮红灯）
- 需要了解整个报告的置信度（数据是否充分、各模块是否可靠）

## 快速开始

```bash
# 1. 基础分析（只需要 PDF）
python "<skill-dir>/scripts/main.py" <招股书路径.pdf>

# 2. 指定行业 + ticker（更多信息 = 更准评分）
python "<skill-dir>/scripts/main.py" <招股书路径.pdf> --industry-hint SaaS --ticker 0700.HK

# 3. 指定发行人财务数据（用于估值对标）
python "<skill-dir>/scripts/main.py" <招股书路径.pdf> \
  --mc 50000 --rev 8000 --ni 1200 --ebitda 2500 \
  --ticker 0700.HK --industry-hint 医药
```

> `<skill-dir>` 指本 `SKILL.md` 所在的目录。

## 行业策略路由

编排器自动从招股书文本中**投票式**检测行业（keyword-score voting），匹配合适的权重策略：

| 行业 | 文档 | 估值 | 现金流 | 情绪 | 策略逻辑 |
|---|---|---|---|---|---|
| SaaS / 软件 | 15% | **40%** | 25% | 20% | 估值倍数波动大，以估值为核心 |
| 医药 / 生物科技 | 10% | 25% | **45%** | 20% | 未盈利企业多，现金跑道最关键 |
| 金融 / 银行 | 20% | 30% | 30% | 20% | 各维度均衡 |
| 地产 / 建筑 | 15% | 30% | **40%** | 15% | 高杠杆行业，现金流与负债优先 |
| 消费 / 零售 | 15% | 35% | 25% | 25% | 品牌受市场情绪影响较大 |
| 通用 / 默认 | 20% | 30% | 25% | 25% | 无行业匹配时的均衡回退 |

## 各维度评分来源

| 维度 | 调用接口 | 评分方式 |
|---|---|---|
| **文档质量** | `get_summary()` + `detect_sections()` + `find_financial_sections()` | 页数、章节数、文本可搜索性、财务报表定位数综合打分 |
| **估值对标** | `check_valuation()` | 多维加权评分（P/E 50%, P/S 25%, EV/EBITDA 25%），直接使用 0-100 分 |
| **现金流** | `check_risk()` | 风险等级映射：正常→85, 关注→55, 预警→25 |
| **市场情绪** | `check_sentiment()` | 情绪分数 [-1,1] 映射到 [0,100] |

## 风险汇总

编排器自动跨维度聚合所有风险信号：

- **现金流风险**：从 `check_risk()` 的 risks 数组中提取所有 预警/关注 指标
- **情绪风险**：信号为 bearish/strongly_bearish 时触发
- **估值风险**：评分 < 50 触发关注，< 35 触发预警
- **文档风险**：关键财务报表定位不完整（< 3/5）时触发

风险汇总输出：总风险数、预警数、关注数、是否有致命风险（预警 ≥ 1）。

## 置信度评估

四个维度各自检查数据完整度：

| 因素 | low 条件 | medium 条件 |
|---|---|---|
| 文档质量 | 识别章节 < 3 | — |
| 情绪评分 | 置信度为 low | 置信度为 medium |
| 现金流 | 综合风险 = 数据不足 | — |
| 估值对标 | — | 可用倍数 < 2 |

综合置信度取最差（low > medium > high）。

## 输出

### 完整报告结构

```json
{
  "meta": {"pdf_path": "...", "industry_strategy": "SaaS / 软件", "ticker": "0700.HK"},
  "weights": {"document": 0.15, "valuation": 0.40, "cashflow": 0.25, "sentiment": 0.20},
  "scores": {"document": 72, "valuation": 65, "cashflow": 55, "sentiment": 70},
  "weighted_total": 64.25,
  "verdict": {
    "level": "中高危风险",
    "summary": "重点跟踪，谨慎参与",
    "action": "建议小仓位或观望",
    "priority": 2
  },
  "confidence": {
    "level": "medium",
    "label": "中等置信度",
    "factors": [
      {"factor": "情绪评分", "level": "low", "reason": "仅 2/5 维度可用"}
    ]
  },
  "risk_summary": {
    "total_risks": 2,
    "critical_count": 1,
    "warning_count": 1,
    "has_critical": true,
    "risks": [
      {"source": "cashflow", "severity": "预警",
       "indicator": "经营活动现金流量净额持续为负",
       "detail": "最近 3 期经营现金流均为负，持续失血"},
      {"source": "document", "severity": "关注",
       "indicator": "关键财务报表不完整",
       "detail": "仅定位到 2/5 类报表"}
    ]
  },
  "details": {
    "document": {"sections_found": 12, "financial_statements_located": 4, ...},
    "valuation": {"verdict": "估值略低于同行", "recommendation": "建议申购", ...},
    "cashflow": {"has_risk": true, "overall_risk": "关注", "risks": [...], ...},
    "sentiment": {"conclusion": "申购情绪偏热", "recommendation": "建议申购", ...}
  }
}
```

### 风险等级映射

| 总分 | 风险等级 | 说明 | 操作建议 |
|---|---|---|---|
| ≥ 75 | **低危风险**（优先级3） | 常规关注，无实质性利空 | 可正常参与打新 |
| 50 ~ 74 | **中高危风险**（优先级2） | 重点跟踪，谨慎参与 | 建议小仓位或观望 |
| < 50 | **极高危风险**（优先级1） | 优先核查，一票否决打新 | 建议回避本次打新 |

输出 `verdict` 字段包含 `level`、`summary`、`action`、`priority` 四个子字段。

## CLI 参数

| 参数 | 说明 |
|---|---|
| `pdf_path` | 招股书 PDF 路径（必填） |
| `--industry-hint` | 行业标签（覆盖自动检测） |
| `--ticker` | 股票代码（用于情绪评分） |
| `--mc` / `--rev` / `--ni` / `--ebitda` | 发行人财务数据（用于估值对标） |

## 依赖

- `pdfplumber` — PDF 解析
- `pandas`、`openpyxl` — Excel 数据处理
- `anthropic` — LLM 融合（可选）

## 资源

- 入口脚本：[scripts/main.py](scripts/main.py)
- 编排逻辑：[scripts/orchestrator.py](scripts/orchestrator.py)
- 依赖清单：[scripts/requirements.txt](scripts/requirements.txt)
- 官方数据源：[references/official-sources.md](references/official-sources.md)
- 关联 skill：[skill1](../skill1_cashflow_analyzer/SKILL.md) · [skill2](../skill2_document_retrieval/SKILL.md) · [skill3](../skill3_sentiment_scorer/SKILL.md) · [skill4](../skill4_valuation_checker/SKILL.md) · [skill5](../skill5_backtest/SKILL.md)
