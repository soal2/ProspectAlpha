# ProspectAlpha 后端实现路线图

本文档把后端分析流程设计拆成可分工、可验证的实施阶段。路线图默认遵守根目录 `AGENTS.md`：模块内实现优先，跨模块改动必须说明影响边界。

## 1. 总体交付策略

推荐顺序：

```text
Phase 1：分析任务、补全问答、文档资产、状态机
Phase 2：PDF 解析、原文块入库、Hybrid Search 索引
Phase 3：风险特征、LangGraph Agent、Verifier、报告草案
Phase 4：证据晋升、回测、监控、正式化加固
```

## 2. 技术选型基线

### 2.1 MVP

| 能力 | 选型 |
|---|---|
| API 服务 | FastAPI |
| 数据库 | PostgreSQL |
| ORM / 迁移 | SQLAlchemy 2.x + Alembic |
| 异步任务 | RQ + Redis |
| 文件存储 | MinIO |
| PDF 解析 | PyMuPDF + pdfplumber |
| OCR | PaddleOCR 可选兜底 |
| 向量库 | Qdrant |
| 全文检索 | PostgreSQL FTS 或轻量 BM25 |
| Agent 编排 | LangGraph 固定 DAG |
| LLM / Embedding | OpenAI-compatible API，经 provider 封装 |
| 可观测 | OpenTelemetry + 自建 trace 表或 LangSmith 思路 |
| 实时事件 | 前端轮询 `GET /analysis-runs/{run_id}/events?after=` |
| 报告 | Markdown / HTML 草案 |
| 回测与监控 | 批处理 job、调度器、行情/公告数据接入 |

### 2.2 正式环境

| 能力 | 选型 |
|---|---|
| API 服务 | FastAPI |
| 数据库 | 托管或高可用 PostgreSQL |
| 异步任务 | Celery 或 LangGraph Worker + Redis/PostgreSQL 状态持久化 |
| 文件存储 | S3 / OSS / COS / 企业私有 MinIO |
| 大文件上传 | 预签名 URL + 分片上传 |
| URL 获取 | 下载沙箱 + 白名单 + 文件嗅探 + 恶意文件扫描 |
| 向量库 | Qdrant / Milvus |
| 全文检索 | OpenSearch / Elasticsearch / PostgreSQL FTS |
| Rerank | cross-encoder 或 LLM rerank |
| Agent 编排 | LangGraph 可配置图 |
| 报告 | Markdown / HTML / PDF + 审批流 |
| 证据晋升 | 审核流、去重聚类、权限分级、来源可信度评分 |


## 3. Phase 1：任务主干与补全问答

目标：前端工作台能调用真实后端完成“用户输入 -> 信息完整性检查 -> 补全 -> ready -> start -> queued”。

### 3.1 模块范围

涉及模块：

- `api/analysis.py`
- `api/documents.py`
- `common/config.py`
- `common/exceptions.py`
- 新增 `models/`
- 新增 `schemas/`
- 新增 `services/analysis_runs.py`
- 新增 `services/documents.py`
- 新增 `workers/`
- 新增 `providers/object_store.py`
- 新增 Alembic 迁移目录

### 3.2 主要任务

1. 建立数据库与迁移基础。
2. 定义 `analysis_runs`、`analysis_run_inputs`、`analysis_input_revisions`。
3. 定义 `documents` 与 `analysis_run_documents`。
4. 实现 `POST /analysis-runs`：创建 draft/clarifying run 并返回缺失问题。
5. 实现 `PATCH /analysis-runs/{run_id}/inputs`：补全输入并重新检查。
6. 实现 `POST /analysis-runs/{run_id}/start`：校验 ready、冻结输入、固化规则快照、投递异步任务。
7. 实现 `GET /analysis-runs/{run_id}` 和 `GET /analysis-runs/{run_id}/events`。
8. 实现 `POST /documents` 的最小 PDF 上传。
9. 实现对象存储 provider，MVP 指向 MinIO。
10. 建立 `analysis_events` 写入能力。

### 3.3 信息完整性检查规则

硬阻塞：

- 分析对象至少一个：公司名称、申请编号、IPO 项目线索。
- 招股书材料至少一个：`document_id`、上传 PDF、可下载 URL。
- 用户确认默认配置：风险规则、Agent 队伍、分析深度。

默认值：

- 风险规则：当前 active 版本，默认 `hk_ipo_prospectus_risk_framework_v1`。
- 分析深度：`deep`。
- Agent 队伍：默认专家组。
- `data_cutoff_date`：默认 run 创建日期。

### 3.4 验收标准

- 可以创建一个 `clarifying` run，并返回 `blocking_questions`。
- 补齐材料后 run 进入 `ready`。
- `start` 后 run 进入 `queued`，输入被冻结。
- `queued` 后再次修改 inputs 返回明确错误。
- 事件表能记录 run 创建、补全、启动投递事件。
- PDF 上传后能得到 `document_id`、`file_sha256` 和 `storage_uri`。

### 3.5 验证命令

```bash
cd backend
pytest
python -m compileall src
```

如果数据库和 MinIO 通过 Docker 启动，再补充：

```bash
docker compose up -d postgres redis minio
```

具体服务名以 `backend/docker-compose.yml` 最终定义为准。

## 4. Phase 2：PDF 解析与证据索引

目标：一份主招股书能够完整解析成原文块，写入数据库，并建立最小 Hybrid Search 索引。

### 4.1 模块范围

涉及模块：

- `ingestion/prospectus.py`
- `parsing/pdf_parser.py`
- `parsing/section_detector.py`
- `parsing/table_parser.py`
- `retrieval/vector.py`
- `retrieval/full_text.py`
- `retrieval/hybrid.py`
- `retrieval/evidence_store.py`
- `providers/embedding.py`
- `providers/ocr.py`

### 4.2 主要任务

1. 定义 `document_blocks` 表。
2. 实现主招股书解析 worker 阶段：`ingesting -> parsing`。
3. 使用 PyMuPDF 提取文本、页码、bbox。
4. 使用 pdfplumber 抽取表格。
5. 实现章节识别，生成 `section_path`。
6. 对扫描页或图片页预留 OCR 兜底。
7. 生成 `source_hash` 和 `parser_version`。
8. 写入 `document_blocks`。
9. 向 Qdrant 写入 embedding，payload 回指 `block_id`。
10. 建立 PostgreSQL FTS 或轻量 BM25 索引。
11. 实现 `retrieval.hybrid_search(query, filters)`。
12. 解析和索引阶段持续写 `analysis_events`。

### 4.3 关键约束

- 必须完整解析和索引后才能进入 Agent 阶段。
- 向量库不是事实源，事实源是 `document_blocks`。
- 每个证据候选必须能回到 `document_id / page_number / section_path / bbox / text`。
- PDF 解析失败时 run 停在 `failed`，记录 `failed_stage = parsing`。

### 4.4 验收标准

- 给定一份 PDF，数据库中能看到稳定的 `document_blocks`。
- 同一 PDF 重复上传能通过 `file_sha256` 去重。
- Hybrid Search 能按关键词和语义召回候选块。
- 候选块包含页码、章节和 bbox。
- 解析事件能在前端实时页展示为阶段进度。

### 4.5 验证命令

```bash
cd backend
pytest tests/parsing tests/retrieval
python -m compileall src
```

建议增加一个小型公开 PDF fixture。不要把非公开招股书提交到仓库。

## 5. Phase 3：风险特征、Agent、Verifier 与报告

目标：完成第一条真正的端到端风险分析闭环，生成带证据的 Markdown/HTML 报告草案。

### 5.1 模块范围

涉及模块：

- `features/financial.py`
- `features/operating.py`
- `features/legal.py`
- `features/market.py`
- `agents/orchestrator.py`
- `agents/financial.py`
- `agents/legal.py`
- `agents/business.py`
- `agents/market.py`
- `agents/verifier.py`
- `agents/reporter.py`
- `skills/risk_extract.py`
- `skills/evidence_verify.py`
- `skills/report_generate.py`
- `prediction/scorecard.py`
- `reporting/markdown.py`
- `reporting/html.py`
- `providers/llm.py`

### 5.2 主要任务

1. 定义 `risk_rule_sets`，从 `backend/configs/hk_ipo_risk_rules_v1.json` 载入 active 版本，并支持 run 级规则快照。
2. 定义 `run_evidence`、`risk_findings`、`verification_results`、`reports`。
3. 实现 `extracting_features` 阶段，按规则维度拆分执行：D1 财务指标、D2 非标隐性风险、D3 文本粉饰度。
4. 实现 LangGraph 固定 DAG：
   - orchestrator
   - financial_agent
   - legal_agent
   - business_agent
   - market_agent
   - verifier
   - reporter
5. 每个 Agent 只输出结构化 `RiskFinding`，并写入 `rule_set_id`、`rule_version`、`rule_id`、`dimension_code`、`rule_type`、`category` 和 `evaluation_result`。
6. Verifier 校验证据存在性、证据支持关系和报告准入状态。
7. Reporter 只读取 verified finding 和 run evidence。
8. 生成 Markdown/HTML 报告草案。
9. 写入 Agent、工具调用、证据发现、风险发现、验证、报告生成事件。

### 5.3 风险规则覆盖

规则库 active 版本为 `hk_ipo_prospectus_risk_framework_v1`，由 `港股IPO招股书风险识别框架_7.3.xlsx` 解析生成，落地文件为 `backend/configs/hk_ipo_risk_rules_v1.json`。

| 维度 | 类型 | 一级分类数 | 规则数 | 首批实现重点 |
|---|---:|---:|---:|---|
| 维度1：标准化财务指标风险 | `quantitative_financial` | 7 | 48 | 盈利能力、现金流、偿债能力、资本结构 |
| 维度2：非标隐性风险 | `qualitative_disclosure` | 11 | 56 | 客户/供应商依赖、关联交易、对赌/赎回条款、政策监管、持续经营 |
| 维度3：文本粉饰度风险 | `textual_nlp` | 8 | 39 | 信息披露完整性、前后一致性、语言模糊性、免责声明密度 |

MVP 不再维护单独硬编码的 10 条风险清单。首批实现可以只覆盖高价值分类，但必须从规则 JSON 中筛选规则并保留完整规则快照；未实现的规则输出 `not_evaluated` 或保留在规则展示层，不伪造成已执行结论。

市场相关风险在缺少上市前市场窗口数据时降级，不阻塞整体报告。当前 Excel 规则以招股书风险识别为主，上市前市场窗口和近期 IPO 破发率作为后续发行/市场扩展规则集接入。

### 5.4 报告准入规则

- `supported` 风险发现进入正式风险结论。
- `weakly_supported` 标注证据较弱或需人工复核。
- `unsupported` 只能进入审计日志。
- `needs_human_review` 进入待复核事项。
- 报告默认 `review_status = draft`。

### 5.5 验收标准

- 完成一个 run 的端到端状态流转：`queued -> completed`。
- `GET /risk-rules` 能展示 active 规则集的 3 个维度、26 个一级分类、143 条规则。
- `POST /analysis-runs/{run_id}/start` 能把 active 规则完整固化到 run 级快照。
- 报告中每个关键风险结论都能回到 `run_evidence`。
- 每个风险发现都能回到 `rule_id` 和规则快照。
- 高风险结论至少绑定一条强证据。
- 前端实时分析页能看到阶段、Agent、证据、风险发现与报告事件。
- 市场数据缺失时 run 仍能完成，并在报告中明确说明市场模块降级。

### 5.6 验证命令

```bash
cd backend
pytest tests/features tests/agents tests/reporting
python -m compileall src
```

端到端验证建议使用一份公开样本 PDF 和固定 fake LLM provider，确保测试可重复。

## 6. Phase 4：证据晋升、回测与监控

目标：把单次分析产物沉淀为长期资产，并让回测和监控作为独立消费方使用分析结果。

### 6.1 证据晋升

主要任务：

1. 定义 `evidence_library`。
2. 实现 `evidence_promotion_job`。
3. 自动晋升官方招股书中的 supported evidence。
4. 对非官方网页、弱证据、人工备注保持 run 级保存，不自动晋升。
5. 记录 `source_run_id`、`document_id`、`block_id`、hash 和 parser 版本。

MVP 验收：

- 报告完成后异步晋升合格证据。
- 晋升失败不影响 `analysis_run.completed`。
- 全局证据可以回溯到来源 run 和原文块。

正式环境增强：

- 人工审核流。
- 去重聚类。
- 来源可信度评分。
- 权限与数据分级。
- 证据过期和版本管理。

### 6.2 回测中心

回测不进入单次主分析链路。

MVP：

- 读取已完成 `analysis_run`、`risk_findings` 和手动导入的上市后标签。
- 评估规则版本和风险发现效果。
- 输出基础指标：AUC、Precision、Recall、TopK、分组收益。

正式环境：

- 批量回测 job。
- 规则版本与模型版本效果追踪。
- SHAP 或特征重要性分析。
- 回测报告归档。

### 6.3 风险监控

监控是持续任务，不是一份报告的一部分。

MVP：

- 使用真实后端数据或手动导入数据展示监控中心。
- 不阻塞主分析报告。

正式环境：

- 调度器拉取公告、行情、新闻。
- 对已分析公司触发后续预警。
- 与原始分析报告建立复盘关系。

## 7. 横向工程要求

### 7.1 幂等与重试

每个 worker 阶段都要幂等：

- 重复解析不能重复创建同一批 blocks。
- 重复索引不能产生重复向量。
- 重复报告不能覆盖已批准报告。
- 重复证据晋升不能污染 evidence_library。

### 7.2 审计与可观测

每个阶段至少记录：

- started
- completed
- failed
- duration
- error_summary

LLM 和 embedding 调用还要记录：

- provider
- model
- token 或字符量
- latency
- retry_count
- cost 估算，可后续补

### 7.3 安全边界

必须注意：

- URL fetch 防 SSRF。
- 上传文件限制大小和 MIME。
- 不提交密钥、私有招股书、生成报告和非公开数据。
- 事件日志不记录完整敏感输入和密钥。
- 全局证据库写入前做权限与来源检查。

### 7.4 数据时间边界

主分析必须遵守 `data_cutoff_date`：

- 上市后个股表现不能进入主分析输入。
- 回测标签与分析输入物理分离。
- 报告记录数据截止日期。
- 市场 Agent 只使用上市前市场和同业窗口数据。

## 8. 建议目录演进

当前 `backend/src/riskagent/` 已经按模块建立骨架。实现时建议逐步补充：

```text
src/riskagent/
  models/
  schemas/
  services/
  workers/
  providers/
  repositories/
  migrations/ 或 backend/alembic/
```

建议职责：

- `models/`：SQLAlchemy ORM。
- `schemas/`：Pydantic 请求/响应模型。
- `services/`：应用服务，承载 use case。
- `repositories/`：数据访问封装。
- `workers/`：异步任务入口。
- `providers/`：外部能力适配。
- `api/`：FastAPI 路由，保持薄层。

不要把业务逻辑塞进 `api/` 或 `common/`。

## 9. 推荐实施顺序

### 第一批 PR

范围：

- SQLAlchemy / Alembic 基础。
- `analysis_runs`、`analysis_events`。
- `POST /analysis-runs`、`PATCH /inputs`、`POST /start`。
- fake worker 把 run 从 `queued` 推到 `completed`，只用于验证状态机，不生成假业务结论。

验收：

- 前端能从工作台拿到真实 `run_id`。
- 补全问答和状态查询可用。
- 状态事件可回放。

### 第二批 PR

范围：

- `documents` 上传。
- MinIO provider。
- `analysis_run_documents`。
- PDF sha256 去重。

验收：

- 用户上传 PDF 后能绑定到 run。
- start 前必须有 primary prospectus。

### 第三批 PR

范围：

- PDF 解析。
- `document_blocks`。
- Hybrid Search 最小实现。

验收：

- 一份样本 PDF 可解析、可检索、可回指原文页码。

### 第四批 PR

范围：

- 风险规则加载、展示和 run 级快照。
- 首批规则驱动风险特征。
- 结构化 `RiskFinding`。

验收：

- `GET /risk-rules` 返回 3 个维度、26 个一级分类、143 条规则。
- 至少输出若干个带 `rule_id` 和证据引用的结构化发现。

### 第五批 PR

范围：

- LangGraph 固定 DAG。
- Verifier。
- Markdown/HTML 报告。
- 实时事件对接前端分析页。

验收：

- 单个 run 端到端完成，生成草案报告。

### 第六批 PR

范围：

- 证据晋升 job。
- 回测中心真实数据入口。
- 监控中心真实数据入口。

验收：

- 单次分析结果能被证据库、回测、监控独立消费。

## 10. 交付检查清单

每个后端 PR 都应说明：

- 影响模块。
- 新增或变更的 API。
- 新增或变更的数据表。
- 是否影响证据追溯。
- 是否影响模型或 Agent 行为。
- 是否触及报告生成。
- 验证命令与结果。
- 已知 TODO。

每个阶段完成时至少验证：

```bash
cd backend
pytest
python -m compileall src
```

如果涉及前端联调，再验证：

```bash
cd frontend
bun run lint
bun run build
```

## 11. 当前已确认决策摘要

- `analysis_run` 是全流程根实体。
- 硬阻塞信息最小化：分析对象、招股书材料、默认配置确认。
- 使用可恢复状态机和 `analysis_events`。
- MVP 完整解析和索引后再启动 Agent。
- `document_blocks` 强制保存 page、section、bbox、text、block_type、source_hash。
- 证据分为 `document_blocks`、`run_evidence`、`evidence_library` 三层。
- Agent 输出必须结构化，报告不能直接依赖长文本回答。
- 风险规则 active 版本来自 `backend/configs/hk_ipo_risk_rules_v1.json`，按 run 固化快照。
- `POST /analysis-runs` 创建 draft/clarifying run 并执行信息检查。
- 分析执行必须异步化。
- 文件本体存对象存储，元数据存 PostgreSQL。
- MVP 使用最小 Hybrid Search。
- MVP 使用 LangGraph 固定 DAG。
- 市场风险模块可降级。
- Verifier 是报告前强制门禁。
- 合格证据选择性晋升到全局知识库。
- 事件记录可审计摘要，不保存完整思维链。
- start 前可改输入，start 后冻结。
- PDF 上传和 URL 统一为 `document_asset`。
- MVP 每个 run 一份主招股书，模型预留多附件。
- 报告生成后默认待人工复核。
- 回测与监控是独立消费方，不进入单次分析主链路。
- API 使用资源语义命名。
- MVP 直接使用 PostgreSQL + SQLAlchemy + Alembic。
- 外部能力通过 provider 封装。
- `data_cutoff_date` 是一等字段，防止未来数据泄漏。
