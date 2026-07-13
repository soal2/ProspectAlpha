# ProspectAlpha 后端分析流程设计

本文档记录 ProspectAlpha 后端从“用户提出 IPO 风险分析问题”到“生成报告并沉淀证据”的实现层设计。当前目标是为多人协作开发建立统一契约；文中标注为设计契约的能力不等同于已经完成生产验证。

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
-> 战略 Agent 动态组建专家队
-> 专家 Agent 通过 A2A 与 JSON 邮箱协同分析
-> Verifier 证据门禁
-> 战略 Agent 汇总并生成报告
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
- 不允许专家 Agent 直接修改 LangGraph 生命周期、启动额外专家或形成无限反思循环；运行时专家拓扑只由战略 Agent 的已校验决策生成。
- 不把所有本次证据自动写入全局知识库。
- 不把回测和监控塞进单次分析主链路。
- 不把 PDF 二进制直接存进 PostgreSQL。
- 不记录完整 chain-of-thought，只记录可审计的事件摘要与结构化输入输出。

## 3. 信息完整性检查

用户在工作台先提交自然语言问题、招股书线索或附件。后端第一步不是直接分析，而是创建一个可审计的 draft/clarifying run。

硬阻塞信息只包括：

- 分析对象：公司名称、申请编号或明确的 IPO 项目线索，至少一个。
- 招股书材料：上传 PDF、可下载 URL 或已存在 `document_id`，至少一个。
- 用户确认：同意使用系统默认风险规则、默认分析深度，并允许战略 Agent 根据实时专家目录动态组队。

非阻塞信息可以使用默认值或后续补充：

- 风险规则版本。
- 分析深度。
- 人工专家选择只在战略输出无法通过校验或没有可用替补时作为恢复输入，不作为普通创建参数。
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
    "expert_selection": "dynamic_strategist"
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
-> agent_planning
-> agent_starting
-> agent_running
-> verifying
-> reporting
-> completed
```

异常与人工状态：

```text
waiting_user
awaiting_expert_selection
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

- `draft / clarifying / ready`：允许修改问题、材料、规则和分析深度；修改后重新执行完整性检查。
- `queued` 及之后：输入冻结；只能取消任务或创建新 run。
- `awaiting_expert_selection`：LangGraph 已通过 interrupt 暂停；只接受专家选择恢复请求，不解冻其余输入。

## 5. API 契约

推荐使用资源语义清晰的 REST 命名。

### 5.1 分析任务

```text
POST   /analysis-runs
PATCH  /analysis-runs/{run_id}/inputs
POST   /analysis-runs/{run_id}/start
POST   /analysis-runs/{run_id}/expert-selection
POST   /analysis-runs/{run_id}/cancel
GET    /analysis-runs/{run_id}
GET    /analysis-runs/{run_id}/events
GET    /analysis-runs/{run_id}/stream
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

完整分析必须在异步 worker 中执行，不能阻塞 HTTP 请求。`GET /events` 提供游标式事件查询；`GET /stream` 通过 SSE 推送同一事件模型。

`POST /analysis-runs/{run_id}/expert-selection` 只在 `status = awaiting_expert_selection` 时接受当前专家目录中的名称；暂停期间 `current_stage` 保持 `expert_orchestration`。恢复后继续使用原 `run_id` 和 LangGraph `thread_id`，不得新建 run 或绕过战略任务生成。

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
GET /analysis-options
GET /a2a/{agent_name}/.well-known/agent-card.json
POST /a2a/{agent_name}
```

`GET /analysis-options` 返回当前可用专家的名称、能力摘要、可用状态和 Agent Card URL，供战略层实时加载与人工恢复页面展示。普通分析创建请求不再携带 `selected_agent_names`。

每个可执行专家暴露 A2A 1.0 Agent Card，战略 Agent 是图内编排与邮箱协调者，不对外声明专家卡。`POST /a2a/{agent_name}` 使用 JSON-RPC 绑定，提供 `SendMessage`、`GetTask` 和 `ListTasks`；`CancelTask` 返回任务不可取消错误，streaming、subscription 与 push notification 未启用。当前 Agent Card 明确声明 `streaming = false`。所有请求携带 `A2A-Version: 1.0`。A2A 的公开数据模型保持为 `AgentCard`、`Task`、`Message`、`Artifact` 与 `Part`，Claude Code 风格的控制消息只放入 `metadata` 扩展，不新增私有顶层字段。

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

正式风险结论只能引用 `supported` 或 `weakly_supported` 的 run evidence。`needs_human_review` 仅进入“待人工补证事项”，`unsupported` 只能留在审计日志中。

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

MVP 使用 LangGraph 管理可恢复生命周期，但不保存任何固定专家边。图中固定的是协议阶段，专家名称、数量、模块和专家间消息拓扑都在运行时生成：

协议依据为 [A2A 1.0 规范](https://github.com/a2aproject/A2A/blob/main/docs/specification.md)与[官方 Python SDK](https://github.com/a2aproject/a2a-python)；本机团队生命周期参考 Claude Code 教学实现的 [Agent Teams](https://learn.shareai.run/zh/s15/)、[Team Protocols](https://learn.shareai.run/zh/s16/) 和 [Autonomous Agents](https://learn.shareai.run/zh/s17/)，并补足教学版未处理的并发安全与持久化约束。

```text
strategy_plan
-> validate_strategy
-> spawn_experts
-> await_expert_idle
-> dispatch_assignments
-> collect_module_results / replan_replacement
-> verify_evidence
-> synthesize_report
-> shutdown_team
```

LangGraph 使用 `run_id` 作为 `thread_id`。`LANGGRAPH_CHECKPOINT_BACKEND` 可指定 `sqlite` 或 `postgres`；未设置时根据 `DATABASE_URL` 自动选择，SQLite 文件保存为 `AGENT_RUNTIME_DATA_DIR/langgraph_checkpoints.sqlite`。动态并行由运行时专家线程 fan-out，人工恢复通过 `interrupt` / `Command`，而不是把专家写成静态 nodes/edges。

### 10.1 战略层动态选人

战略 Agent 的 System Prompt 必须包含显式变量 `AVAILABLE_EXPERTS_JSON`。变量在初始规划、每轮替补和人工恢复前从当前 `AgentSpecRepository` 重新生成，内容包含目录中全部可执行专家的 Agent Card URL、能力描述、输入输出模式与输出 schema；不得使用代码白名单或缓存的固定队伍代替实时目录。当前实现以“目录成员身份”表示可用性，尚无独立 active/inactive 字段。

战略输出必须先通过 Pydantic `StrategyDecision` 校验，再允许创建线程：

```text
StrategyDecision
- summary
- focus_areas[]
- selected_agents[]            # 唯一且全部存在于本次能力快照
- assignments[]: ExpertAssignment

ExpertAssignment
- module_id
- agent_name
- module
- goal
- expected_outputs[]
```

至少选择一位专家，每位被选专家有且仅有一个主模块，任务中的 `agent_name` 必须属于 `selected_agents`。非法 JSON、重复/未知专家、任务缺失或战略模型失败时不得静默回退到旧默认链；图进入 `awaiting_expert_selection` interrupt。恢复时保留已成功模块、evaluation、team state 与替补历史，只把用户选择的实时可用专家按 Agent Card 能力一一映射到未完成模块，然后合并新结果。

### 10.2 A2A 1.0 与 JSON 邮箱桥

跨 Agent 语义遵循官方 A2A 1.0：`context_id = run_id`；`Task.id` 是跨 run 唯一的服务端 opaque ID，业务 `module_id` 保留在任务 assignment/metadata 中。请求和结果使用 `Message`、`Task` 与 `Artifact`，每个 Agent 通过 Agent Card 声明能力。HTTP 边界使用官方 Python SDK 的 JSON-RPC dispatcher，避免修改协议 schema。

Claude Code 风格 JSON 邮箱承担同一共享文件系统内的跨线程和跨进程传输。进程内请求直接访问活动 `TeamRuntime`；API 与 RQ worker 分进程时通过同一 `team.json`、JSONL 邮箱与 `JsonTaskStore` 协作。每个 run 使用隔离目录：

```text
{AGENT_RUNTIME_DATA_DIR}/{run_id}/
├── .team-runtime.lock          # 单 run 跨进程独占锁
├── .locks/                     # 每个收件箱的文件锁
├── team.json
├── inboxes/{agent_name}.jsonl
├── cursors/{agent_name}.json
├── tasks/                      # A2A TaskStore 持久化记录
└── dead-letter/{agent_name}.jsonl
```

邮箱每行承载一个可验证的 A2A `Message`，并通过 `TaskStore` 固化 `Task` / `Artifact`。`task`、`result`、`peer_request` / `peer_response`、`idle_notification`、`shutdown_request`、`shutdown_approved` 与 `shutdown_rejected` 等团队控制类型写入 A2A `metadata`。Task result 必须匹配 assigned agent 与原任务 `message_id/in_reply_to`；peer 和 shutdown 响应同时校验 `request_id`、预期发送方与 `in_reply_to`。实现使用文件锁、append 后 `flush/fsync`、持久化 claim/consumer/lease、处理期续租、原子 cursor 与 `message_id` 去重，提供至少一次投递；并发消费者不能同时 claim 同一消息，崩溃后的过期 claim 可重投。同一 run 另有进程级 TeamRuntime 独占锁，避免重复 RQ job 同时覆写 `team.json`。损坏、伪造或未知消息进入 dead-letter。

运行目录默认为 `./data/agent_runtime`，可由 `AGENT_RUNTIME_DATA_DIR` 覆盖。API 与 RQ worker 在容器部署时必须共享同一 `agent_runtime_data` volume；目录权限 `0700`、文件权限 `0600`。邮箱只保留任务、finding、evidence ID 和必要摘要，不复制完整招股书、模型密钥或 chain-of-thought。

### 10.3 专家线程生命周期与自主通信

每个被选专家运行在独立的非 daemon 线程，拥有独立 handler/任务上下文；模型化专家实现还必须加载专属 System Prompt。初始化后先加载自己的 Agent Card、团队成员与任务协议，再向战略 Agent 发送 `idle_notification`。线程按 500ms 间隔轮询自己的 JSON 邮箱：

```text
START -> IDLE -> WORK -> RESULT -> IDLE -> SHUTDOWN
```

收到任务后，专家输出 `ExpertTaskOutput` Artifact，其中包含结构化 `evaluations[]` 与 `ExpertModuleResult`。模块结果记录摘要、命中规则 ID、规则覆盖数、局限性和 peer message IDs；下游流水线再把 evaluations 转换为 `RiskFinding`、生成原文证据并关联 finding/evidence ID。即使未发现风险，也必须报告检索覆盖和“未发现重大风险”。专家可向本次已启动的其他专家发送带 `request_id` 的 A2A peer request/response，同一任务最多 3 轮，不得自行启动新的专家线程。

任务默认超时 300 秒。超时 Task 会持久化为 `FAILED`，迟到结果被拒绝；专家主线程可在处理期响应关机，避免超时 handler 阻断团队清理。专家启动、初始化、投递、执行或结构化输出失败时，战略层携带失败模块、原因、已尝试专家和实时剩余能力重新规划。系统严格校验替补者必须来自实时目录且未被该模块尝试，战略输出负责根据 Agent Card 能力选择匹配专家。同一模块最多替补 2 轮；仍失败或无可用替补则进入人工选择 interrupt，不生成缺模块报告。

运行结束或暂停清理前，战略层向全部存活线程发送 `shutdown_request`，等待 `shutdown_approved` / `shutdown_rejected`，随后 join 并记录 `teammate_terminated`。启动与关机握手各默认 10 秒，禁止遗留后台线程。

### 10.4 边界约束

- 专家不得直接写全局知识库或绕过 provider / retrieval 接口调用 SDK。
- 专家不得绕过 Verifier 将 finding 写入最终报告。
- 战略层不得根据未验证的自然语言摘要新增事实或证据。
- 运行时可以动态选择和替补专家，但协议生命周期、超时、证据门禁和最大协作轮数不可由 Agent 修改。

## 11. Verifier 证据门禁

Verifier 是报告生成前的强制门禁。

门禁规则：

- `supported`：可以进入正式报告。
- `weakly_supported`：可以进入报告，但必须标注“证据较弱 / 需人工复核”。
- `unsupported`：不能进入正式风险结论，只能进入审计日志。
- `needs_human_review`：不进入正式风险结论；仅在报告的“待人工补证事项”与审计轨迹中显示。

每个高风险结论至少需要一条强证据。每个专业 Agent 即使未发现风险，也要输出“未发现”记录和检索范围，避免无法区分“没跑”和“跑了但没发现”。

## 12. 报告与复核

Verifier 完成全部模块门禁后，战略 Agent 接收已验证模块结果与证据索引，统一生成 `IntegratedRiskReport`。它负责消除重复、解释跨模块关联并统一风险等级，但不得新增没有 finding/evidence 支撑的事实。

最终结构至少覆盖执行摘要、全部专家模块、跨模块关联、综合等级、证据索引、局限性和人工复核建议。系统在渲染前再次校验：每个成功模块均出现、所有 finding/evidence ID 已知、`unsupported` finding 未进入正式结论。结构化输出失败允许战略 Agent 纠正一次；仍失败或模块未收齐时 run 失败，不交付伪完整报告。

报告生成后默认是草案，不代表人工批准。MVP 对终端用户交付 Markdown/HTML；PDF 导出仍属于后续能力。

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
strategy_plan_generated
strategy_validation_failed
expert_selection_required
expert_replacement_planned
agent_started
agent_idle
agent_completed
a2a_message_sent
a2a_message_received
team_shutdown_completed
tool_called
evidence_found
risk_finding_created
verification_completed
report_section_generated
report_synthesized
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
- A2A 事件只记录消息类型、发送方、接收方、message/task/request ID 与状态摘要，不复制完整邮箱内容。
- `expert_selection_required` 发出后，SSE 发送 `paused` 并关闭；用户恢复 run 后前端重新连接。
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
| Agent 编排 | LangGraph 动态战略图 + SQLite checkpointer | LangGraph 动态战略图 + Postgres checkpointer / Worker |
| Agent 通信 | A2A 1.0 + 共享文件系统 JSONL 邮箱 | A2A 1.0 + 对象/消息服务后端、完整鉴权与追踪 |
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
- 战略输出未通过结构校验时不得启动任何专家；不得恢复旧固定队伍作为静默兜底。
- Agent 不得绕过 Verifier 直接进入报告。
- 全局证据库写入应异步执行，失败不影响本次报告完成。
- 某模块缺少输入时由战略层缩小任务，或从实时未尝试候选中选择能力合适的替补专家；不得通过硬编码跳过某个具名 Agent。
- JSON 邮箱必须使用 run 隔离、文件锁、原子 cursor 和消息去重；API 与 worker 未共享运行目录时不得启动跨进程团队。
- `failed` 状态必须保留失败阶段和错误摘要，便于前端提示和人工重试。
- 报告中的每个关键结论都必须绑定证据；缺证据只能标注“未发现”或“需人工复核”。
