---
name: report-generation-and-archive
version: 1.0.0
description: 整合全部专家模块结果生成正式 IPO 风险报告，并把报告沉淀入知识库与检索索引。当需要撰写最终报告、约束报告引用纪律、或复用历史报告结论时使用。
owners: [orchestrator]
execution: prompt_only
failure_policy: required
---

# 报告生成与知识沉淀

## 目标

把各专家模块结果整合为一份结构完整、全部结论可溯源的 IPO 风险报告，并沉淀为跨 run 可检索的知识资产。

## 报告输出契约

Agent 只输出内部结构化 JSON，不负责 Markdown 章节、标题或表格排版：

```json
{"title":str,"executive_summary":str,"overall_risk_level":str,
 "sections":[{"module_id":str,"title":str,"analysis":str,"finding_ids":[str],"evidence_ids":[str]}],
 "risk_themes":[{"title":str,"conclusion":str,"analysis":str,"investment_impact":str,
   "uncertainties":[str],"monitoring_points":[str],"finding_ids":[str],"evidence_ids":[str]}],
 "cross_module_risks":[{"title":str,"analysis":str,"finding_ids":[str],"evidence_ids":[str]}],
 "recommendations":[str],"limitations":[str]}
```

## 引用纪律（硬约束）

- 不得新增输入中不存在的 finding_id 或 evidence_id。
- 每个专家模块都必须有对应 section，即使结论是「未发现重大风险」。
- 每个被引用的 finding 必须在同一 section 中附带至少一条归属于它的 evidence。
- 没有可验证引用时不得创建跨模块风险。
- 未通过证据门禁的 finding 只能进「待人工补证事项」。
- `supported` 才能进入正式风险结论；`weakly_supported` 只能作为「推测性研判 / 待人工复核」单独展示。
- `risk_themes` 必须覆盖全部准入 finding 且每条 finding 只能出现一次；不得混合 supported 与 weakly_supported。
- 相同风险驱动因素应聚合为一个主题，最多 8 个；证据足够时目标为 5-8 个，证据不足时不得强行补齐。
- 主题正文必须解释风险结论、因果链、投研影响、不确定性与跟踪指标，不得只复述结构化字段。

## 固定模板边界

- 最终 Markdown 由报告生成模块按照《港股IPO风险穿透预警报告》固定模板确定性渲染。
- 模板章节、标题层级、字段和表格列名不得由 Agent 重写。
- 无输入数据写「待补充」；已有分析但证据不足写「暂无充分证据」，不得用生成内容填空。
- 发行人事实引用招股书 RunEvidence；市场章节可引用本次 run 冻结的 ResearchArtifact 和版本化数据快照。

## 知识沉淀

1. run 完成时报告自动归档：`report_library` 表按 source_run_id 幂等 upsert（公司、规则版本、综合风险等级、finding 数、全文与分节）。
2. 报告的 `## ` 级分节被切成合成块（`document_id=report_{run_id}`，`block_type=report_section`），以 `run_id="knowledge_reports"` 追加进混合检索索引。
3. 后续分析可检索 `knowledge_reports` 索引复用历史结论，经 `GET /knowledge/reports` 浏览全部归档报告。
4. 归档失败只记事件、不失败已完成的分析。
