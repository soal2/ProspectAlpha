import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  UploadCloud,
  Check,
  FileText,
  Loader2,
  ArrowRight,
  Search,
  Settings2,
  Users,
  ShieldAlert,
  Send,
  Sparkles,
} from "lucide-react";
import { useRef, useState, useEffect } from "react";
import {
  askDocument,
  createAnalysisDraft,
  getAnalysisOptions,
  getIntakeData,
  startAnalysisRun,
  uploadDocument,
  type AnalysisRun,
  type AnalysisOptions,
  type AskCitation,
} from "@/lib/data-service";

export const Route = createFileRoute("/intake")({
  head: () => ({
    meta: [
      { title: "新建 IPO 风险分析 · ProspectAlpha IPO RiskAgent" },
      {
        name: "description",
        content: "选择标的、准备材料、配置风险规则，由战略层动态组建 Agent 团队。",
      },
    ],
  }),
  component: IntakePage,
});

const workflowSteps = [
  { label: "标的选择", desc: "公司名称 / 申请编号 / URL", icon: Search },
  { label: "材料准备", desc: "招股书 PDF 与基础信息", icon: FileText },
  { label: "分析配置", desc: "规则版本 / 分析深度 / 知识库", icon: Settings2 },
  { label: "启动分析", desc: "解析后派发 Agent 协同", icon: ArrowRight },
];

const sampleText =
  "招股书披露公司存在关联交易及对赌/赎回安排，前五大供应商采购占比较高。" +
  "报告期内经营活动现金流持续为负，且部分章节存在前瞻性陈述和免责声明。";

const sampleStructuredData = "供应商集中度=Top5 采购占比 80%\n经营活动现金流=连续两期为负";

function IntakePage() {
  const navigate = useNavigate();
  const [data, setData] = useState<{
    checks: { label: string; value: string; ok: boolean }[];
    parsing: { label: string; pct: number; status: string }[];
    uploadedFile: { name: string; pages: number; size: string; uploadedAt: string };
    progressText: string;
  } | null>(null);
  const [options, setOptions] = useState<AnalysisOptions | null>(null);
  const [companyName, setCompanyName] = useState("宁德时代 · A1 申请稿");
  const [documentText, setDocumentText] = useState(sampleText);
  const [structuredDataText, setStructuredDataText] = useState(sampleStructuredData);
  const [analysisRun, setAnalysisRun] = useState<AnalysisRun | null>(null);
  const [draftRun, setDraftRun] = useState<AnalysisRun | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [selectedRuleSetId, setSelectedRuleSetId] = useState("");
  const [analysisDepth, setAnalysisDepth] = useState<"quick" | "standard" | "deep">("standard");
  const [uploadedDocument, setUploadedDocument] = useState<{
    document_id: string;
    filename: string;
    page_count: number;
    file_size_bytes: number;
    created_at: string;
  } | null>(null);
  const [pdfQuestion, setPdfQuestion] = useState("");
  const [pdfAnswer, setPdfAnswer] = useState("");
  const [pdfCitations, setPdfCitations] = useState<AskCitation[]>([]);
  const [pdfAsking, setPdfAsking] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const pdfAbortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getIntakeData(), getAnalysisOptions()]).then(([intake, loadedOptions]) => {
      if (cancelled) return;
      setData(intake);
      setOptions(loadedOptions);
      setSelectedRuleSetId(
        loadedOptions.risk_rule_sets.find((item) => item.active)?.rule_set_id ?? "",
      );
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <div className="px-6 py-8 sm:px-10">
      <div>
        <h1 className="font-serif text-3xl font-semibold">新建 IPO 风险分析</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          选择标的、准备材料、校验元数据、配置分析方案，然后启动 Agent 协同
        </p>
      </div>

      <div className="mt-6 grid gap-3 md:grid-cols-4">
        {workflowSteps.map((step, index) => (
          <div key={step.label} className="rounded-xl border border-border bg-card p-4">
            <div className="flex items-center justify-between">
              <span className="tabular text-[11px] text-muted-foreground">
                {String(index + 1).padStart(2, "0")}
              </span>
              <step.icon className="size-4 text-primary" />
            </div>
            <div className="mt-3 text-sm font-medium">{step.label}</div>
            <div className="mt-1 text-xs text-muted-foreground">{step.desc}</div>
          </div>
        ))}
      </div>

      <div className="mt-8 grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
        <div className="space-y-6">
          <div className="rounded-2xl border border-border bg-card p-6">
            <div className="flex items-center gap-3">
              <div className="grid size-10 place-items-center rounded-lg bg-primary/10 text-primary">
                <Search className="size-5" />
              </div>
              <div>
                <h2 className="font-serif text-xl font-semibold">标的选择</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  输入公司名称、申请编号、招股书 URL 或拟上市代码（如已分配）
                </p>
              </div>
            </div>
            <div className="mt-5 flex flex-col gap-3 sm:flex-row">
              <input
                value={companyName}
                onChange={(event) => setCompanyName(event.target.value)}
                className="h-10 flex-1 rounded-md border border-border bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                aria-label="分析标的"
              />
              <button className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-border bg-background px-4 text-sm font-medium hover:bg-accent">
                <Search className="size-4" /> 匹配标的
              </button>
            </div>
          </div>

          {/* Upload card */}
          <div className="rounded-2xl border border-dashed border-primary/30 bg-gradient-to-br from-[color:var(--sage-soft)]/40 to-[color:var(--paper)] p-8">
            <div className="grid size-12 place-items-center rounded-xl bg-primary/10 text-primary">
              <UploadCloud className="size-6" />
            </div>
            <h2 className="mt-4 font-serif text-xl font-semibold">材料准备</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              上传招股书 PDF，系统将绑定公司基础信息并准备进入解析流程
            </p>
            <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center">
              <input
                ref={fileInputRef}
                type="file"
                accept="application/pdf,.pdf"
                className="hidden"
                onChange={(event) => {
                  const file = event.currentTarget.files?.[0];
                  if (file) void handlePdfUpload(file);
                }}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                className="inline-flex items-center justify-center gap-2 rounded-full bg-primary px-5 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
              >
                <UploadCloud className="size-4" /> {uploadedDocument ? "重新选择 PDF" : "选择文件"}
              </button>
              <span className="text-xs text-muted-foreground">或拖拽 PDF 到此处</span>
            </div>

            {(uploadedDocument || data?.uploadedFile.name) && (
              <div className="mt-6 rounded-xl border border-border bg-card/80 p-4">
                <div className="flex items-center gap-3">
                  <FileText className="size-5 text-primary" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">
                      {uploadedDocument?.filename ?? data?.uploadedFile.name}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {uploadedDocument
                        ? `${uploadedDocument.page_count} 页 · ${formatBytes(uploadedDocument.file_size_bytes)} · 已解析`
                        : `${data?.uploadedFile.pages} 页 · ${data?.uploadedFile.size} · 上传于 ${data?.uploadedFile.uploadedAt}`}
                    </div>
                  </div>
                  <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                    已接入
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* PDF Q&A card */}
          <div className="rounded-2xl border border-border bg-card p-6">
            <div className="flex items-center gap-3">
              <div className="grid size-10 place-items-center rounded-lg bg-primary/10 text-primary">
                <Sparkles className="size-5" />
              </div>
              <div>
                <h2 className="font-serif text-xl font-semibold">PDF 自由提问</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  基于已上传的招股书实时调用大模型，回答流式输出
                </p>
              </div>
            </div>
            <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-start">
              <textarea
                value={pdfQuestion}
                onChange={(event) => setPdfQuestion(event.target.value)}
                placeholder={
                  uploadedDocument
                    ? "例如：这家公司有哪些核心风险因素？经营性现金流如何？"
                    : "请先上传招股书 PDF 后再提问"
                }
                rows={3}
                disabled={pdfAsking || !uploadedDocument}
                className="field-input flex-1 resize-none disabled:cursor-not-allowed disabled:opacity-60"
              />
              <button
                onClick={() => void askPdf()}
                disabled={pdfAsking || !uploadedDocument || !pdfQuestion.trim()}
                className="inline-flex items-center justify-center gap-2 rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {pdfAsking ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Send className="size-4" />
                )}
                提问
              </button>
            </div>

            {pdfError ? (
              <div className="mt-3 rounded-md border border-destructive/30 bg-destructive/8 px-3 py-2 text-xs text-destructive">
                {pdfError}
              </div>
            ) : null}

            {pdfAnswer || pdfAsking ? (
              <div className="mt-4 rounded-xl border border-border bg-background p-4">
                <div className="mb-2 flex items-center justify-between text-[11px] text-muted-foreground">
                  <span className="inline-flex items-center gap-1.5 font-medium">
                    <Sparkles className="size-3 text-primary" /> 大模型回答
                  </span>
                  {pdfAsking ? (
                    <span className="inline-flex items-center gap-1 tabular">
                      <Loader2 className="size-3 animate-spin" /> 生成中
                    </span>
                  ) : (
                    <span className="tabular">{pdfAnswer.length} 字</span>
                  )}
                </div>
                <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-foreground/90">
                  {pdfAnswer}
                  {pdfAsking ? (
                    <span className="ml-0.5 inline-block h-3 w-0.5 animate-pulse bg-primary align-middle" />
                  ) : null}
                </pre>

                {pdfCitations.length > 0 ? (
                  <div className="mt-3 border-t border-border/60 pt-3">
                    <div className="text-[10.5px] font-medium uppercase tracking-wider text-muted-foreground">
                      引用证据（{pdfCitations.length}）
                    </div>
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {pdfCitations.map((c) => (
                        <span
                          key={c.block_id}
                          title={c.snippet}
                          className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/40 px-2 py-0.5 text-[10.5px] text-muted-foreground"
                        >
                          <FileText className="size-3 text-primary" />
                          P.{c.page_number} · {c.section_path}
                        </span>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>

        <div className="space-y-6">
          {/* Metadata verify */}
          <div className="rounded-2xl border border-border bg-card p-6">
            <div className="flex items-center justify-between">
              <h2 className="font-serif text-xl font-semibold">元数据自动校验</h2>
              {/* <span className="text-xs text-muted-foreground">来自港股基本信息表</span> */}
            </div>
            <div className="mt-4 divide-y divide-border rounded-xl border border-border">
              {data?.checks.map((c) => (
                <div
                  key={c.label}
                  className="flex items-center justify-between px-4 py-2.5 text-sm"
                >
                  <span className="text-muted-foreground">{c.label}</span>
                  <span className="flex items-center gap-2">
                    <span className="tabular">{c.value}</span>
                    {c.ok ? (
                      <Check className="size-4 text-[color:var(--risk-low)]" />
                    ) : (
                      <span className="rounded-full bg-[color:var(--risk-mid)]/15 px-2 py-0.5 text-[10px] text-[oklch(0.45_0.12_75)]">
                        人工核对
                      </span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-border bg-card p-6">
            <div className="flex items-center justify-between">
              <h2 className="font-serif text-xl font-semibold">分析配置</h2>
              <span className="rounded-full bg-primary/8 px-2.5 py-1 text-[11px] font-medium text-primary">
                真实配置
              </span>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              选择风险规则与分析深度；专家编队将在任务启动后由战略层实时生成
            </p>
            <div className="mt-4 space-y-4">
              <label className="block text-xs font-medium text-muted-foreground">
                风险规则
                <select
                  value={selectedRuleSetId}
                  onChange={(event) => setSelectedRuleSetId(event.target.value)}
                  className="mt-1 h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  {options?.risk_rule_sets.map((ruleSet) => (
                    <option key={ruleSet.rule_set_id} value={ruleSet.rule_set_id}>
                      {ruleSet.name} · {ruleSet.version} · {ruleSet.rule_count} 条
                    </option>
                  ))}
                </select>
              </label>

              <div>
                <div className="text-xs font-medium text-muted-foreground">Agent 编队</div>
                <div className="mt-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-3">
                  <div className="flex items-start gap-2">
                    <Sparkles className="mt-0.5 size-4 shrink-0 text-primary" />
                    <div>
                      <div className="text-sm font-medium">战略层动态选用专家</div>
                      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                        启动后将实时读取 {options?.agents.length ?? 0}{" "}
                        位可用专家的能力卡，按任务模块生成编队；此处无需预设固定执行链。
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              <div>
                <div className="text-xs font-medium text-muted-foreground">分析深度</div>
                <div className="mt-2 grid grid-cols-3 gap-2">
                  {options?.analysis_depths.map((depth) => (
                    <button
                      key={depth.depth}
                      type="button"
                      onClick={() => setAnalysisDepth(depth.depth)}
                      className={`rounded-lg border px-3 py-2 text-left text-sm ${
                        analysisDepth === depth.depth
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-border bg-background"
                      }`}
                    >
                      <span className="block font-medium">{depth.label}</span>
                      <span className="block text-[11px] text-muted-foreground">
                        上限 {depth.max_findings} 条
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Parsing progress */}
      <div className="mt-8 rounded-2xl border border-border bg-card p-6">
        <div className="flex items-center justify-between">
          <h2 className="font-serif text-xl font-semibold">启动分析准备</h2>
          <span className="text-xs text-muted-foreground">{data?.progressText}</span>
        </div>
        <div className="mt-4 space-y-3">
          {data?.parsing.map((p) => (
            <div key={p.label}>
              <div className="flex items-center justify-between text-xs">
                <span className="flex items-center gap-2">
                  {p.status === "done" && (
                    <Check className="size-3.5 text-[color:var(--risk-low)]" />
                  )}
                  {p.status === "running" && (
                    <Loader2 className="size-3.5 animate-spin text-primary" />
                  )}
                  {p.status === "queued" && (
                    <span className="size-2 rounded-full bg-muted-foreground/40" />
                  )}
                  {p.label}
                </span>
                <span className="tabular text-muted-foreground">{p.pct}%</span>
              </div>
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary transition-all"
                  style={{ width: `${p.pct}%` }}
                />
              </div>
            </div>
          ))}
        </div>
        <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
          <p className="text-xs text-muted-foreground">
            文档解析完成后可派发到多 Agent 协同分析。所有上市后行情仅用于回测，不会进入分析输入。
          </p>
          <button
            type="button"
            onClick={() => void createDraftForConfirmation()}
            disabled={analyzing || (!documentText.trim() && !uploadedDocument)}
            className="inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {analyzing ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <ArrowRight className="size-4" />
            )}
            {draftRun ? "重新生成确认信息" : "识别任务并确认"}
          </button>
        </div>
        {draftRun ? (
          <div className="mt-5 rounded-xl border border-primary/20 bg-primary/5 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="text-sm font-semibold">确认本次港股 IPO 招股书风险调研任务</div>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  我们识别到你上传或选择的是「
                  {draftRun.understood?.company_name ?? draftRun.company_name}」
                  的招股书材料。本次任务将重点分析财务风险、业务风险、合规风险、估值风险与上市后破发风险。
                </p>
                <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                  <span className="rounded-full bg-background px-2 py-1">
                    类型：港股 IPO 招股书风险分析
                  </span>
                  <span className="rounded-full bg-background px-2 py-1">
                    规则：{draftRun.rule_snapshot.version}
                  </span>
                  <span className="rounded-full bg-background px-2 py-1">专家：战略层动态选择</span>
                  <span className="rounded-full bg-background px-2 py-1">
                    深度：{draftRun.analysis_config.analysis_depth}
                  </span>
                </div>
                {draftRun.blocking_questions?.length ? (
                  <div className="mt-3 rounded-md border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                    {draftRun.blocking_questions.join("；")}
                  </div>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => void startConfirmedDraft()}
                disabled={analyzing || draftRun.status !== "ready"}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {analyzing ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <ArrowRight className="size-4" />
                )}
                开始专家协作分析
              </button>
            </div>
          </div>
        ) : null}
      </div>

      <div className="mt-8 grid gap-6 xl:grid-cols-[0.95fr_1.05fr]">
        <div className="rounded-2xl border border-border bg-card p-6">
          <div className="flex items-center gap-3">
            <div className="grid size-10 place-items-center rounded-lg bg-primary/10 text-primary">
              <ShieldAlert className="size-5" />
            </div>
            <div>
              <h2 className="font-serif text-xl font-semibold">规则 Agent 试运行</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                使用当前 active 规则快照，对招股书摘录和结构化数据生成可回溯 finding
              </p>
            </div>
          </div>

          <div className="mt-5 space-y-4">
            <label className="block text-xs font-medium text-muted-foreground">
              招股书文本 / 解析片段
              <textarea
                value={documentText}
                onChange={(event) => setDocumentText(event.target.value)}
                rows={7}
                className="field-input mt-1 resize-none"
              />
            </label>
            <label className="block text-xs font-medium text-muted-foreground">
              结构化数据（每行 key=value）
              <textarea
                value={structuredDataText}
                onChange={(event) => setStructuredDataText(event.target.value)}
                rows={4}
                className="field-input mt-1 resize-none"
              />
            </label>
          </div>

          {analysisError ? (
            <div className="mt-4 rounded-md border border-destructive/30 bg-destructive/8 px-3 py-2 text-xs text-destructive">
              {analysisError}
            </div>
          ) : null}

          <button
            onClick={() => void runRuleAgent(false)}
            disabled={analyzing || (!documentText.trim() && !uploadedDocument)}
            className="mt-5 inline-flex items-center gap-2 rounded-full bg-primary px-5 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {analyzing ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <ArrowRight className="size-4" />
            )}
            启动规则 Agent
          </button>
        </div>

        <div className="rounded-2xl border border-border bg-card p-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="font-serif text-xl font-semibold">分析结果</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                {analysisRun
                  ? `${analysisRun.run_id} · ${analysisRun.findings.length} 条 finding`
                  : "等待规则 Agent 输出"}
              </p>
            </div>
            {analysisRun ? (
              <span className="rounded-full bg-[color:var(--risk-low)]/10 px-2.5 py-1 text-[11px] font-medium text-[color:var(--risk-low)]">
                {analysisRun.status}
              </span>
            ) : null}
          </div>

          {analysisRun ? (
            <div className="mt-4 space-y-3">
              <div className="grid grid-cols-3 gap-2 text-center text-[11px]">
                <MiniStat label="规则快照" value={analysisRun.rule_snapshot.totals.rule_count} />
                <MiniStat label="输入字数" value={analysisRun.input_summary.document_text_chars} />
                <MiniStat
                  label="结构化项"
                  value={analysisRun.input_summary.structured_data_keys.length}
                />
              </div>
              <div className="max-h-[520px] space-y-3 overflow-y-auto pr-1">
                {analysisRun.findings.map((finding) => (
                  <div
                    key={finding.finding_id}
                    className="rounded-xl border border-border bg-background p-4"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                          <span className="tabular">{finding.rule_id}</span>
                          <span>{finding.dimension_code}</span>
                          <span>{finding.category}</span>
                        </div>
                        <div className="mt-1 font-medium leading-snug">{finding.title}</div>
                      </div>
                      <span
                        className={`rounded-full px-2 py-0.5 text-[11px] ${
                          finding.severity === "high"
                            ? "bg-[color:var(--risk-high)]/10 text-[color:var(--risk-high)]"
                            : finding.severity === "medium"
                              ? "bg-[color:var(--risk-mid)]/12 text-[oklch(0.45_0.12_75)]"
                              : "bg-[color:var(--risk-low)]/10 text-[color:var(--risk-low)]"
                        }`}
                      >
                        {finding.severity}
                      </span>
                    </div>
                    <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                      {finding.reasoning_summary}
                    </p>
                    {finding.evidence_snippets[0] ? (
                      <blockquote className="mt-3 border-l-2 border-primary/40 pl-3 text-xs leading-relaxed">
                        {finding.evidence_snippets[0]}
                      </blockquote>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="mt-12 text-center text-xs text-muted-foreground">
              Agent 输出会展示规则编号、触发条件、证据片段和置信度。
            </div>
          )}
        </div>
      </div>
    </div>
  );

  async function runRuleAgent(openAfterComplete: boolean) {
    setAnalyzing(true);
    setAnalysisError(null);
    try {
      if (!selectedRuleSetId) throw new Error("请先选择风险规则");
      const draft = await createAnalysisDraft(buildPayload());
      setDraftRun(draft);
      if (draft.status !== "ready") {
        setAnalysisRun(draft);
        return;
      }
      const run = await startAnalysisRun(draft.run_id);
      setAnalysisRun(run);
      if (openAfterComplete) {
        await navigate({ to: "/analyses/$id", params: { id: draft.run_id } });
      }
    } catch (err) {
      setAnalysisError(err instanceof Error ? err.message : "规则 Agent 分析失败");
    } finally {
      setAnalyzing(false);
    }
  }

  async function createDraftForConfirmation() {
    setAnalyzing(true);
    setAnalysisError(null);
    try {
      if (!selectedRuleSetId) throw new Error("请先选择风险规则");
      const run = await createAnalysisDraft(buildPayload());
      setDraftRun(run);
      setAnalysisRun(run);
    } catch (err) {
      setAnalysisError(err instanceof Error ? err.message : "任务识别失败");
    } finally {
      setAnalyzing(false);
    }
  }

  async function startConfirmedDraft() {
    if (!draftRun) return;
    setAnalyzing(true);
    setAnalysisError(null);
    try {
      const run = await startAnalysisRun(draftRun.run_id);
      setAnalysisRun(run);
      await navigate({ to: "/analyses/$id", params: { id: draftRun.run_id } });
    } catch (err) {
      setAnalysisError(err instanceof Error ? err.message : "启动专家协作失败");
    } finally {
      setAnalyzing(false);
    }
  }

  function buildPayload() {
    return {
      company_name: companyName,
      document_text: uploadedDocument ? "" : documentText,
      document_id: uploadedDocument?.document_id,
      risk_rule_set_id: selectedRuleSetId,
      analysis_depth: analysisDepth,
      structured_data: parseStructuredData(structuredDataText),
      max_findings:
        options?.analysis_depths.find((item) => item.depth === analysisDepth)?.max_findings ?? 20,
    };
  }

  async function handlePdfUpload(file: File) {
    setAnalysisError(null);
    try {
      const document = await uploadDocument(file);
      setUploadedDocument(document);
      // reset prior Q&A when a new doc is uploaded
      setPdfAnswer("");
      setPdfCitations([]);
      setPdfError(null);
    } catch (err) {
      setAnalysisError(err instanceof Error ? err.message : "PDF 上传失败");
    }
  }

  async function askPdf() {
    if (!uploadedDocument || !pdfQuestion.trim()) return;
    setPdfAsking(true);
    setPdfError(null);
    setPdfAnswer("");
    setPdfCitations([]);
    const controller = new AbortController();
    pdfAbortRef.current = controller;
    try {
      const result = await askDocument(
        uploadedDocument.document_id,
        pdfQuestion.trim(),
        (_delta, full) => setPdfAnswer(full),
        { signal: controller.signal },
      );
      setPdfAnswer(result.answer);
      setPdfCitations(result.citations);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setPdfError(err instanceof Error ? err.message : "PDF 问答失败");
    } finally {
      setPdfAsking(false);
      pdfAbortRef.current = null;
    }
  }
}

function MiniStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg bg-muted px-3 py-2">
      <div className="tabular text-base font-semibold">{value}</div>
      <div className="text-muted-foreground">{label}</div>
    </div>
  );
}

function parseStructuredData(text: string): Record<string, string> {
  return Object.fromEntries(
    text
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const [key, ...rest] = line.split("=");
        return [key.trim(), rest.join("=").trim()];
      })
      .filter(([key, value]) => key && value),
  );
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
