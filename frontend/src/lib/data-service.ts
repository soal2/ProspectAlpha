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
} from "@/mocks";
import type {
  ReportData,
  TraceData,
  EvidenceDetailItem,
  KnowledgeData,
  BacktestDetailData,
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
  evidence_snippets: string[];
  matched_terms: string[];
  requires_human_review: boolean;
}

export interface AnalysisRun {
  run_id: string;
  company_name: string;
  status: string;
  current_stage: string;
  created_at: string;
  completed_at: string | null;
  rule_snapshot: {
    rule_set_id: string;
    version: string;
    totals: { dimension_count: number; category_count: number; rule_count: number };
  };
  input_summary: {
    document_text_chars: number;
    structured_data_keys: string[];
  };
  findings: AnalysisFinding[];
  events: { event_type: string; actor_name: string; title: string; summary: string }[];
}

// Mock data fetchers (replace with real API calls when backend is ready)
async function fetchAnalyses(): Promise<typeof mockAnalyses> {
  return [];
}

async function fetchAnalysis(id: string): Promise<(typeof mockAnalyses)[number] | null> {
  return null;
}

async function fetchAgents(): Promise<typeof mockAgents> {
  return [];
}

async function fetchBacktests(): Promise<typeof mockBacktests> {
  return [];
}

async function fetchDashboardStats(): Promise<typeof mockDashboardStats> {
  return {
    totalAnalyses: 0,
    evidenceCount: 0,
    conclusions: 0,
    crossValidationRate: 0,
    hoursSaved: 0,
    efficiency: 0,
    avgSources: 0,
    tokenK: 0,
  };
}

async function fetchHomepageExamples(): Promise<typeof homepageExamples> {
  return [];
}

async function fetchReportData(id: string): Promise<ReportData | null> {
  return null;
}

async function fetchTraceData(id: string): Promise<TraceData | null> {
  return null;
}

async function fetchEvidenceDetailItems(id: string): Promise<EvidenceDetailItem[]> {
  return [];
}

async function fetchKnowledgeData(): Promise<KnowledgeData> {
  return {
    facets: [],
    evidence: [],
    stats: { totalEvidence: 0, totalCompanies: 0 },
    preview: {
      evidenceId: "",
      code: "",
      section: "",
      page: 0,
      linkedConclusions: [],
    },
  };
}

async function fetchBacktestDetailData(id: string): Promise<BacktestDetailData | null> {
  return null;
}

async function fetchIntakeData(): Promise<typeof intakeData> {
  return {
    checks: [],
    parsing: [],
    uploadedFile: {
      name: "",
      pages: 0,
      size: "",
      uploadedAt: "",
    },
    progressText: "",
  };
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
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

async function fetchRiskRulesData(): Promise<typeof riskRulesData> {
  return apiRequest<typeof riskRulesData>("/risk-rules");
}

async function fetchBacktestNewData(): Promise<typeof backtestNewData> {
  return {
    steps: [],
  };
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
  if (USE_MOCK) return fetchKnowledgeData();
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

export async function createAnalysisRun(payload: {
  company_name: string;
  document_text: string;
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
      findings: [],
      events: [],
    } satisfies AnalysisRun;
  }
  return apiRequest<AnalysisRun>("/analysis-runs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
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

async function fetchEvidenceFeed(): Promise<typeof evidenceFeed> {
  return [];
}

export async function getEvidenceFeed() {
  if (USE_MOCK) return evidenceFeed;
  return fetchEvidenceFeed();
}
