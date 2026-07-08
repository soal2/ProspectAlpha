import { API_BASE_URL, USE_MOCK } from "./env";
import {
  analyses as mockAnalyses,
  agents as mockAgents,
  backtests as mockBacktests,
  dashboardStats as mockDashboardStats,
  homepageExamples,
  reportData,
  traceData,
  evidenceDetailItems,
  backtestDetailData,
  intakeData,
  riskRulesData,
  backtestNewData,
  knowledgeData,
} from "@/mocks";
import type {
  ReportData,
  TraceData,
  EvidenceDetailItem,
  KnowledgeData,
  BacktestDetailData,
  Agent,
  DashboardStats,
  HomepageExample,
} from "@/mocks";

export interface AnalysisFinding {
  finding_id: string;
  agent_name: string;
  rule_set_id: string;
  rule_version: string;
  rule_id: string;
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

export interface AskCitation {
  block_id: string;
  page_number: number;
  section_path: string;
  snippet: string;
}

export interface AskResult {
  answer: string;
  citations: AskCitation[];
}

export type OrchestraStageStatus = "queued" | "running" | "done" | "failed";

export interface AnalysisRunEvent {
  event_type: string;
  actor_name: string;
  title: string;
  summary: string;
  created_at?: string;
}

export interface AnalysisRun {
  run_id: string;
  company_name: string;
  status: string;
  current_stage: string;
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
    selected_agents: { agent_name: string; title: string }[];
    max_findings: number;
  };
  input_summary: {
    document_text_chars: number;
    structured_data_keys: string[];
  };
  findings: AnalysisFinding[];
  events: { event_type: string; actor_name: string; title: string; summary: string }[];
  orchestra_plan?: OrchestraPlan;
}

type BackendReport = {
  report_id: string;
  status: string;
  review_status: string;
  markdown: string;
  html: string;
  created_at: string;
};

type UploadedDocument = {
  document_id: string;
  filename: string;
  content_type: string;
  file_sha256: string;
  storage_uri: string;
  file_size_bytes: number;
  page_count: number;
  text_chars: number;
  block_count: number;
  source_url: string | null;
  created_at: string;
};

type BackendEvidence = {
  evidence_id: string;
  finding_id: string;
  document_block_id: string;
  page_number: number;
  section_path: string;
  text: string;
  bbox: number[];
  char_start: number;
  char_end: number;
  source_hash: string;
  confidence: number;
  verifier_status: string;
};

type BackendAgent = Agent;

export type AnalysisOptions = {
  risk_rule_sets: {
    rule_set_id: string;
    version: string;
    name: string;
    rule_count: number;
    active: boolean;
  }[];
  agents: {
    agent_name: string;
    title: string;
    default: boolean;
  }[];
  analysis_depths: {
    depth: "quick" | "standard" | "deep";
    label: string;
    max_findings: number;
  }[];
};

// Mock data fetchers (replace with real API calls when backend is ready)
async function fetchAnalyses(): Promise<typeof mockAnalyses> {
  const runs = await apiRequest<AnalysisRun[]>("/analysis-runs");
  return runs.map(toAnalysisSummary);
}

async function fetchAnalysis(id: string): Promise<(typeof mockAnalyses)[number] | null> {
  try {
    return toAnalysisSummary(await apiRequest<AnalysisRun>(`/analysis-runs/${id}`));
  } catch (err) {
    if (isNotFound(err)) return null;
    throw err;
  }
}

async function fetchAgents(): Promise<typeof mockAgents> {
  return apiRequest<BackendAgent[]>("/agents");
}

async function fetchBacktests(): Promise<typeof mockBacktests> {
  return [];
}

async function fetchDashboardStats(): Promise<DashboardStats> {
  const runs = await apiRequest<AnalysisRun[]>("/analysis-runs");
  const totalAnalyses = runs.length;
  const conclusions = runs.reduce((total, run) => total + run.findings.length, 0);
  const evidenceCount = runs.reduce((total, run) => total + countRunEvidence(run), 0);
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
      apiRequest<BackendReport>(`/analysis-runs/${id}/report`),
    ]);
    return toReportData(run, report);
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
      conf: item.confidence,
      snippet: item.text,
      risk: riskByFinding[item.finding_id] ?? "风险证据",
    }));
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
      conf: item.confidence,
      snippet: item.text,
      risk: riskByFinding[item.finding_id] ?? item.verifier_status,
    }));
  });
  const companies = [...new Set(runs.map((run) => run.company_name).filter(Boolean))];
  const sections = [...new Set(evidence.map((item) => item.section).filter(Boolean))];
  const risks = [...new Set(evidence.map((item) => item.risk).filter(Boolean))];
  const preview = evidence[0];
  return {
    facets: [
      { label: "公司", items: ["全部", ...companies] },
      { label: "招股书章节", items: ["全部", ...sections] },
      { label: "风险类别", items: ["全部", ...risks] },
      { label: "置信度", items: ["全部", "≥ 0.9 高", "0.7–0.9 中", "< 0.7 低"] },
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

async function fetchBacktestDetailData(id: string): Promise<BacktestDetailData | null> {
  return null;
}

async function fetchIntakeData(): Promise<typeof intakeData> {
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
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function isNotFound(err: unknown) {
  return err instanceof Error && err.message.includes("404");
}

async function fetchRiskRulesData(): Promise<typeof riskRulesData> {
  return apiRequest<typeof riskRulesData>("/risk-rules");
}

async function fetchBacktestNewData(): Promise<typeof backtestNewData> {
  return backtestNewData;
}

// Analyses
export async function getAnalyses() {
  if (USE_MOCK) return mockAnalyses;
  return fetchAnalyses();
}

export async function getAnalysis(id: string) {
  if (USE_MOCK) return mockAnalyses.find((a) => a.id === id) ?? null;
  return fetchAnalysis(id);
}

// Agents
export async function getAgents() {
  if (USE_MOCK) return mockAgents;
  return fetchAgents();
}

// Backtests
export async function getBacktests() {
  if (USE_MOCK) return mockBacktests;
  return fetchBacktests();
}

// Dashboard
export async function getDashboardStats() {
  if (USE_MOCK) return mockDashboardStats;
  return fetchDashboardStats();
}

// Homepage
export async function getHomepageExamples() {
  if (USE_MOCK) return homepageExamples;
  return fetchHomepageExamples();
}

// Report
export async function getReportData(id: string) {
  if (USE_MOCK) return reportData[id] ?? null;
  return fetchReportData(id);
}

// Trace
export async function getTraceData(id: string) {
  if (USE_MOCK) return traceData[id] ?? null;
  return fetchTraceData(id);
}

// Evidence Detail
export async function getEvidenceDetailItems(id: string) {
  if (USE_MOCK) return evidenceDetailItems[id] ?? [];
  return fetchEvidenceDetailItems(id);
}

// Evidence Knowledge
export async function getKnowledgeData() {
  if (USE_MOCK) return knowledgeData;
  return fetchKnowledgeData();
}

// Backtest Detail
export async function getBacktestDetailData(id: string) {
  if (USE_MOCK) return backtestDetailData[id] ?? null;
  return fetchBacktestDetailData(id);
}

// Intake
export async function getIntakeData() {
  if (USE_MOCK) return intakeData;
  return fetchIntakeData();
}

export async function getAnalysisOptions() {
  if (USE_MOCK) {
    return {
      risk_rule_sets: [
        {
          rule_set_id: riskRulesData.rule_set_id,
          version: riskRulesData.version,
          name: riskRulesData.name,
          rule_count: riskRulesData.totals.rule_count,
          active: true,
        },
      ],
      agents: [
        { agent_name: "financial_agent", title: "Financial specialist", default: false },
        { agent_name: "legal_agent", title: "Legal and compliance specialist", default: false },
        { agent_name: "market_agent", title: "Market and industry specialist", default: false },
        {
          agent_name: "business_agent",
          title: "Business and disclosure specialist",
          default: true,
        },
      ],
      analysis_depths: [
        { depth: "quick", label: "快速", max_findings: 8 },
        { depth: "standard", label: "标准", max_findings: 20 },
        { depth: "deep", label: "深度", max_findings: 40 },
      ],
    } satisfies AnalysisOptions;
  }
  return fetchAnalysisOptions();
}

export async function createAnalysisRun(payload: {
  company_name: string;
  document_text: string;
  document_id?: string;
  risk_rule_set_id?: string;
  selected_agent_names?: string[];
  analysis_depth?: "quick" | "standard" | "deep";
  structured_data?: Record<string, string>;
  max_findings?: number;
}) {
  if (USE_MOCK) {
    return {
      run_id: `mock_${Date.now()}`,
      company_name: payload.company_name,
      status: "completed",
      current_stage: "completed",
      created_at: new Date().toISOString(),
      completed_at: new Date().toISOString(),
      rule_snapshot: {
        rule_set_id: riskRulesData.rule_set_id,
        version: riskRulesData.version,
        totals: riskRulesData.totals,
      },
      input_summary: {
        document_text_chars: payload.document_text.length,
        structured_data_keys: Object.keys(payload.structured_data ?? {}),
      },
      analysis_config: {
        risk_rule_set_id: payload.risk_rule_set_id ?? riskRulesData.rule_set_id,
        analysis_depth: payload.analysis_depth ?? "standard",
        selected_agents: [],
        max_findings: payload.max_findings ?? 20,
      },
      findings: [],
      events: [],
    } satisfies AnalysisRun;
  }
  const draft = await apiRequest<AnalysisRun>("/analysis-runs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (draft.status !== "ready") return draft;
  const started = await apiRequest<AnalysisRun>(`/analysis-runs/${draft.run_id}/start`, {
    method: "POST",
  });
  if (started.status !== "queued") return started;
  return pollAnalysisRun(started.run_id);
}

export async function uploadDocument(file: File) {
  if (USE_MOCK) {
    return {
      document_id: `mock_doc_${Date.now()}`,
      filename: file.name,
      content_type: file.type || "application/pdf",
      file_sha256: "",
      storage_uri: "",
      file_size_bytes: file.size,
      page_count: 0,
      text_chars: 0,
      block_count: 0,
      source_url: null,
      created_at: new Date().toISOString(),
    } satisfies UploadedDocument;
  }
  const form = new FormData();
  form.append("file", file);
  return apiRequest<UploadedDocument>("/documents/upload", {
    method: "POST",
    body: form,
  });
}

// PDF Q&A — SSE stream from POST /documents/{id}/ask
// EventSource doesn't support POST, so we use fetch + ReadableStream reader.
export async function askDocument(
  documentId: string,
  question: string,
  onDelta: (delta: string, full: string) => void,
  options: { topK?: number; signal?: AbortSignal } = {},
): Promise<AskResult> {
  if (USE_MOCK) {
    // simulate streaming
    const answer = `（mock 模式）基于已上传的文档「${documentId}」，对问题「${question}」的样例回答：本招股书披露了关联交易、对赌安排与现金流压力等风险因素。`;
    let acc = "";
    for (const ch of answer) {
      await new Promise((r) => setTimeout(r, 12));
      acc += ch;
      onDelta(ch, acc);
    }
    return {
      answer,
      citations: [
        {
          block_id: "mock_block_1",
          page_number: 92,
          section_path: "风险因素 · 关联交易",
          snippet: "报告期内，公司与关联方存在持续性的采购与销售交易安排……",
        },
      ],
    };
  }
  const response = await fetch(`${API_BASE_URL}/documents/${documentId}/ask`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, top_k: options.topK ?? 6 }),
    signal: options.signal,
  });
  if (!response.ok || !response.body) {
    const text = await response.text().catch(() => "");
    throw new Error(text || `Request failed: ${response.status}`);
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
  if (USE_MOCK) {
    const reply = `（mock 模式）已收到你的问题：「${message}」。我会基于港股 IPO 风险分析框架，从财务、法律、业务和市场四个维度切入，先确认标的公司和招股书来源，然后再派发到 Agent 协同。`;
    let acc = "";
    for (const ch of reply) {
      await new Promise((r) => setTimeout(r, 12));
      acc += ch;
      onDelta(ch, acc);
    }
    return {
      reply,
      understanding: {
        company: message.slice(0, 6) || null,
        focus: ["对赌", "现金流"],
        material_hints: null,
        clarifying_questions: ["请提供招股书 PDF 或链接"],
      },
    };
  }
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
  options: { signal?: AbortSignal } = {},
): Promise<void> {
  if (USE_MOCK) {
    // simulate a few events then done
    const fakeEvents: AnalysisRunEvent[] = [
      {
        event_type: "stage_started",
        actor_name: "orchestrator",
        title: "Orchestra 编排分析计划",
        summary: "生成多阶段编队…",
      },
      {
        event_type: "orchestra_plan_generated",
        actor_name: "orchestrator",
        title: "Orchestra 计划已生成",
        summary: "4 个阶段",
      },
      {
        event_type: "stage_completed",
        actor_name: "orchestrator",
        title: "Orchestra 编排完成",
        summary: "进入解析阶段",
      },
    ];
    for (const ev of fakeEvents) {
      await new Promise((r) => setTimeout(r, 600));
      onEvent(ev);
    }
    const run = await apiRequest<AnalysisRun>(`/analysis-runs/${runId}`);
    onDone(run);
    return;
  }
  const response = await fetch(`${API_BASE_URL}/analysis-runs/${runId}/stream`, {
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
      let evt: { type: string; event?: AnalysisRunEvent; run?: AnalysisRun; message?: string };
      try {
        evt = JSON.parse(payload);
      } catch {
        continue;
      }
      if (evt.type === "event" && evt.event) {
        onEvent(evt.event);
      } else if (evt.type === "done" && evt.run) {
        onDone(evt.run);
        return;
      } else if (evt.type === "error") {
        throw new Error(evt.message || "stream error");
      }
    }
  }
}

// Risk Rules
export async function getRiskRulesData() {
  if (USE_MOCK) return riskRulesData;
  return fetchRiskRulesData();
}

export type RiskRulePayload = Omit<(typeof riskRulesData.rules)[number], "rule_id">;

export async function createRiskRule(payload: RiskRulePayload) {
  if (USE_MOCK) return { ...payload, rule_id: `MOCK-${Date.now()}` };
  return apiRequest<(typeof riskRulesData.rules)[number]>("/risk-rules", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateRiskRule(ruleId: string, payload: Partial<RiskRulePayload>) {
  if (USE_MOCK) return { ...riskRulesData.rules[0], ...payload, rule_id: ruleId };
  return apiRequest<(typeof riskRulesData.rules)[number]>(`/risk-rules/${ruleId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteRiskRule(ruleId: string) {
  if (USE_MOCK) return;
  return apiRequest<void>(`/risk-rules/${ruleId}`, { method: "DELETE" });
}

// Backtest New
export async function getBacktestNewData() {
  if (USE_MOCK) return backtestNewData;
  return fetchBacktestNewData();
}

// Evidence feed for analyses.$id.index (realtime stream page)
const evidenceFeed = [
  { id: "#1", source: "官方", title: "招股书 P.218 · 现金流量表", conf: 0.93, time: "近实时" },
  {
    id: "#2",
    source: "web",
    title: "海通证券 · 宁德时代深度报告 2026Q2",
    conf: 0.62,
    time: "近一年",
  },
  {
    id: "#3",
    source: "web",
    title: "公司公告 · 关于回购义务的补充披露",
    conf: 0.81,
    time: "近半年",
  },
  {
    id: "#4",
    source: "官方",
    title: "招股书 P.92 · 风险因素 · 关联交易",
    conf: 0.88,
    time: "近实时",
  },
  {
    id: "#5",
    source: "web",
    title: "Wind · 同业宁波容百 2025 年度报告",
    conf: 0.54,
    time: "近一年",
  },
];

async function fetchEvidenceFeed(id?: string): Promise<typeof evidenceFeed> {
  if (!id) return [];
  const trace = await fetchTraceData(id);
  return trace?.evidenceFeed ?? [];
}

export async function getEvidenceFeed(id?: string) {
  if (USE_MOCK) return evidenceFeed;
  return fetchEvidenceFeed(id);
}

function toAnalysisSummary(run: AnalysisRun): (typeof mockAnalyses)[number] {
  const structured =
    (run as AnalysisRun & { structured_data?: Record<string, unknown> }).structured_data ?? {};
  const evidenceCount = run.findings.reduce(
    (total, finding) => total + finding.evidence_snippets.length,
    0,
  );
  const highConfidence = run.findings.filter((finding) => finding.confidence >= 0.75).length;
  const riskScore = scoreRun(run);
  return {
    id: run.run_id,
    code: String(structured.code ?? structured.ticker ?? "IPO"),
    name: run.company_name,
    industry: String(structured.industry ?? "港股 IPO"),
    listingDate: String(structured.listing_date ?? run.data_cutoff_date),
    offerPrice: Number(structured.offer_price ?? 0),
    offerSize: String(structured.offer_size ?? "待确认"),
    riskLevel: riskScore >= 70 ? "high" : riskScore >= 40 ? "mid" : "low",
    riskScore,
    evidenceCount,
    highConfidence,
    mode: "expert",
    createdAt: run.created_at,
    experts: new Set(run.findings.map((finding) => finding.agent_name)).size || 4,
  };
}

async function pollAnalysisRun(runId: string) {
  const deadline = Date.now() + 90_000;
  let latest = await apiRequest<AnalysisRun>(`/analysis-runs/${runId}`);
  while (
    latest.status === "queued" ||
    latest.status === "parsing" ||
    latest.status === "indexing" ||
    latest.status === "agent_running" ||
    latest.status === "verifying" ||
    latest.status === "reporting"
  ) {
    if (Date.now() > deadline) return latest;
    await new Promise((resolve) => setTimeout(resolve, 1500));
    latest = await apiRequest<AnalysisRun>(`/analysis-runs/${runId}`);
  }
  return latest;
}

function countRunEvidence(run: AnalysisRun) {
  return run.findings.reduce(
    (total, finding) =>
      total + Math.max(finding.evidence_ids?.length ?? 0, finding.evidence_snippets.length),
    0,
  );
}

function scoreRun(run: AnalysisRun) {
  if (!run.findings.length) return 0;
  const total = run.findings.reduce((sum, finding) => {
    const severity = finding.severity === "high" ? 24 : finding.severity === "medium" ? 14 : 7;
    return sum + severity * finding.confidence;
  }, 0);
  return Math.min(95, Math.max(10, Math.round(total)));
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
