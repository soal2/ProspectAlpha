---
name: rag-embedding-index
description: 将招股书文本块向量化并写入混合检索索引（DashScope embedding + Qdrant + 关键词倒排）。当需要建立或更新 RAG 知识库、按语义检索招股书原文、或为证据检索准备索引时使用。
---

# RAG 向量化与混合检索

## 目标

把解析后的文本块转成 1024 维语义向量并入库，使专家可以用自然语言问题检索到相关原文块。

## 索引契约

- Embedding 模型：阿里云 DashScope `text-embedding-v4`（OpenAI 兼容接口），向量维度 1024。
- 向量库：Qdrant，collection 名按「模型+维度」版本化（`prospect_alpha_blocks_{model}_{dims}`），避免维度冲突。
- 同时维护 SQL 端 `retrieval_index` 表（含 terms 倒排与向量副本），检索时融合关键词与语义得分。
- 批量 embed 每批 10 条，单条截断约 8000 字符；embedding 失败必须硬失败并上报，禁止静默降级为假向量。

## 工作步骤

1. 解析完成后，对全部 DocumentBlock 调用 `index_blocks(run_id=..., blocks=..., replace=True)` 建立本次分析的索引。
2. 长文档（600 页级）向量化需 1-3 分钟，通过 `on_progress` 回调发布 SSE 进度事件。
3. 检索时用 `search(run_id=..., query=..., limit=...)`，返回块的全部溯源字段（document_id/页码/bbox/source_hash）。
4. 报告分节沉淀走 `run_id="knowledge_reports"` 且 `replace=False`（追加语义），跨 run 共享。

## 检索质量纪律

- 检索问题应贴近招股书用语（如「经营活动现金流为负」而非「缺钱」）。
- 命中块必须原样携带溯源字段传递下游，不得剥离。
