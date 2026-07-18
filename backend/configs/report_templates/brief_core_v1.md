---
template_id: brief_core
version: 1
kind: core_brief
compatible_schema_version: "2026.07"
required_sections:
  - risk_profile
  - risks_verified
  - risks_unresolved
  - evidence_quality
  - coverage_limits
required_components:
  - risk_profile_heatmap
provides_sections: []
provides_slot_targets: []
---

{{#section risk_profile}}
# 风险全景

目标公司：{{var.company.name}}（{{var.company.stock_code}}）

外部信息截止日期：{{var.run.external_cutoff_date}}

本简报仅汇总已通过证据门槛的重大风险事项，以及裁决为"重大待核实"的事项。完整调查过程、所有假设与全部证据见同期生成的专项深度研究报告。

```component
kind: risk_profile_heatmap
params:
  dimensions: all
```
{{#section risks_verified}}
# 已验证重大风险事项

下列风险事项已通过证据门槛：至少一项一级权威来源，或两项相互独立的二级来源，并完成反证检索。本节为正式结论。

```component
kind: risk_card
params:
  selection: verified
```
{{#section risks_unresolved}}
# 重大待核实事项

下列事项经红队复核被裁决为"重大待核实"（material_unresolved 或 escalate_human）。该类事项不构成正式结论，但需在投资决策前进一步核实。

```component
kind: risk_card
params:
  selection: material_unresolved
```
{{#section evidence_quality}}
# 证据质量

```component
kind: source_quality
params:
  summary_only: true
```
{{#section coverage_limits}}
# 覆盖与限制

- 研究完成状态：{{var.artifact.completion_status}}
- 覆盖维度数：{{var.artifact.coverage_dimension_count}}
- 是否触发人工复核：{{var.bundle.review_required}}
- 数据限制与未能覆盖的范围详见深度研究报告"方法与研究限制"章节。
