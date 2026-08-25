---
name: evidence-verification
version: 1.0.0
description: 校验风险结论的证据链完整性并晋升合格证据入证据库。当需要判定某条 finding 是否有充分原文支撑、执行证据门禁、或把 supported 证据沉淀到跨 run 证据库时使用。
owners: [financial_agent, legal_agent, market_agent, evidence, quality, redteam]
execution: prompt_only
failure_policy: required
---

# 证据核验与沉淀

## 目标

保证每条正式风险结论都有可回溯、未篡改的原文证据；不达标的结论降级为「待人工补证」，不进入正式报告结论。

## 证据门禁标准

一条证据（RunEvidence）合格必须同时满足：

- `document_id` 非空且对应文档可回传原文（`GET /documents/{id}/file`）。
- `page_number` ≥ 1，`bbox` 为 4 元素数组，能在 PDF 预览中定位高亮。
- `source_hash` 与所引文本块的 SHA-256 一致（防篡改）。
- `verifier_status` 判定：原文直接支撑结论 → `supported`；仅间接相关 → `weakly_supported`；无法支撑 → 结论标记 `needs_human_review`。

## 工作步骤

1. 对每条 finding 遍历其 evidence_ids，逐条按上述标准核验。
2. 核验不通过的 finding 置 `requires_human_review=true`，在报告中列入「待人工补证事项」，不作为正式风险结论。
3. run 完成时，`supported` 状态的证据按 `sha256(document_block_id|text)` 去重后晋升入 evidence_library（跨 run 复用）。
4. 质检审裁阶段复查：核心判断证据、页码引用、风险等级一致性、无证据结论清零。

## 禁止事项

- 禁止用推测性语言（「可能存在」「据推断」）代替证据引用充当结论支撑。
- 禁止对同一证据重复计数以抬高置信度。
