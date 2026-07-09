# ProspectAlpha 后端分析流程设计

本文档记录 ProspectAlpha 后端从“用户提出 IPO 风险分析问题”到“生成报告并沉淀证据”的实现层设计。当前目标是为多人协作开发建立统一契约，不代表业务功能已经实现。

## 1. 设计目标

ProspectAlpha 后端要支撑以下主链路：

```text
用户问题 / 招股书材料
-> AI 信息完整性检查
-> 用户补全必要信息
-> 创建并启动分析任务
-> PDF 解析与结构化
-> 原文块入库与 Hybrid Search 索引
-> 风险特征抽取
-> 多 Agent 协同分析
-> Verifier 证据门禁
-> 报告生成
-> 本次证据固化
-> 合格证据异步晋升到全局证据库
```

核心原则：

- `analysis_run` 是全流程根实体，PDF、规则、Agent、证据、事件、报告都挂在同一个 `run_id` 下。
- 证据先行。没有可回溯原文证据的风险判断，不能进入正式风险结论。
- 未上市企业分析不得使用上市后个股表现作为输入。
- 用户不需要一开始填写完整配置；后端先检查信息缺口，再返回补全问题。
- 报告生成不等于人工批准，AI 报告默认是待复核草案。

## 2. 非目标

MVP 阶段不做以下事情：

- 不做半本 PDF 的流式分析。必须完整解析并完成索引后再启动 Agent。
- 不做 Agent 自主改图、无限反思循环或复杂辩论。
- 不把所有本次证据自动写入全局知识库。
- 不把回测和监控塞进单次分析主链路。
- 不把 PDF 二进制直接存进 PostgreSQL。
- 不记录完整 chain-of-thought，只记录可审计的事件摘要与结构化输入输出。

## 3. 信息完整性检查

用户在工作台先提交自然语言问题、招股书线索或附件。后端第一步不是直接分析，而是创建一个可审计的 draft/clarifying run。

硬阻塞信息只包括：

- 分析对象：公司名称、申请编号或明确的 IPO 项目线索，至少一个。
- 招股书材料：上传 PDF、可下载 URL 或已存在 `document_id`，至少一个。
- 用户确认：同意使用系统默认风险规则、默认 Agent 队伍和默认分析深度。

非阻塞信息可以使用默认值或后续补充：

- 风险规则版本。
- 分析深度。
- Agent 使用偏好。
- 行业、发行规模、保荐人、拟上市代码。
- 上市前行情与市场窗口数据。

推荐响应结构：

```json
{
  "run_id": "run_xxx",
  "status": "clarifying",
  "understood": {
    "company_name": "待确认",
    "material_hint": "已提供招股书链接",
    "focus": ["现金流", "对赌条款"]
  },
  "blocking_questions": [],
  "optional_questions": [],
  "assumed_defaults": {
    "risk_rule_version": "active",
    "analysis_depth": "deep",
    "agent_group": "default"
  }
}
```

## 4. 分析任务状态机

`analysis_run` 负责表达一次分析任务的生命周期。前端工作台、实时分析页、证据页和报告页都围绕 `run_id` 查询。

主状态：

```text
draft
-> clarifying
-> ready
-> queued
-> ingesting
-> parsing
-> indexing
-> extracting_features
-> agent_running
-> verifying
-> reporting
-> completed
```

异常与人工状态：

```text
waiting_user
failed
cancelled
partial_completed
```

状态字段建议：

- `analysis_runs.status`：粗粒度状态，用于列表和页面入口。
- `analysis_runs.current_stage`：当前执行阶段，用于进度条。
- `analysis_runs.failed_stage`：失败阶段。
- `analysis_runs.error_summary`：可展示错误摘要。
- `analysis_events`：细粒度事件日志，用于实时流、回放和审计。

输入修改规则：

- `draft / clarifying / ready`：允许修改问题、材料、规则、Agent 偏好和分析深度；修改后重新执行完整性检查。
- `queued` 及之后：输入冻结；只能取消任务或创建新 run。

## 5. API 契约

推荐使用资源语义清晰的 REST 命名。

### 5.1 分析任务

```text
POST   /analysis-runs
PATCH  /analysis-runs/{run_id}/inputs
POST   /analysis-runs/{run_id}/start
POST   /analysis-runs/{run_id}/cancel
GET    /analysis-runs/{run_id}
GET    /analysis-runs/{run_id}/events
GET    /analysis-runs/{run_id}/evidence
GET    /analysis-runs/{run_id}/findings
GET    /analysis-runs/{run_id}/report
```

`POST /analysis-runs` 的语义是“创建 draft run 并执行信息完整性检查”，不是无状态的 clarify。

`POST /analysis-runs/{run_id}/start` 只负责：

- 校验 run 是否 ready。
- 固化输入、规则快照和数据截止日期。
- 状态改为 `queued`。
- 投递后台任务。
- 立即返回 run 状态。

完整分析必须在异步 worker 中执行，不能阻塞 HTTP 请求。

### 5.2 文档材料

```text
POST /documents
POST /documents/fetch
GET  /documents/{document_id}
GET  /documents/{document_id}/blocks
```

上传 PDF 和招股书 URL 都统一抽象成 `document_asset`。后续流程只绑定 `document_id`，不关心材料来源。

MVP 只支持每个 run 一份主招股书，但数据模型预留多文档关系。

```text
analysis_run_documents
- run_id
- document_id
- role
- is_primary
- added_by
- created_at
```

MVP 唯一约束：

```text
每个 run 只能有一个 is_primary = true 且 role = primary_prospectus 的 document
```

### 5.3 规则与 Agent

```text
GET /risk-rules
GET /risk-rules/{version}
GET /agents
```

默认 active 规则集来自 `backend/configs/hk_ipo_risk_rules_v1.json`，规则集标识为 `hk_ipo_prospectus_risk_framework_v1`，覆盖港股 IPO 招股书的 3 个维度、26 个一级分类、143 个二级风险项：

| 维度 | 类型 | 一级分类数 | 规则数 | 使用方式 |
|---|---:|---:|---:|---|
| 维度1：标准化财务指标风险 | `quantitative_financial` | 7 | 48 | 财务表格抽取、指标计算、行业阈值比较 |
| 维度2：非标隐性风险 | `qualitative_disclosure` | 11 | 56 | 章节检索、条款抽取、证据归因 |
| 维度3：文本粉饰度风险 | `textual_nlp` | 8 | 39 | NLP 文本统计、跨章节一致性与措辞分析 |

`GET /risk-rules` 面向前端展示规则目录，按 `dimension -> category -> rules` 返回摘要字段：`rule_id`、`risk_item`、`trigger_condition`、`source_sections`。`GET /risk-rules/{version}` 返回完整规则集，包含 `description`、`calculation_or_method` 和使用说明。

风险规则在 run 启动时必须固化快照，不能在执行中动态读取最新配置。快照至少保存 `rule_set_id`、`version`、`totals`、`rules[]` 和源文件信息，保证报告、回测和审计能复现当时的规则口径。

## 6. PDF 解析与文档块模型

MVP 阶段必须完整解析并完成索引后再启动 Agent。解析阶段可以给前端展示页数、OCR 页数、章节识别和表格抽取进度，但不能提前让 Agent 基于半截文档下结论。

PDF 解析产物的最小模型：

```text
document_blocks
- id
- run_id
- document_id
- page_number
- section_path
- block_type: text | table | image_ocr
- text
- bbox
- char_start / char_end
- table_html / table_json
- source_hash
- parser_version
- created_at
```

注意：

- `document_blocks` 是原文事实源。
- 向量库只保存 embedding 和可回指的 `block_id`。
- 不能只把 chunk 存在向量库里，否则报告截图、证据高亮和解析器版本追踪都会失去根基。

## 7. 证据三层模型

证据系统分为三层，避免把“原文事实”“本次引用”和“长期知识资产”混在一起。

### 7.1 document_blocks

PDF 解析后的原文事实块，只表达“原文里有什么”，不表达风险判断。

### 7.2 run_evidence

本次分析过程中被检索、引用、验证过的证据，绑定：

- `run_id`
- `risk_finding_id`
- `agent_name`
- `confidence`
- `verifier_status`
- `document_block_id`

报告只能引用 `supported`、`weakly_supported` 或 `needs_human_review` 的 run evidence。`unsupported` 只能留在审计日志中。

### 7.3 evidence_library

长期证据知识库，只接收经过门禁的证据。

可自动晋升：

- 官方招股书原文块。
- 被 Verifier 标记为 `supported` 的证据。
- 有完整 `source_run_id / document_id / page / section / bbox / hash` 的证据。
- 去重后未存在或版本更新的证据。

不自动晋升：

- 联网检索的非官方网页。
- LLM 总结文本。
- 用户私有备注。
- `weakly_supported` 或 `needs_human_review` 的证据。
- 无 bbox 或无法回溯原文的材料。

## 8. 检索与索引

MVP 也应实现最小 Hybrid Search，不建议只做向量检索。

原因是招股书中存在大量需要精确匹配的术语，例如：

- 对赌
- 赎回
- 回购义务
- 关联交易
- 重大不利变化
- 持续经营
- 客户集中度
- 供应商集中度

统一检索接口：

```text
retrieval.hybrid_search(query, filters) -> EvidenceCandidate[]
```

Agent、特征抽取和 Verifier 只调用统一检索接口，不直接依赖 Qdrant、PostgreSQL FTS 或 OpenSearch。

## 9. 风险特征与 Agent 输出

Agent 输出必须结构化。自然语言只能作为解释层或报告草稿，不能作为系统事实源。

专业 Agent 统一输出 `RiskFinding`：

```text
risk_findings
- id
- run_id
- agent_name
- rule_set_id
- rule_version
- rule_id
- dimension_code: D1 | D2 | D3
- rule_type: quantitative_financial | qualitative_disclosure | textual_nlp
- category
- title
- severity: low | medium | high
- confidence
- trigger_condition
- observed_value
- evaluation_result: triggered | not_triggered | inconclusive | not_evaluated
- reasoning_summary
- evidence_ids[]
- feature_ids[]
- counter_evidence_ids[]
- assumptions[]
- requires_human_review
- created_at
```

Verifier 输出：

```text
verification_results
- finding_id
- status: supported | weakly_supported | unsupported | needs_human_review
- checked_evidence_ids[]
- reason
- created_at
```

报告生成只读取：

- 已验证的 `risk_findings`。
- 对应的 `run_evidence`。
- run 启动时固化的风险规则快照。
- `analysis_run` 元数据。
- 可审计的 Agent 事件日志。

## 10. Agent 编排

MVP 使用 LangGraph，但图保持固定、可控、可观测。

推荐固定 DAG：

```text
orchestrator
-> financial_agent
-> legal_agent
-> business_agent
-> market_agent
-> verifier
-> reporter
```

其中 financial/legal/business/market 可以并行执行。市场 Agent 是可降级模块，缺少上市前市场数据时不阻塞财务、法务和经营分析。

MVP 不做：

- Agent 自主改图。
- 无限反思循环。
- 多轮复杂辩论。
- Agent 直接写全局知识库。
- Agent 绕过 provider 或 retrieval 接口直接调用 SDK。

## 11. Verifier 证据门禁

Verifier 是报告生成前的强制门禁。

门禁规则：

- `supported`：可以进入正式报告。
- `weakly_supported`：可以进入报告，但必须标注“证据较弱 / 需人工复核”。
- `unsupported`：不能进入正式风险结论，只能进入审计日志。
- `needs_human_review`：进入报告的“待复核事项”部分。

每个高风险结论至少需要一条强证据。每个专业 Agent 即使未发现风险，也要输出“未发现”记录和检索范围，避免无法区分“没跑”和“跑了但没发现”。

## 12. 报告与复核

报告生成后默认是草案，不代表人工批准。

推荐模型：

```text
reports
- id
- run_id
- status: generated | failed
- review_status: draft | pending_review | approved | rejected | revised
- markdown_uri
- html_uri
- pdf_uri
- report_revision
- created_at
```

MVP 默认 `review_status = draft`，前端展示“系统生成草案，待人工复核”。

正式环境再增加：

- reviewer_id
- review_comments
- approved_at
- 修订记录
- 导出水印与权限控制

## 13. 事件模型

后端事件流用于前端实时分析过程、决策回放和审计。记录粒度是“可审计摘要”，不是完整思维链。

推荐事件类型：

```text
stage_started
stage_completed
stage_failed
agent_started
agent_completed
tool_called
evidence_found
risk_finding_created
verification_completed
report_section_generated
```

事件表最小字段：

```text
analysis_events
- id
- run_id
- stage
- event_type
- actor_type: system | agent | tool | verifier | reporter
- actor_name
- title
- summary
- payload_json
- related_evidence_ids[]
- related_finding_ids[]
- created_at
```

注意：

- `tool_called` 记录工具名、参数摘要、耗时和状态，不记录密钥或完整大文本。
- `evidence_found` 记录 evidence_id、页码、章节和置信度。
- `risk_finding_created` 记录 finding_id、severity、rule_id 和 evaluation_result。
- 前端消费事件，不依赖 LangGraph 内部实现。

## 14. 数据时间边界

未上市企业分析必须显式防止未来数据泄漏。`data_cutoff_date` 是分析任务的一等字段。

推荐字段：

```text
analysis_runs
- data_cutoff_date
- expected_listing_date
- listing_date
```

数据接入校验：

- `market_data.as_of_date <= data_cutoff_date`
- `news_or_web_data.published_at <= data_cutoff_date`
- `document.disclosure_date <= data_cutoff_date`
- 上市后 1/5/20/60 日收益、破发标签、上市后价格不得进入主分析输入。

回测标签与主分析输入必须物理分离。上市后数据只用于验证、回测和监控，不用于当前未上市项目风险判断。

## 15. 外部能力 Provider

LLM、Embedding、OCR、对象存储等外部能力必须通过 provider 接口封装。

推荐边界：

```text
common/providers/
- llm.py
- embedding.py
- ocr.py
- object_store.py
```

调用原则：

- `agents/skills` 调用 `ModelClient`。
- `retrieval/indexing` 调用 `EmbeddingClient`。
- `parsing` 调用 `OCRClient`。
- `documents/reporting` 调用 `ObjectStoreClient`。

不要在业务模块里散落具体 SDK 调用。这样便于测试注入 fake provider、替换模型供应商、统一记录 token、耗时、错误和成本。

## 16. MVP 与正式环境技术选型

| 能力 | MVP 选型 | 正式环境选型 |
|---|---|---|
| API 服务 | FastAPI | FastAPI |
| 数据库 | PostgreSQL | 托管或高可用 PostgreSQL |
| ORM / 迁移 | SQLAlchemy 2.x + Alembic | SQLAlchemy 2.x + Alembic 迁移流水线 |
| 异步任务 | RQ + Redis | Celery 或 LangGraph Worker + Redis/PostgreSQL 状态持久化 |
| 文件存储 | MinIO | S3 / OSS / COS / 企业私有 MinIO |
| PDF 解析 | PyMuPDF + pdfplumber | PyMuPDF + pdfplumber + 版面服务优化 |
| OCR | PaddleOCR 可选兜底 | OCR 服务化 |
| 向量库 | Qdrant | Qdrant / Milvus |
| 全文检索 | PostgreSQL FTS 或轻量 BM25 | OpenSearch / Elasticsearch / PostgreSQL FTS |
| Hybrid Search | weighted score + section filter | BM25 + vector + section/page filter + rerank |
| Rerank | 预留接口，默认不开 | cross-encoder 或 LLM rerank |
| Agent 编排 | LangGraph 固定 DAG | LangGraph 可配置图 + Worker |
| LLM | OpenAI-compatible API，经 ModelClient 包装 | 多模型路由、限流、重试、成本记录 |
| Embedding | OpenAI-compatible 或本地 embedding，经 EmbeddingClient 包装 | embedding 版本管理与批量索引 |
| 实时事件 | 轮询 `GET /events?after=` | SSE / WebSocket + OpenTelemetry trace |
| 报告 | Markdown / HTML | Markdown / HTML / PDF + 审批流 |
| 证据晋升 | 官方招股书 supported evidence 自动晋升 | 审核流、去重聚类、来源可信度评分 |
| 回测 | 独立模块，手动导入标签 | 批处理 job + 规则/模型版本效果追踪 |
| 监控 | 独立模块，真实后端数据或手动导入数据 | 调度器 + 行情/公告接入 |

## 17. 初始核心表

MVP 初始表建议：

```text
analysis_runs
analysis_run_inputs
analysis_input_revisions
analysis_run_documents
documents
document_blocks
run_evidence
evidence_library
risk_findings
verification_results
analysis_events
reports
risk_rule_sets
```

这些表优先服务于可恢复、可审计、可追溯，不追求一次覆盖所有正式环境能力。

## 18. 实现风险与注意点

- URL 获取必须防 SSRF。至少禁止 localhost、内网 IP、云元数据地址，限制文件大小、MIME 类型和超时。
- 文件上传要基于 `file_sha256` 去重。
- 每个异步阶段要幂等，重复执行不能产生重复证据或重复报告。
- 规则版本、模型版本、parser 版本和 embedding 版本都要能回溯。
- Agent 不得绕过 Verifier 直接进入报告。
- 全局证据库写入应异步执行，失败不影响本次报告完成。
- 缺少市场数据时，市场模块降级，不阻塞整体分析。
- `failed` 状态必须保留失败阶段和错误摘要，便于前端提示和人工重试。
- 报告中的每个关键结论都必须绑定证据；缺证据只能标注“未发现”或“需人工复核”。
