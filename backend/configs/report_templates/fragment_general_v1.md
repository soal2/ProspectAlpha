---
template_id: fragment_general
version: 1
kind: fragment
compatible_schema_version: "2026.07"
required_sections: []
required_components: []
provides_sections:
  - industry_overview
  - industry_competition
  - industry_regulatory
provides_slot_targets: []
---

{{#section industry_overview}}
## 行业概述

通用行业风险概述。本节由通用行业片段提供；若目标公司属于特定行业（如生物医药），深度核心模板会优先选择对应行业片段。

{{#section industry_competition}}
## 行业竞争格局

```component
kind: peer_comparison
params:
  scope: industry_fragment
```
{{#section industry_regulatory}}
## 行业监管动态

通用监管风险点，结合公司所属监管框架细化。
