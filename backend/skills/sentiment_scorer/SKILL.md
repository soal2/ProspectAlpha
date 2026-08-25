---
name: sentiment-scorer
version: 1.0.0
description: 分别评估版本化历史认购热度与博查冻结的新闻、分析师和社交舆情，形成可解释的市场情绪风险。
owners: [market_agent]
execution: callable
input_schema: SentimentSkillInput
output_schema: SentimentSkillResult
failure_policy: partial
---

# 市场情绪分析

## 两类输入不得混淆

- 认购热度：认购倍数、中签率、申购人数、保荐人、基石投资者和发行结构。
- 外部舆情：按公司名称和股票代码经博查检索后冻结的新闻、分析师与社交来源。

## 输出

- 分别报告认购热度、新闻舆情和社交舆情的覆盖情况与风险信号。
- 引用数据快照和 ResearchArtifact ID，列明检索时间、来源与限制。
- 博查失败或历史数据未命中时返回 partial/insufficient_data，不能用行业均值伪装成发行人事实。

## 禁止事项

- 不输出申购、买入或卖出建议。
- 不把新闻数量直接等同于情绪方向，不引用未冻结的搜索结果。
