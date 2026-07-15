---
name: risk-rule-screening
description: 用数据库中的 143 条港股 IPO 风险规则对招股书文本做规则预筛，产出候选风险条目。当需要初筛风险信号、按专家能力域分配规则、或解释某条 finding 由哪条规则触发时使用。
---

# 风险规则预筛

## 目标

用结构化规则库（3 个维度、26 个类目、143 条规则）对全文做确定性关键词/条件匹配，形成候选风险条目（RuleEvaluation），供专家 LLM 二次判断。

## 规则库契约

- 规则存于数据库三表：规则集（rule set）→ 维度（dimension）→ 规则（rule），经 `GET/POST/PATCH/DELETE /risk-rules` 全量 CRUD。
- 每次分析 run 启动时冻结规则快照（rule_snapshot），run 期间规则修改不影响本次结果，下一次 run 自动生效。
- 每条规则含 `rule_id`（HKIPO- 前缀）、`rule_type`、`category`、`trigger_condition`、`severity` 缺省值。

## 工作步骤

1. 按专家能力域筛选规则子集：优先用技能映射（skill 的 rule_types/categories），否则用 AgentSpec 的 rule_types/categories。
2. 对文本执行 `evaluate_text_against_rules`；关键词命中只产出 `evaluation_result="candidate"` 的预筛条目，附 matched_terms 与证据片段。
3. `重大`、`显著`、`可能` 等泛化词不得独立形成候选；候选必须由专家二判验证实际触发条件，证据不足时标记 `insufficient_evidence`。
4. 候选条目必须保留 `rule_id`、`rule_set_id`、`rule_version`，保证结论可追溯到规则版本。
5. 预筛结果严禁直接当成最终结论，必须交给专家 LLM 二判（见 expert-risk-analysis 技能）。

## 禁止事项

- 禁止编造规则库中不存在的 rule_id。
- 禁止跳过快照机制直接读最新规则回填历史 run。
