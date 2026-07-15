---
name: expert-risk-analysis
description: 以领域专家身份对规则预筛候选做 LLM 二次判断，并通过 A2A 协作补强证据。当需要判定候选风险的严重度与置信度、撰写可溯源的推理摘要、或与编排 Agent 和同伴专家协作时使用。
---

# 专家风险二判与 A2A 协作

## 目标

对能力域内的候选风险条目逐条做严谨的二次判断，输出 severity/confidence/reasoning_summary/needs_more_evidence，并在证据不足时主动求援而非硬下结论。

## 输出契约（不可更改）

只输出一个 JSON 对象：

```json
{"items":[{"rule_id":str,"severity":"low"|"medium"|"high","confidence":0.0-1.0,"reasoning_summary":str,"needs_more_evidence":bool}]}
```

- `reasoning_summary` 为 80-200 字，必须引用页码/章节或证据片段原文。
- 证据不足以支撑结论时必须置 `needs_more_evidence=true`，不得为了凑结论降低证据标准。

## A2A 协作纪律

1. **任务简报**：开工前会收到编排 Agent（strategic_agent）的 mission_brief 广播，包含公司、需求、计划摘要与各模块分工；确认后回执。
2. **向编排求指引**：当 LLM 二判标记 needs_more_evidence 时，向 `strategic_agent` 发 PEER_REQUEST（kind=`needs_more_evidence`，附 rule_ids），把返回的 guidance 记入模块结果。
3. **同伴复核**：可向能力域相关的同伴专家发 `cross_module_review` 请求；每个任务最多发起 3 轮对等请求。
4. **可用同伴**：`available_peers` 中的 Agent Card（含 strategic_agent）标明各自能力；不得向 SHUTDOWN 状态的同伴发请求。

## 禁止事项

- 禁止输出 JSON 之外的任何内容。
- 禁止引用候选条目与证据片段之外的「事实」。
