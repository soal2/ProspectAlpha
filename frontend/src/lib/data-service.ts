import { USE_MOCK } from "./env";
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

async function fetchRiskRulesData(): Promise<typeof riskRulesData> {
  return {
    taxonomy: [
      { key: "financial", label: "财务风险", color: "var(--chart-1)", rules: [] },
      { key: "business", label: "经营风险", color: "var(--chart-2)", rules: [] },
      { key: "legal", label: "法律合规风险", color: "var(--chart-4)", rules: [] },
      { key: "market", label: "市场风险", color: "var(--chart-5)", rules: [] },
    ],
  };
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

// Risk Rules
export async function getRiskRulesData() {
  if (USE_MOCK) return riskRulesData;
  return fetchRiskRulesData();
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
