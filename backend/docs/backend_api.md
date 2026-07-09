# ProspectAlpha Backend API

Base URL defaults to `http://localhost:8000`. Frontend configuration uses:

- `VITE_API_BASE_URL`: backend base URL

All JSON endpoints use `Content-Type: application/json` unless noted.

## Agents

### `GET /agents`

Returns the database-backed Agent expert directory used by the frontend expert guild pages.
Empty databases are initialized with the bundled expert seed data before the response is produced.

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

## Health

### `GET /health`

Returns runtime health.

Response:

```json
{ "status": "ok" }
```

## Analysis Options

### `GET /analysis-options`

Returns active rule-set, available LangGraph agents, and supported analysis depths for the intake page.

Response fields:

- `risk_rule_sets[]`: `{ rule_set_id, version, name, rule_count, active }`
- `agents[]`: `{ agent_name, title, default }`
- `analysis_depths[]`: `{ depth, label, max_findings }`

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
  "selected_agent_names": ["financial_agent", "legal_agent"],
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

### `GET /analysis-runs/{run_id}/events?after=0`

Returns run events after the numeric offset.

Response:

```json
{
  "run_id": "run_xxx",
  "events": []
}
```

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
- `findings[]`
- `events[]`

Errors:

- `400`: invalid create payload
- `404`: run/report not found
- `409`: invalid state transition, such as starting a non-ready run or editing frozen inputs

## Frontend Coverage Notes

The current backend exposes no dedicated routes for backtest jobs, global evidence search, homepage examples, or dashboard KPI snapshots. The frontend real-data mode therefore derives dashboard, homepage, expert, and evidence-library views from the existing `analysis-options` and `analysis-runs` APIs. Backtest pages keep an empty-state data contract until backend backtest routes are added.
