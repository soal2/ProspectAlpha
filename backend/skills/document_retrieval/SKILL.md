---
name: document-retrieval
version: 1.0.0
description: 在现有招股书索引内执行章节、财务表格、关键词与跨页联合检索，返回可溯源 DocumentBlock。
owners: [evidence]
execution: callable
input_schema: DocumentRetrievalSkillInput
output_schema: DocumentRetrievalSkillResult
failure_policy: required
---

# 长文档检索

## 输入与输出

- 只读取本次 run 的 document_id、已索引 block ID、查询和规则上下文。
- 输出 DocumentBlock ID、页码、章节路径、bbox、source hash 与检索分数。

## 约束

- 复用 `riskagent.parsing` 与 `riskagent.retrieval`，不自行重新打开 PDF 建立第二套索引。
- 跨页表格必须保留各页来源；无法定位原文时不得返回正式证据。
