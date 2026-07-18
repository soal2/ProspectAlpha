---
template_id: fragment_biotech
version: 1
kind: fragment
compatible_schema_version: "2026.07"
required_sections: []
required_components: []
provides_sections:
  - bio_rd_pipeline
  - bio_regulatory
  - bio_patents
  - bio_licensing
  - bio_commercialization
  - bio_manufacturing_quality
provides_slot_targets: []
---

{{#section bio_rd_pipeline}}
## 研发管线

生物医药公司专属风险章节。涵盖在研产品管线进展、临床试验阶段、临床失败风险、候选产品临床数据等。

```component
kind: pipeline_chart
params:
  industry: biotech
```
{{#section bio_regulatory}}
## 监管审批进度

NMPA / FDA / EMA 等监管机构的审批状态、临床试验批件（IND/CTA）、上市许可（NDA/BLA/MAA）的进度与风险。

```component
kind: event_timeline
params:
  filter: regulatory_milestone
```
{{#section bio_patents}}
## 专利与知识产权

核心化合物专利、制剂专利、工艺专利、用途专利的布局与到期风险。

```component
kind: risk_causal_graph
params:
  scope: patents
```
{{#section bio_licensing}}
## 授权与合作

对外授权（license-out）、引入授权（license-in）、合作开发安排的风险与潜在利益冲突。
{{#section bio_commercialization}}
## 商业化进展

已上市产品的销售曲线、医保谈判、进入医保目录、集中带量采购影响等。
{{#section bio_manufacturing_quality}}
## 生产与质量

GMP 合规性、生产场地检查记录、CMC（化学制造与控制）风险、供应链关键原料稳定性。
