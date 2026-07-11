# ProspectAlpha Backend API

Base URL defaults to `http://localhost:8000`. Frontend configuration uses:

- `VITE_API_BASE_URL`: backend base URL

All JSON endpoints use `Content-Type: application/json` unless noted.

## Agents

### `GET /agents`

Returns the database-backed Agent expert directory used by the frontend expert guild pages.
Empty databases are initialized with the bundled expert seed data before the response is produced.
The dynamic strategist consumes executable expert capabilities through `/analysis-options` and each expert's Agent Card; this presentation directory is not a fixed execution chain.

Response item:

```json
{
  "id": "L3-001",
  "name": "沈北辰",
  "role": "首席指挥官 · 总监",
  "layer": "L3",
  "desc": "统筹全局，拆解风险维度，组建专家队、终审报告并签发。",
  "expertise": ["策略", "总控", "报告"],
  "callCount": 312,
  "avgTokens": 4820,
  "avatar": "沈",
  "accent": "#0b243d"
}
```

## Agent-to-Agent (A2A)

The following is the A2A 1.0 public surface for each executable expert in the current catalog. Routes resolve the catalog on every request, so additions, capability updates, and removals do not require an API restart. The strategist remains an internal LangGraph/mailbox coordinator. The collaboration runtime uses the official SDK JSON-RPC dispatcher; Agent Cards declare `streaming: false`.

### `GET /a2a/{agent_name}/.well-known/agent-card.json`

Returns the current A2A Agent Card, including the agent identity, capability description, skills, supported input/output modes, streaming support and JSON-RPC interface URL. Unknown catalog names return `404`; card discovery does not require an active analysis run.

### `POST /a2a/{agent_name}`

Accepts A2A 1.0 JSON-RPC requests. Clients send `A2A-Version: 1.0` and `Content-Type: application/json`. Supported core methods are:

- `SendMessage`
- `GetTask`
- `ListTasks`

`CancelTask` is recognized but returns the standard not-cancelable error because an in-flight mailbox expert cannot be interrupted safely. Streaming, task subscription, push notifications, and extended Agent Cards are not enabled.

A2A protocol failures use the JSON-RPC `error` envelope (normally with HTTP `200`), including a version-not-supported error when `A2A-Version` is absent or not `1.0`.

The canonical payloads are `Message`, `Task`, `Artifact` and `Part`. For an analysis run, `contextId` maps to `run_id`; the server generates a globally unique opaque `Task.id`, while the business `module_id` remains in assignment/metadata. Omit `message.taskId` to create a task; a supplied task ID must already exist or returns `TaskNotFound`. Repeating the same `messageId` returns the existing task rather than executing it twice. Internal team controls such as `task`, `idle_notification`, peer request/response, and shutdown handshakes are carried in message `metadata`, not as non-standard top-level A2A fields.

## Health

### `GET /health`

Returns runtime health.

Response:

```json
{ "status": "ok" }
```

## Analysis Options

### `GET /analysis-options`

Returns the active rule-set, current executable expert catalog, and supported analysis depths. The catalog is used both by the strategist's `AVAILABLE_EXPERTS_JSON` prompt variable and by the manual-selection recovery UI.

Response fields:

- `risk_rule_sets[]`: `{ rule_set_id, version, name, rule_count, active }`
- `agents[]`: `{ agent_name, title, description, capabilities[], rule_types[], categories[], available, agent_card_url, input_modes[], output_modes[], output_schema }`
- `analysis_depths[]`: `{ depth, label, max_findings }`

No returned agent is implicitly part of a fixed team. The strategist chooses the run-specific set after analysis starts.

## Documents

### `POST /documents`

Creates a document asset from plain text.

Request body:

```json
{
  "filename": "prospectus.txt",
  "content_text": "招股书正文...",
  "source_url": "https://example.com/prospectus"
}
```

### `POST /documents/upload`

Uploads and parses a PDF or text document.

Request: `multipart/form-data`

- `file`: required PDF or text file, max 50 MB
- `source_url`: optional source URL

### `GET /documents/{document_id}`

Returns a document asset by id.

Document response fields:

- `document_id`
- `filename`
- `content_type`
- `file_sha256`
- `storage_uri`
- `file_size_bytes`
- `page_count`
- `text_chars`
- `block_count`
- `source_url`
- `created_at`

Errors:

- `400`: invalid upload or unsupported file type
- `404`: document not found

## Risk Rules

### `GET /risk-rules`

Returns the active risk-rule set.

Response fields:

- `rule_set_id`
- `version`
- `name`
- `source_workbook`
- `source_main_sheet`
- `source_summary_sheet`
- `scope`
- `usage_notes[]`
- `totals`: `{ dimension_count, category_count, rule_count }`
- `dimensions[]`: dimension/category rollups
- `rules[]`: risk rules

### `GET /risk-rules/sets`

Returns available rule sets. The current backend returns the active YAML rule set only.

### `GET /risk-rules/{rule_id}`

Returns one rule.

### `POST /risk-rules`

Creates a rule.

Request body:

```json
{
  "dimension_code": "D1",
  "dimension": "维度1：标准化财务指标风险",
  "rule_type": "quantitative_financial",
  "category": "现金流",
  "risk_item": "经营现金流连续为负",
  "description": "经营活动现金流持续为负。",
  "trigger_condition": "经营现金流连续2期为负",
  "calculation_or_method": "读取现金流量表经营活动现金流净额",
  "source_sections": "现金流量表 / 财务资料章节",
  "enabled": true
}
```

### `PATCH /risk-rules/{rule_id}`

Partially updates a rule. Body accepts any subset of the create fields.

### `DELETE /risk-rules/{rule_id}`

Deletes a rule. Returns `204 No Content`.

Errors:

- `404`: rule not found

## Analysis Runs

### `POST /analysis-runs`

Creates an analysis draft. If required inputs are complete, the run becomes `ready`; otherwise it becomes `clarifying`.

Request body:

```json
{
  "company_name": "待确认公司",
  "document_text": "招股书正文或补充文本",
  "document_id": "doc_xxx",
  "material_url": "https://example.com/prospectus",
  "ipo_project_hint": "拟上市代码或项目备注",
  "confirm_defaults": true,
  "risk_rule_set_id": "hk_ipo_prospectus_risk_framework_v1",
  "analysis_depth": "standard",
  "structured_data": { "industry": "金融科技" },
  "max_findings": 30
}
```

### `GET /analysis-runs`

Lists analysis runs.

### `GET /analysis-runs/{run_id}`

Returns one run.

### `PATCH /analysis-runs/{run_id}/inputs`

Updates draft inputs before the run starts. Once started, inputs are frozen.

### `POST /analysis-runs/{run_id}/start`

Starts a `ready` run, freezes inputs and dispatches the background worker.

The worker loads the current expert catalog, injects it into the strategist prompt as `AVAILABLE_EXPERTS_JSON`, validates the resulting `StrategyDecision`, then dynamically starts only the selected experts. Replacement and manual-resume decisions refresh the catalog before validation. An invalid strategy does not fall back to a default chain; the run pauses at `awaiting_expert_selection`.

### `POST /analysis-runs/{run_id}/expert-selection`

Resumes a run paused at `awaiting_expert_selection`.

Request body:

```json
{
  "agent_names": ["financial_agent", "legal_agent"]
}
```

All names must be unique and currently available. If no valid `strategy_plan` exists, the system derives one assignment per selected expert from its live capability payload. When recovering failed modules, exactly one expert must be selected per unfinished module; completed results, evaluations, team state, and replacement history are preserved, and the selected experts are mapped to pending modules by capability score. Both paths resume the existing LangGraph `thread_id = run_id`; inputs remain frozen. Calling this endpoint in any other state returns `409`.

### `GET /analysis-runs/{run_id}/events?after=0`

Returns run events after the numeric offset.

Response:

```json
{
  "run_id": "run_xxx",
  "events": []
}
```

### `GET /analysis-runs/{run_id}/stream`

Streams analysis events as Server-Sent Events. Terminal runs end with a `done` event. A run paused for human expert selection emits `paused` with reason `awaiting_expert_selection` and closes; after `expert-selection` succeeds, clients open a new stream for the same `run_id`.

### `GET /analysis-runs/{run_id}/evidence`

Returns source-grounded evidence snippets for the run.

### `GET /analysis-runs/{run_id}/findings`

Returns run findings.

Response:

```json
{
  "run_id": "run_xxx",
  "findings": []
}
```

### `GET /analysis-runs/{run_id}/report`

Returns generated report draft.

### `GET /analysis-runs/{run_id}/trace`

Returns trace view data for decision replay.

Analysis run response fields:

- `run_id`
- `company_name`
- `status`
- `current_stage`
- `failed_stage`
- `error_summary`
- `created_at`
- `completed_at`
- `data_cutoff_date`
- `rule_snapshot`
- `analysis_config`
- `input_summary`
- `understood`
- `blocking_questions`
- `optional_questions`
- `assumed_defaults`
- `inputs_frozen`
- `strategy_plan`
- `team_state[]`
- `interrupt_reason`
- `replacement_history[]`
- `module_results[]`
- `orchestra_plan` (read-only compatibility alias for `strategy_plan`)
- `findings[]`
- `events[]`

Errors:

- `400`: invalid create payload
- `422`: invalid FastAPI request/structured payload
- `404`: run/report not found
- `409`: invalid state transition or rejected expert selection, such as starting a non-ready run, editing frozen inputs, submitting duplicate/unknown experts, or resuming a run that is not awaiting expert selection

## Frontend Coverage Notes

The current backend exposes no dedicated routes for backtest jobs, global evidence search, homepage examples, or dashboard KPI snapshots. The frontend real-data mode therefore derives dashboard, homepage, expert, and evidence-library views from the existing `analysis-options` and `analysis-runs` APIs. Backtest pages keep an empty-state data contract until backend backtest routes are added.
