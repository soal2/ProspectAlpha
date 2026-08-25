import { API_BASE_URL } from "./env";
import type {
  Agent,
  AnalysisSummary,
  BacktestDetailData,
  BacktestDetailResponse,
  BacktestJob,
  BacktestCandidatePreview,
  BacktestSummary,
  DashboardStats,
  EvidenceDetailItem,
  EvidenceFeedItem,
  HomepageExample,
  IntakeData,
  KnowledgeData,
  MarketSnapshotOption,
  ReportData,
  RiskLevel,
  RiskRule,
  RiskRulesData,
  TraceData,
} from "./types";

export interface AnalysisFinding {
  finding_id: string;
  agent_name: string;
  skill_id?: string | null;
  rule_set_id?: string | null;
  rule_version?: string | null;
  rule_id?: string | null;
  dimension_code: string;
  rule_type: string;
  category: string;
  title: string;
  severity: "low" | "medium" | "high";
  confidence: number;
  trigger_condition: string;
  evaluation_result: string;
  reasoning_summary: string;
  evidence_ids?: string[];
  evidence_snippets: string[];
  matched_terms: string[];
  requires_human_review: boolean;
  verification_status?: string;
  risk_type?: string;
  origin?: "rule" | "text_signal" | "agent_emergent" | "external";
  causal_chain?: string;
  industry_relevance?: string;
  company_specificity?: string;
  expected_market_effects?: string[];
  external_evidence_ids?: string[];
  counter_evidence_ids?: string[];
  evidence_grade?: "direct" | "inferential" | "lead" | "contradicted";
  hypothesis_status?: "reported" | "corroborated" | "disputed" | "refuted";
}

export interface OrchestraStage {
  key: string;
  label: string;
  agent: string;
  goal: string;
  depends_on: string[];
  expected_outputs: string[];
}

export interface OrchestraPlan {
  summary: string;
  focus_areas: string[];
  stages: OrchestraStage[];
}

export interface StrategyAssignment {
  module_id: string;
  agent_name: string;
  module: string;
  goal: string;
  expected_outputs: string[];
}

export interface StrategyPlan {
  summary: string;
  focus_areas?: string[];
  selected_agents?: string[];
  assignments: StrategyAssignment[];
}

export interface AgentTeamState {
  agent_name: string;
  status: string;
  current_module?: string | null;
  replacement_round?: number;
}

export interface ReplacementHistoryEntry {
  module_id?: string;
  failed_agent?: string;
  replacement_agent?: string;
  agent_name?: string;
  reason?: string;
  status?: string;
  round?: number;
  [key: string]: unknown;
}

export interface AskCitation {
  block_id: string;
  document_id: string;
  page_number: number;
  section_path: string;
  snippet: string;
  bbox_regions: { page_number: number; bbox: number[] }[];
  bbox_coordinate_space: "pdf_points" | "normalized_1000";
}

export interface AskResult {
  answer: string;
  citations: AskCitation[];
}

export type OrchestraStageStatus = "queued" | "running" | "done" | "failed" | "paused";

export interface AnalysisRunEvent {
  event_id?: string;
  event_type: string;
  actor_name: string;
  title: string;
  summary: string;
  created_at?: string;
  stage_key?: string;
  stage_status?: string;
  skill_id?: string;
  agent_role?: string;
  action_label?: string;
  payload?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
  call_id?: string;
  sequence?: number;
  provider?: string;
  model?: string;
  raw_response?: string;
  duration_ms?: number;
  error?: string;
}

export interface AnalysisProgress {
  run_id: string;
  phase: string;
  updated_at: string;
  status: string;
  current_stage: string;
  error_summary?: string | null;
  pdf: {
    status: string;
    parsed_pages: number;
    total_pages: number;
    parsed_blocks: number;
    error: string | null;
  };
  index: {
    status: string;
    indexed_blocks: number;
    total_blocks: number;
  };
  agents: {
    agent_name: string;
    module_id: string;
    module: string;
    status: string;
    latest_action: string;
    latest_summary: string;
  }[];
}

export interface AnalysisRun {
  run_id: string;
  stock_code?: string | null;
  company_name: string;
  status: string;
  current_stage: string;
  failed_stage?: string | null;
  error_summary?: string | null;
  created_at: string;
  completed_at: string | null;
  data_cutoff_date: string;
  rule_snapshot: {
    rule_set_id: string;
    version: string;
    totals: { dimension_count: number; category_count: number; rule_count: number };
  };
  analysis_config: {
    risk_rule_set_id: string;
    analysis_depth: "quick" | "standard" | "deep";
    selection_mode?: "dynamic_strategy";
    available_agent_count?: number;
    strategy_selected_agents?: string[];
    selected_agents?: { agent_name: string; title: string }[];
    max_findings: number;
  };
  input_summary: {
    stock_code?: string | null;
    document_text_chars: number;
    document_text_preview?: string;
    structured_data_keys: string[];
    has_document_id?: boolean;
    has_material_url?: boolean;
  };
  understood?: {
    company_name?: string;
    material_hint?: string;
    focus?: string[];
  };
  blocking_questions?: string[];
  optional_questions?: string[];
  assumed_defaults?: Record<string, string>;
  findings: AnalysisFinding[];
  events: AnalysisRunEvent[];
  strategy_plan?: StrategyPlan;
  team_state?: AgentTeamState[];
  interrupt_reason?: string | null;
  replacement_history?: ReplacementHistoryEntry[];
  module_results?: Record<string, unknown>[];
  industry_profile?: {
    code: string;
    name: string;
    rationale: string;
    evidence_ids: string[];
    evidence_pages: number[];
    user_confirmed: boolean;
    frozen: boolean;
  } | null;
  text_signal_snapshot?: {
    version: string;
    industry_code: string;
    signals: Record<string, unknown>[];
    benchmark_note: string;
  } | null;
  hypotheses?: Record<string, unknown>[];
  research_budget?: { hypothesis_limit: number; search_limit: number } | null;
  research_usage?: { hypotheses_researched: number; searches_used: number; pages_fetched: number } | null;
  /** Read-only compatibility shape for runs created before dynamic strategy planning. */
  orchestra_plan?: OrchestraPlan | StrategyPlan;
}

type BackendReport = {
  report_id: string;
  status: string;
  review_status: string;
  overall_risk_level: RiskLevel;
  markdown: string;
  html: string;
  created_at: string;
  summary?: string;
  themes?: Record<string, unknown>[];
  exploratory_findings?: Record<string, unknown>[];
  counterevidence?: Record<string, unknown>[];
  limitations?: string[];
  evidence_index?: Record<string, unknown>[];
};

export type UploadedDocument = {
  document_id: string;
  filename: string;
  content_type: string;
  file_sha256: string;
  storage_uri: string;
  file_size_bytes: number;
  page_count: number;
  text_chars: number;
  block_count: number;
  processing_status: "uploaded" | "queued" | "processing" | "ready" | "failed" | "interrupted";
  processing_error_code: string | null;
  processing_error_message: string | null;
  processing_attempts: number;
  source_url: string | null;
  created_at: string;
};

export type AnalysisRunPayload = {
  company_name: string;
  stock_code?: string;
  document_text: string;
  document_id?: string;
  material_url?: string;
  ipo_project_hint?: string;
  data_cutoff_date?: string;
  confirm_defaults?: boolean;
  risk_rule_set_id?: string;
  analysis_depth?: "quick" | "standard" | "deep";
  structured_data?: Record<string, string>;
  max_findings?: number;
};

export type AnalysisRunInputPatch = {
  company_name?: string;
  stock_code?: string;
  document_text?: string;
  document_id?: string;
  material_url?: string;
  ipo_project_hint?: string;
  data_cutoff_date?: string;
  risk_rule_set_id?: string;
  analysis_depth?: "quick" | "standard" | "deep";
  structured_data?: Record<string, string>;
  max_findings?: number;
};

type BackendEvidence = {
  evidence_id: string;
  finding_id: string;
  document_block_id: string;
  document_id: string;
  page_number: number;
  section_path: string;
  text: string;
  bbox: number[];
  bbox_coordinate_space: "pdf_points" | "normalized_1000";
  char_start: number;
  char_end: number;
  source_hash: string;
  retrieval_score: number;
  relevance_score: number;
  verifier_status: string;
};

type BackendAgent = Agent;

export type AnalysisAgentOption = {
  agent_name: string;
  title: string;
  default?: boolean;
  description?: string;
  capabilities?: string[];
  agent_card_url?: string;
  persona_id?: string;
  display_name?: string;
  layer?: string;
  function?: string;
  avatar?: string;
  accent?: string;
  expertise?: string[];
  role_zh?: string;
  skill_id?: string;
};

export type AnalysisOptions = {
  risk_rule_sets: {
    rule_set_id: string;
    version: string;
    name: string;
    rule_count: number;
    active: boolean;
  }[];
  agents: AnalysisAgentOption[];
  analysis_depths: {
    depth: "quick" | "standard" | "deep";
    label: string;
    max_findings: number;
  }[];
};

async function fetchAnalyses(): Promise<AnalysisSummary[]> {
  const runs = await apiRequest<AnalysisRun[]>("/analysis-runs");
  return Promise.all(
    runs.map(async (run) => {
      const [evidenceCount, report] = await Promise.all([
        fetchRunEvidence(run.run_id).then((items) => items.length),
        fetchBackendReport(run.run_id),
      ]);
      return toAnalysisSummary(run, evidenceCount, report?.overall_risk_level);
    }),
  );
}

async function fetchAnalysis(id: string): Promise<AnalysisSummary | null> {
  try {
    const [run, report] = await Promise.all([
      apiRequest<AnalysisRun>(`/analysis-runs/${id}`),
      fetchBackendReport(id),
    ]);
    return toAnalysisSummary(run, undefined, report?.overall_risk_level);
  } catch (err) {
    if (isNotFound(err)) return null;
    throw err;
  }
}

async function fetchAgents(): Promise<Agent[]> {
  const [agents, options] = await Promise.all([
    apiRequest<BackendAgent[]>("/agents"),
    apiRequest<AnalysisOptions>("/analysis-options"),
  ]);
  const runtimeNameByPersona = new Map(
    options.agents.map((option) => [option.persona_id, option.agent_name]),
  );
  return agents.map((agent) => ({
    ...agent,
    runtimeName: runtimeNameByPersona.get(agent.id),
  }));
}

async function fetchBacktests(): Promise<BacktestSummary[]> {
  return apiRequest<BacktestSummary[]>("/backtests");
}

async function fetchDashboardStats(): Promise<DashboardStats> {
  const runs = await apiRequest<AnalysisRun[]>("/analysis-runs");
  const evidenceCounts = await Promise.all(
    runs.map(async (run) => (await fetchRunEvidence(run.run_id)).length),
  );
  const totalAnalyses = runs.length;
  const conclusions = runs.reduce((total, run) => total + run.findings.length, 0);
  const evidenceCount = evidenceCounts.reduce((total, count) => total + count, 0);
  const crossValidated = runs.reduce(
    (total, run) =>
      total +
      run.findings.filter(
        (finding) => (finding.evidence_ids?.length ?? finding.evidence_snippets.length) >= 2,
      ).length,
    0,
  );
  return {
    totalAnalyses,
    evidenceCount,
    conclusions,
    crossValidationRate: conclusions ? Math.round((crossValidated / conclusions) * 100) : 0,
    hoursSaved: totalAnalyses * 4,
    efficiency: totalAnalyses ? 6 : 0,
    avgSources: conclusions ? Number((evidenceCount / conclusions).toFixed(1)) : 0,
    tokenK: Math.round(
      runs.reduce((total, run) => total + run.events.length * 1.2 + run.findings.length * 0.8, 0),
    ),
  };
}

async function fetchHomepageExamples(): Promise<HomepageExample[]> {
  const ruleSet = await fetchRiskRulesData();
  const icons = ["ShieldAlert", "FileText", "LineChart", "Building2"];
  return ruleSet.dimensions.slice(0, 4).map((dimension, index) => ({
    icon: icons[index] ?? "Building2",
    title: dimension.dimension,
    desc:
      dimension.categories.length > 0
        ? `覆盖 ${dimension.categories.map((item) => item.name).join("、")}，共 ${dimension.rule_count} 条规则。`
        : `覆盖 ${dimension.rule_count} 条风险规则。`,
  }));
}

async function fetchReportData(id: string): Promise<ReportData | null> {
  try {
    const [run, report] = await Promise.all([
      apiRequest<AnalysisRun>(`/analysis-runs/${id}`),
      fetchBackendReport(id),
    ]);
    if (!report) return null;
    return toReportData(run, report);
  } catch (err) {
    if (isNotFound(err)) return null;
    throw err;
  }
}

async function fetchBackendReport(id: string): Promise<BackendReport | null> {
  try {
    return await apiRequest<BackendReport>(`/analysis-runs/${id}/report`);
  } catch (err) {
    if (isNotFound(err)) return null;
    throw err;
  }
}

async function fetchTraceData(id: string): Promise<TraceData | null> {
  try {
    return apiRequest<TraceData>(`/analysis-runs/${id}/trace`);
  } catch (err) {
    if (isNotFound(err)) return null;
    throw err;
  }
}

function evidenceStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    supported: "已核验支持",
    weakly_supported: "弱支持证据",
    needs_human_review: "待人工复核",
    rejected_by_debate: "红队否决候选证据",
    retrieval_candidate: "检索候选 · 尚未核验",
    pending: "待核验",
  };
  return labels[status] ?? status;
}

async function fetchEvidenceDetailItems(id: string): Promise<EvidenceDetailItem[]> {
  try {
    const [run, evidence] = await Promise.all([
      apiRequest<AnalysisRun>(`/analysis-runs/${id}`),
      apiRequest<BackendEvidence[]>(`/analysis-runs/${id}/evidence`),
    ]);
    const riskByFinding = Object.fromEntries(
      run.findings.map((finding) => [finding.finding_id, finding.category]),
    );
    return evidence.map((item) => ({
      id: item.evidence_id,
      section: item.section_path,
      page: item.page_number,
      conf:
        item.verifier_status === "retrieval_candidate"
          ? item.retrieval_score
          : item.relevance_score,
      scoreKind:
        item.verifier_status === "retrieval_candidate"
          ? "retrieval"
          : item.relevance_score > 0
            ? "relevance"
            : ("unavailable" as "retrieval" | "relevance" | "unavailable"),
      snippet: item.text,
      risk: riskByFinding[item.finding_id] ?? evidenceStatusLabel(item.verifier_status),
      documentId: item.document_id,
      bbox: item.bbox,
      bboxCoordinateSpace: item.bbox_coordinate_space,
    }));
  } catch (err) {
    if (isNotFound(err)) return [];
    throw err;
  }
}

async function fetchRunEvidence(id: string): Promise<BackendEvidence[]> {
  try {
    return await apiRequest<BackendEvidence[]>(`/analysis-runs/${id}/evidence`);
  } catch (err) {
    if (isNotFound(err)) return [];
    throw err;
  }
}

async function fetchKnowledgeData(): Promise<KnowledgeData> {
  const runs = await apiRequest<AnalysisRun[]>("/analysis-runs");
  const perRunEvidence = await Promise.all(
    runs.map(async (run) => {
      try {
        return {
          run,
          evidence: await apiRequest<BackendEvidence[]>(`/analysis-runs/${run.run_id}/evidence`),
        };
      } catch (err) {
        if (isNotFound(err)) return { run, evidence: [] };
        throw err;
      }
    }),
  );
  const evidence = perRunEvidence.flatMap(({ run, evidence: items }) => {
    const riskByFinding = Object.fromEntries(
      run.findings.map((finding) => [finding.finding_id, finding.category]),
    );
    return items.map((item) => ({
      id: item.evidence_id,
      code: String(
        (run as AnalysisRun & { structured_data?: Record<string, unknown> }).structured_data
          ?.code ?? "IPO",
      ),
      name: run.company_name,
      section: item.section_path,
      page: item.page_number,
      conf:
        item.verifier_status === "retrieval_candidate"
          ? item.retrieval_score
          : item.relevance_score,
      scoreKind:
        item.verifier_status === "retrieval_candidate"
          ? "retrieval"
          : item.relevance_score > 0
            ? "relevance"
            : ("unavailable" as "retrieval" | "relevance" | "unavailable"),
      snippet: item.text,
      risk: riskByFinding[item.finding_id] ?? evidenceStatusLabel(item.verifier_status),
      documentId: item.document_id,
      bbox: item.bbox,
      bboxCoordinateSpace: item.bbox_coordinate_space,
      createdAt: run.created_at,
    }));
  });
  const companies = [...new Set(evidence.map((item) => item.name).filter(Boolean))];
  const sections = [...new Set(evidence.map((item) => item.section).filter(Boolean))];
  const risks = [...new Set(evidence.map((item) => item.risk).filter(Boolean))];
  const preview = evidence[0];
  return {
    facets: [
      { label: "公司", items: ["全部", ...companies] },
      { label: "招股书章节", items: ["全部", ...sections] },
      { label: "风险类别", items: ["全部", ...risks] },
      {
        label: "证据相关性",
        items: ["全部", "≥ 0.9 高", "0.7–0.9 中", "< 0.7 低", "未核验"],
      },
    ],
    evidence,
    stats: { totalEvidence: evidence.length, totalCompanies: companies.length },
    preview: {
      evidenceId: preview?.id ?? "",
      code: preview?.code ?? "",
      section: preview?.section ?? "",
      page: preview?.page ?? 0,
      linkedConclusions: preview ? [preview.risk] : [],
    },
  };
}

async function fetchBacktestDetailData(id: string): Promise<BacktestDetailResponse | null> {
  try {
    return apiRequest<BacktestDetailResponse>(`/backtests/${id}`);
  } catch (err) {
    if (isNotFound(err)) return null;
    throw err;
  }
}

async function fetchIntakeData(): Promise<IntakeData> {
  const options = await fetchAnalysisOptions();
  const activeRuleSet = options.risk_rule_sets.find((item) => item.active);
  return {
    checks: [
      {
        label: "后端 API",
        value: API_BASE_URL,
        ok: true,
      },
      {
        label: "风险规则",
        value: activeRuleSet
          ? `${activeRuleSet.version} · ${activeRuleSet.rule_count} 条`
          : "未配置",
        ok: Boolean(activeRuleSet),
      },
      {
        label: "Agent 配置",
        value: `${options.agents.length} 个可选 Agent`,
        ok: options.agents.length > 0,
      },
    ],
    parsing: [
      { label: "上传文档", pct: 0, status: "等待 PDF" },
      { label: "解析原文块", pct: 0, status: "等待启动" },
      { label: "建立检索索引", pct: 0, status: "等待启动" },
      { label: "Agent 分析", pct: 0, status: "等待启动" },
    ],
    uploadedFile: {
      name: "",
      pages: 0,
      size: "",
      uploadedAt: "",
    },
    progressText: "等待上传招股书并创建分析任务",
  };
}

async function fetchAnalysisOptions(): Promise<AnalysisOptions> {
  return apiRequest<AnalysisOptions>("/analysis-options");
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      ...(isFormData ? {} : { "content-type": "application/json" }),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error(await responseError(response));
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function isNotFound(err: unknown) {
  return err instanceof Error && err.message.includes("404");
}

async function fetchRiskRulesData(): Promise<RiskRulesData> {
  return apiRequest<RiskRulesData>("/risk-rules");
}

// Analyses
export async function getAnalyses() {
  return fetchAnalyses();
}

export async function getAnalysis(id: string) {
  return fetchAnalysis(id);
}

export async function getAnalysisRun(id: string) {
  return apiRequest<AnalysisRun>(`/analysis-runs/${id}`);
}

export async function deleteAnalysisRun(id: string) {
  return apiRequest<void>(`/analysis-runs/${id}`, { method: "DELETE" });
}

export async function getAnalysisProgress(id: string) {
  return apiRequest<AnalysisProgress>(`/analysis-runs/${id}/progress`);
}

// Agents
export async function getAgents() {
  return fetchAgents();
}

// Backtests
export async function getBacktests() {
  return fetchBacktests();
}

export async function createBacktest(payload: { snapshot_id: string; wind_code: string; name?: string; test_start_date?: string; test_end_date?: string }) {
  return apiRequest<BacktestSummary>("/backtests", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getBacktestCandidates(snapshotId: string) {
  return apiRequest<BacktestCandidatePreview[]>(`/backtests/market-data/${snapshotId}/candidates`);
}

export async function confirmBacktestCandidates(id: string, decisions: { wind_code: string; score_id?: string | null; include: boolean; exclusion_reason?: string | null }[]) {
  return apiRequest<BacktestSummary>(`/backtests/${id}/candidates`, { method: "POST", body: JSON.stringify({ decisions }) });
}

export async function startBacktest(id: string) {
  return apiRequest<BacktestJob>(`/backtests/${id}/start`, { method: "POST" });
}

export async function reproduceBacktest(id: string) {
  return apiRequest<BacktestJob>(`/backtests/${id}/reproduce`, { method: "POST" });
}

export async function publishMarketSnapshot(id: string) {
  return apiRequest<MarketSnapshotOption>(`/backtests/market-data/${id}/publish`, { method: "POST" });
}

export async function uploadMarketData(files: { security: File; company: File; prices: File }) {
  const body = new FormData();
  body.append("security", files.security);
  body.append("company", files.company);
  body.append("prices", files.prices);
  const response = await fetch(`${API_BASE_URL}/backtests/market-data/upload`, { method: "POST", body });
  if (!response.ok) throw new Error((await response.json().catch(() => ({ detail: response.statusText }))).detail ?? "市场数据上传失败");
  return response.json() as Promise<{ snapshot_id: string; labels_created: number }>;
}

export async function getMarketSnapshots() {
  return apiRequest<MarketSnapshotOption[]>("/backtests/market-data/snapshots");
}

// Dashboard
export async function getDashboardStats() {
  return fetchDashboardStats();
}

// Homepage
export async function getHomepageExamples() {
  return fetchHomepageExamples();
}

// Report
export async function getReportData(id: string) {
  return fetchReportData(id);
}

// Trace
export async function getTraceData(id: string) {
  return fetchTraceData(id);
}

// Evidence Detail
export async function getEvidenceDetailItems(id: string) {
  return fetchEvidenceDetailItems(id);
}

// Evidence Knowledge
export async function getKnowledgeData() {
  return fetchKnowledgeData();
}

// Backtest Detail
export async function getBacktestDetailData(id: string) {
  return fetchBacktestDetailData(id);
}

// Intake
export async function getIntakeData() {
  return fetchIntakeData();
}

export async function getAnalysisOptions() {
  return fetchAnalysisOptions();
}

export async function createAnalysisDraft(payload: AnalysisRunPayload) {
  return apiRequest<AnalysisRun>("/analysis-runs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateAnalysisInputs(runId: string, patch: AnalysisRunInputPatch) {
  return apiRequest<AnalysisRun>(`/analysis-runs/${runId}/inputs`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function startAnalysisRun(runId: string) {
  const response = await fetch(`${API_BASE_URL}/analysis-runs/${runId}/start`, {
    method: "POST",
    headers: { "content-type": "application/json" },
  });
  if (response.status === 409) {
    return getAnalysisRun(runId);
  }
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<AnalysisRun>;
}

export async function confirmIndustryProfile(
  runId: string,
  payload: { code?: string; name?: string },
) {
  return apiRequest<AnalysisRun>(`/analysis-runs/${runId}/industry-profile/confirm`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function selectAnalysisExperts(runId: string, agentNames: string[]) {
  return apiRequest<AnalysisRun>(`/analysis-runs/${runId}/expert-selection`, {
    method: "POST",
    body: JSON.stringify({ agent_names: agentNames }),
  });
}

export async function createAnalysisRun(payload: AnalysisRunPayload) {
  const draft = await createAnalysisDraft(payload);
  if (draft.status !== "ready") return draft;
  const started = await startAnalysisRun(draft.run_id);
  if (started.status !== "queued") return started;
  return pollAnalysisRun(started.run_id);
}

export async function uploadDocument(file: File) {
  const form = new FormData();
  form.append("file", file);
  return apiRequest<UploadedDocument>("/documents/upload", {
    method: "POST",
    body: form,
  });
}

export function getDocumentFileUrl(documentId: string) {
  return `${API_BASE_URL}/documents/${documentId}/file`;
}

export async function listDocuments() {
  return apiRequest<UploadedDocument[]>("/documents");
}

export async function getDocument(documentId: string) {
  return apiRequest<UploadedDocument>(`/documents/${documentId}`);
}

export async function reparseDocument(documentId: string) {
  return apiRequest<UploadedDocument>(`/documents/${documentId}/reparse`, {
    method: "POST",
  });
}

export async function getDocumentParserHealth() {
  return apiRequest<{
    status: "ready" | "model_download_required" | "unavailable";
    installed: boolean;
    model_source: string;
    device: string;
    model_cache_present: boolean;
    message: string;
  }>("/documents/parser-health");
}

export async function waitForDocumentReady(
  documentId: string,
  onProgress?: (document: UploadedDocument) => void,
) {
  const deadline = Date.now() + 30 * 60_000;
  while (Date.now() < deadline) {
    const document = await getDocument(documentId);
    onProgress?.(document);
    if (document.block_count > 0 || document.processing_status === "ready") return document;
    if (["failed", "interrupted"].includes(document.processing_status)) {
      throw new Error(
        document.processing_error_message ?? "MinerU 解析失败，可点击“重新解析”再次尝试。",
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
  throw new Error("MinerU 解析仍在后台运行，请稍后重新打开页面查看状态。");
}

// PDF Q&A — SSE stream from POST /documents/{id}/ask
// EventSource doesn't support POST, so we use fetch + ReadableStream reader.
export async function askDocument(
  documentId: string,
  question: string,
  onDelta: (delta: string, full: string) => void,
  options: { topK?: number; signal?: AbortSignal } = {},
): Promise<AskResult> {
  const response = await fetch(`${API_BASE_URL}/documents/${documentId}/ask`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, top_k: options.topK ?? 6 }),
    signal: options.signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(await responseError(response));
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answer = "";
  const citations: AskCitation[] = [];
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 2);
      if (!raw.startsWith("data:")) continue;
      const payload = raw.slice(5).trim();
      if (!payload) continue;
      let evt: { type: string; delta?: string; citations?: AskCitation[]; message?: string };
      try {
        evt = JSON.parse(payload);
      } catch {
        continue;
      }
      if (evt.type === "delta" && evt.delta) {
        answer += evt.delta;
        onDelta(evt.delta, answer);
      } else if (evt.type === "done" && evt.citations) {
        citations.push(...evt.citations);
      } else if (evt.type === "error") {
        throw new Error(evt.message || "LLM stream error");
      }
    }
  }
  return { answer, citations };
}

async function responseError(response: Response) {
  const text = await response.text().catch(() => "");
  if (!text) return `Request failed: ${response.status}`;
  try {
    const parsed = JSON.parse(text) as { detail?: string };
    return `Request failed (${response.status}): ${parsed.detail || text}`;
  } catch {
    return `Request failed (${response.status}): ${text}`;
  }
}

// General LLM chat — SSE stream from POST /chat (no document required)
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatUnderstanding {
  company?: string | null;
  focus?: string[];
  material_hints?: string | null;
  clarifying_questions?: string[];
}

export interface ChatResult {
  reply: string;
  understanding?: ChatUnderstanding;
}

export async function chatWithAssistant(
  message: string,
  history: ChatMessage[],
  onDelta: (delta: string, full: string) => void,
  options: { signal?: AbortSignal } = {},
): Promise<ChatResult> {
  const response = await fetch(`${API_BASE_URL}/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message, history }),
    signal: options.signal,
  });
  if (!response.ok || !response.body) {
    const text = await response.text().catch(() => "");
    throw new Error(text || `Request failed: ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let reply = "";
  let understanding: ChatUnderstanding | undefined;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 2);
      if (!raw.startsWith("data:")) continue;
      const payload = raw.slice(5).trim();
      if (!payload) continue;
      let evt: {
        type: string;
        delta?: string;
        reply?: string;
        understanding?: ChatUnderstanding;
        message?: string;
      };
      try {
        evt = JSON.parse(payload);
      } catch {
        continue;
      }
      if (evt.type === "delta" && evt.delta) {
        reply += evt.delta;
        onDelta(evt.delta, reply);
      } else if (evt.type === "done") {
        if (evt.reply) reply = evt.reply;
        if (evt.understanding) understanding = evt.understanding;
      } else if (evt.type === "error") {
        throw new Error(evt.message || "LLM stream error");
      }
    }
  }
  return { reply, understanding };
}

// Live analysis-run SSE stream — GET /analysis-runs/{id}/stream
export async function streamAnalysisRun(
  runId: string,
  onEvent: (event: AnalysisRunEvent) => void,
  onDone: (run: AnalysisRun) => void,
  options: { signal?: AbortSignal; after?: number } = {},
): Promise<void> {
  const after = options.after ? `?after=${options.after}` : "";
  const response = await fetch(`${API_BASE_URL}/analysis-runs/${runId}/stream${after}`, {
    method: "GET",
    headers: { accept: "text/event-stream" },
    signal: options.signal,
  });
  if (!response.ok || !response.body) {
    const text = await response.text().catch(() => "");
    throw new Error(text || `Request failed: ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 2);
      if (!raw.startsWith("data:")) continue;
      const payload = raw.slice(5).trim();
      if (!payload) continue;
      let evt:
        | ({
            type: string;
            event?: AnalysisRunEvent;
            run?: AnalysisRun;
            message?: string;
          } & Partial<AnalysisRunEvent>)
        | AnalysisRunEvent;
      try {
        evt = JSON.parse(payload);
      } catch {
        continue;
      }
      if ("type" in evt && evt.type === "event" && evt.event) {
        onEvent(evt.event);
      } else if ("type" in evt && evt.type === "done" && evt.run) {
        onDone(evt.run);
        return;
      } else if ("type" in evt && evt.type === "paused") {
        onDone(evt.run ?? (await getAnalysisRun(runId)));
        return;
      } else if ("type" in evt && evt.type === "error") {
        throw new Error(evt.message || "stream error");
      } else if ("event_type" in evt && evt.event_type) {
        const event = evt as AnalysisRunEvent;
        onEvent(event);
        if (
          event.event_type === "analysis_paused" ||
          event.event_type === "expert_selection_required"
        ) {
          onDone(await getAnalysisRun(runId));
          return;
        }
      }
    }
  }
}

// Risk Rules
export async function getRiskRulesData() {
  return fetchRiskRulesData();
}

export type RiskRulePayload = Omit<RiskRule, "rule_id">;

export async function createRiskRule(payload: RiskRulePayload) {
  return apiRequest<RiskRule>("/risk-rules", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateRiskRule(ruleId: string, payload: Partial<RiskRulePayload>) {
  return apiRequest<RiskRule>(`/risk-rules/${ruleId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteRiskRule(ruleId: string) {
  return apiRequest<void>(`/risk-rules/${ruleId}`, { method: "DELETE" });
}

async function fetchEvidenceFeed(id?: string): Promise<EvidenceFeedItem[]> {
  if (!id) return [];
  const trace = await fetchTraceData(id);
  return trace?.evidenceFeed ?? [];
}

export async function getEvidenceFeed(id?: string) {
  return fetchEvidenceFeed(id);
}

function toAnalysisSummary(
  run: AnalysisRun,
  evidenceCountOverride?: number,
  reportRiskLevel?: RiskLevel,
): AnalysisSummary {
  const structured =
    (run as AnalysisRun & { structured_data?: Record<string, unknown> }).structured_data ?? {};
  const evidenceCount =
    evidenceCountOverride ??
    run.findings.reduce((total, finding) => total + finding.evidence_snippets.length, 0);
  const highConfidence = run.findings.filter((finding) => finding.confidence >= 0.75).length;
  return {
    id: run.run_id,
    code: String(structured.code ?? structured.ticker ?? "IPO"),
    name: run.company_name,
    industry: String(structured.industry ?? "港股 IPO"),
    listingDate: String(structured.listing_date ?? run.data_cutoff_date),
    offerPrice: Number(structured.offer_price ?? 0),
    offerSize: String(structured.offer_size ?? "待确认"),
    riskLevel: reportRiskLevel ?? rateRun(run),
    evidenceCount,
    highConfidence,
    mode: "expert",
    createdAt: run.created_at,
    experts:
      run.strategy_plan?.assignments?.length ||
      run.team_state?.length ||
      new Set(run.findings.map((finding) => finding.agent_name)).size ||
      0,
  };
}

async function pollAnalysisRun(runId: string) {
  const deadline = Date.now() + 90_000;
  let latest = await apiRequest<AnalysisRun>(`/analysis-runs/${runId}`);
  while (
    latest.status === "queued" ||
    latest.status === "parsing" ||
    latest.status === "indexing" ||
    latest.status === "spawning_experts" ||
    latest.status === "agent_running" ||
    latest.status === "replanning" ||
    latest.status === "verifying" ||
    latest.status === "synthesizing" ||
    latest.status === "reporting"
  ) {
    if (Date.now() > deadline) return latest;
    await new Promise((resolve) => setTimeout(resolve, 1500));
    latest = await apiRequest<AnalysisRun>(`/analysis-runs/${runId}`);
  }
  return latest;
}

function rateRun(run: AnalysisRun): AnalysisSummary["riskLevel"] {
  const admitted = run.findings.filter((finding) =>
    ["supported", "weakly_supported"].includes(finding.verification_status ?? ""),
  );
  if (!admitted.length) return "unrated";
  if (admitted.some((finding) => finding.severity === "high" && !finding.requires_human_review)) {
    return "very_high";
  }
  if (admitted.length >= 5) return "high";
  if (admitted.length >= 3) return "medium";
  if (admitted.length >= 1) return "low";
  return "very_low";
}

function toReportData(run: AnalysisRun, report: BackendReport): ReportData {
  const categories = run.findings.reduce<Record<string, number>>((acc, finding) => {
    acc[finding.category] = (acc[finding.category] ?? 0) + 1;
    return acc;
  }, {});
  const toc = [
    "执行摘要 · 风险总评",
    "公司概览与发行信息",
    ...Object.keys(categories).slice(0, 8),
    "证据门禁与人工复核",
  ];
  const radar = ["FIN", "BUSINESS", "LEGAL", "MARKET", "GOVERNANCE"].map((dim) => ({
    dim,
    score: Math.min(
      100,
      20 + run.findings.filter((finding) => finding.dimension_code === dim).length * 18,
    ),
  }));
  const pie = Object.entries(categories).map(([name, value]) => ({ name, value }));
  const waterfall = [
    { name: "基线", value: 20 },
    ...run.findings.slice(0, 5).map((finding) => ({
      name: finding.category,
      value: finding.severity === "high" ? 12 : finding.severity === "medium" ? 8 : 4,
    })),
    { name: "报告", value: report.review_status === "draft" ? 0 : -5 },
  ];
  return {
    id: run.run_id,
    riskLevel: report.overall_risk_level,
    markdown: report.markdown,
    reviewStatus: report.review_status,
    createdAt: report.created_at,
    themes: report.themes ?? [],
    exploratoryFindings: report.exploratory_findings ?? [],
    counterevidence: report.counterevidence ?? [],
    limitations: report.limitations ?? [],
    evidenceIndex: report.evidence_index ?? [],
    toc,
    radar,
    pie,
    waterfall,
    cashflow: [],
    palette: [
      "oklch(var(--chart-1))",
      "oklch(var(--chart-2))",
      "oklch(var(--chart-3))",
      "oklch(var(--chart-4))",
      "oklch(var(--chart-5))",
      "oklch(var(--chart-6))",
    ],
  };
}
