export type RiskLevel = "high" | "mid" | "low";

export interface AnalysisSummary {
  id: string;
  code: string;
  name: string;
  industry: string;
  listingDate: string;
  offerPrice: number;
  offerSize: string;
  riskLevel: RiskLevel;
  riskScore: number;
  evidenceCount: number;
  highConfidence: number;
  mode: "quick" | "deep" | "expert";
  createdAt: string;
  experts: number;
}

export interface Agent {
  id: string;
  name: string;
  role: string;
  layer: "L3" | "L2" | "L1";
  desc: string;
  expertise: string[];
  callCount: number;
  avgTokens: number;
  avatar: string;
  accent: string;
}

export interface BacktestSummary {
  id: string;
  name: string;
  model: string;
  modelVersion: string;
  samples: number;
  auc: number;
  ks: number;
  lift10: number;
  recall: number;
  createdAt: string;
  status: "完成" | "运行中" | "失败";
  author: string;
}

export interface DashboardStats {
  totalAnalyses: number;
  evidenceCount: number;
  conclusions: number;
  crossValidationRate: number;
  hoursSaved: number;
  efficiency: number;
  avgSources: number;
  tokenK: number;
}

export interface HomepageExample {
  icon: string;
  title: string;
  desc: string;
}

export interface ReportData {
  id: string;
  markdown: string;
  reviewStatus: string;
  createdAt: string;
  toc: string[];
  radar: { dim: string; score: number }[];
  pie: { name: string; value: number }[];
  waterfall: { name: string; value: number }[];
  cashflow: { y: string; cfo: number; ni: number; cap: number }[];
  palette: string[];
}

export interface TraceStage {
  key: string;
  label: string;
  owner: string;
  status: "done" | "running" | "queued" | "failed";
  cost?: string | null;
}

export interface TraceSeedItem {
  who: string;
  kind: "execute" | "discover" | "challenge" | "verdict" | "assign";
  text: string;
  t: string;
}

export interface TraceFlowItem {
  who: string;
  kind: "execute" | "discover" | "challenge" | "verdict" | "assign";
  text: string;
  t: string;
}

export interface EvidenceFeedItem {
  id: string;
  source: string;
  title: string;
  conf: number;
  time: string;
  page?: number | null;
  snippet?: string | null;
}

export interface TraceData {
  stages: TraceStage[];
  seed: TraceSeedItem[];
  flow: TraceFlowItem[];
  evidenceFeed: EvidenceFeedItem[];
}

export interface EvidenceDetailItem {
  id: string;
  section: string;
  page: number;
  conf: number;
  snippet: string;
  risk: string;
  documentId: string | null;
  pdfUrl: string | null;
  bbox: number[];
  bboxRegions: EvidenceBBoxRegion[];
  bboxCoordinateSpace: BBoxCoordinateSpace;
}

export interface EvidenceBBoxRegion {
  page: number;
  bbox: number[];
}

export interface BBoxCoordinateSpace {
  width: number;
  height: number;
  origin: "top-left";
}

export interface KnowledgePreview {
  evidenceId: string;
  code: string;
  section: string;
  page: number;
  linkedConclusions: string[];
}

export interface KnowledgeStats {
  totalEvidence: number;
  totalCompanies: number;
}

export interface KnowledgeFacet {
  label: string;
  items: string[];
}

export interface KnowledgeEvidence {
  id: string;
  code: string;
  name: string;
  section: string;
  page: number;
  conf: number;
  snippet: string;
  risk: string;
  documentId: string | null;
  pdfUrl: string | null;
  bbox: number[];
  bboxRegions: EvidenceBBoxRegion[];
  bboxCoordinateSpace: BBoxCoordinateSpace;
}

export interface KnowledgeData {
  facets: KnowledgeFacet[];
  evidence: KnowledgeEvidence[];
  stats: KnowledgeStats;
  preview: KnowledgePreview;
}

export interface BacktestDetailData {
  roc: { fpr: number; tpr: number }[];
  groupRet: { d: string; high: number; mid: number; low: number; hsi: number }[];
  lift: { p: string; lift: number; baseline: number }[];
  cm: { label: string; n: number; hint: string; tone: "ok" | "warn" }[];
  fnSamples: {
    code: string;
    name: string;
    actual: number;
    score: number;
    reason: string;
    risk: RiskLevel;
  }[];
  fpSamples: {
    code: string;
    name: string;
    actual: number;
    score: number;
    reason: string;
    risk: RiskLevel;
  }[];
  palette: string[];
  hoursSaved: number;
  factorContributions: { f: string; v: number }[];
  precision: number;
  recall: number;
  f1: number;
}

export interface UploadedFile {
  name: string;
  pages: number;
  size: string;
  uploadedAt: string;
}

export interface IntakeCheck {
  label: string;
  value: string;
  ok: boolean;
}

export interface IntakeParsing {
  label: string;
  pct: number;
  status: string;
}

export interface IntakeData {
  checks: IntakeCheck[];
  parsing: IntakeParsing[];
  uploadedFile: UploadedFile;
  progressText: string;
}

export interface RiskRule {
  rule_id: string;
  dimension_code: string;
  dimension: string;
  rule_type: string;
  category: string;
  risk_item: string;
  description: string;
  trigger_condition: string;
  calculation_or_method: string;
  source_sections: string;
  enabled: boolean;
}

export interface RiskDimension {
  dimension_code: string;
  dimension: string;
  rule_type: string;
  category_count: number;
  rule_count: number;
  description?: string;
  categories: { name: string; rule_count: number }[];
}

export interface RiskRulesData {
  rule_set_id?: string;
  version?: string;
  name?: string;
  source_workbook?: string;
  source_main_sheet?: string;
  source_summary_sheet?: string;
  scope?: string;
  usage_notes: string[];
  totals: {
    dimension_count: number;
    category_count: number;
    rule_count: number;
  };
  dimensions: RiskDimension[];
  rules: RiskRule[];
}

export interface BacktestNewStep {
  key: string;
  label: string;
  desc: string;
}

export interface BacktestNewData {
  steps: BacktestNewStep[];
}
