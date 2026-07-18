---
template_id: deep_core
version: 1
kind: core_deep
compatible_schema_version: "2026.07"
required_sections:
  - company
  - business
  - financials
  - governance
  - regulatory
  - industry
  - valuation
  - key_personnel
  - supply_chain
  - reputation
  - hypothesis_matrix
  - weak_sources_appendix
  - evidence_and_counter
  - timeline
  - methodology
required_components:
  - hypothesis_matrix
  - source_quality
  - event_timeline
provides_sections: []
provides_slot_targets:
  - industry_sections
---

{{#section company}}
# 公司概况

- 公司名称：{{var.company.name}}
- 股票代码：{{var.company.stock_code}}
- 上市板块：{{var.company.listing_board}}
- 外部信息截止日期：{{var.run.external_cutoff_date}}
- 招股书披露日期：{{var.company.prospectus_date}}
{{#section business}}
# 业务

```component
kind: risk_card
params:
  dimension: 业务
  selection: formal
```
{{#section financials}}
# 财务

```component
kind: financial_trend
params:
  selection: formal
```
{{#section governance}}
# 治理

```component
kind: risk_card
params:
  dimension: 治理
  selection: formal
```
{{#section regulatory}}
# 监管

```component
kind: risk_card
params:
  dimension: 监管
  selection: formal
```
{{#section industry}}
# 行业竞争

下列章节由行业片段模板填充；若无匹配行业片段，使用通用行业风险描述。

{{#slot industry_sections}}
本插槽由行业片段填充。
{{#section valuation}}
# 估值

```component
kind: peer_comparison
params:
  selection: formal
```
{{#section key_personnel}}
# 核心人员

```component
kind: risk_card
params:
  dimension: 核心人员
  selection: formal
```
{{#section supply_chain}}
# 供应链

```component
kind: risk_card
params:
  dimension: 供应链
  selection: formal
```
{{#section reputation}}
# 声誉

```component
kind: risk_card
params:
  dimension: 声誉
  selection: formal
```
{{#section hypothesis_matrix}}
# 假设矩阵（非正式结论）

> 本章节内容不构成正式结论。下列假设包括调查中提出但尚未通过证据门槛、或被裁决为待补充/降级/驳回的假设。列出目的是为投资决策提供完整的研究轨迹。

```component
kind: hypothesis_matrix
params:
  include_statuses:
    - partially_supported
    - contested
    - inconclusive
    - refuted
```
{{#section weak_sources_appendix}}
# 弱来源隔离区

下列证据来自三级以下来源（论坛、自媒体、匿名消息等），仅作为调查线索保留，**不得作为正式结论依据**。

```component
kind: risk_causal_graph
params:
  evidence_tier: tier3
  label: 弱来源隔离区
```
{{#section evidence_and_counter}}
# 证据与反证

```component
kind: source_quality
params: {}
```
{{#section timeline}}
# 时间线

```component
kind: event_timeline
params: {}
```
{{#section methodology}}
# 方法与研究限制

- 研究完成状态：{{var.artifact.completion_status}}
- 覆盖维度：{{var.artifact.coverage_dimension_count}}
- 是否触发人工复核：{{var.bundle.review_required}}
- 触发原因：{{var.bundle.review_trigger_reasons}}
- 数据限制：本报告不保存完整网页快照或全文；外部证据以 URL 卡片、网页摘要和必要原文引用为限。
- 反证检索：所有正式结论已完成显式反证检索；未完成反证检索的假设计入假设矩阵附录，不计入正式结论。
