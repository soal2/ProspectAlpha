import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  ArrowRight,
  UploadCloud,
  FileText,
  ShieldAlert,
  LineChart,
  Building2,
  Loader2,
  Send,
  CheckCircle2,
  AlertCircle,
  Users,
  Sparkles,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  createAnalysisDraft,
  getAgents,
  getAnalyses,
  getAnalysisOptions,
  listDocuments,
  uploadDocument,
  waitForDocumentReady,
  type AnalysisOptions,
  type UploadedDocument,
} from "@/lib/data-service";
import { type Agent, type AnalysisSummary } from "@/lib/types";
import { motion } from "framer-motion";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "工作台 · ProspectAlpha" },
      {
        name: "description",
        content:
          "ProspectAlpha — 机构级港股 IPO 风险智能终端：招股书解析、风险归因、链路回溯与上市后预警。",
      },
    ],
  }),
  component: WorkspacePage,
});

function inferCompanyName(text: string, document?: UploadedDocument | null) {
  const trimmed = text.trim();
  const quoted = trimmed.match(/[「《“"]([^」》”"]{2,40})[」》”"]/);
  if (quoted?.[1]) return quoted[1];
  if (document?.filename && /(?:该|上传的)\s*PDF/i.test(trimmed)) {
    return document.filename.replace(/\.pdf$/i, "");
  }
  const companyMatch = trimmed.match(
    /([\u4e00-\u9fa5A-Za-z0-9-]{2,40})(?:公司|集团|科技|时代|控股|生物|健康|股份|有限)?/,
  );
  if (companyMatch?.[0] && !["请", "帮我", "对该", "根据"].includes(companyMatch[0])) {
    return companyMatch[0];
  }
  if (document?.filename) return document.filename.replace(/\.pdf$/i, "");
  return "待确认 IPO 企业";
}

function inferFocus(text: string) {
  const focus = [
    "现金流",
    "对赌",
    "赎回",
    "关联交易",
    "供应商集中",
    "客户集中",
    "估值",
    "破发",
  ].filter((keyword) => text.includes(keyword));
  return focus.length ? focus.join("、") : "招股书 IPO 上市风险全量审核";
}

function createSessionId() {
  if (typeof window === "undefined") return "#IPO-DESK";
  const now = new Date();
  const hkt = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Hong_Kong" }));
  const dateKey = `${hkt.getFullYear()}${hkt.getMonth()}${hkt.getDate()}`;
  const storageKey = `sid-${dateKey}`;
  const stored = window.sessionStorage.getItem(storageKey);
  if (stored) return stored;
  const prefix = String.fromCharCode(65 + Math.floor(Math.random() * 26));
  const num = String(hkt.getHours()).padStart(2, "0") + String(hkt.getMinutes()).padStart(2, "0");
  const id = `#${prefix}-${num}`;
  window.sessionStorage.setItem(storageKey, id);
  return id;
}

function WorkspacePage() {
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const processingDocumentIdRef = useRef<string | null>(null);
  const [analyses, setAnalyses] = useState<AnalysisSummary[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [options, setOptions] = useState<AnalysisOptions | null>(null);
  const [sessionTime, setSessionTime] = useState("");
  const [requestText, setRequestText] = useState(
    "请根据我上传的 PDF，对该 PDF 所示企业进行 IPO 上市风险审核。",
  );
  const [uploadedDocument, setUploadedDocument] = useState<UploadedDocument | null>(null);
  const [availableDocuments, setAvailableDocuments] = useState<UploadedDocument[]>([]);
  const [uploading, setUploading] = useState(false);
  const [documentProcessing, setDocumentProcessing] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [dispatching, setDispatching] = useState(false);
  const [flowError, setFlowError] = useState<string | null>(null);
  const [blockingQuestions, setBlockingQuestions] = useState<string[]>([]);
  const [sessionId, setSessionId] = useState("#IPO-DESK");

  useEffect(() => {
    setSessionId(createSessionId());
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getAnalyses(), getAgents(), getAnalysisOptions(), listDocuments()]).then(
      ([analysesData, agentsData, optionsData, documentsData]) => {
        if (cancelled) return;
        setAnalyses(analysesData);
        setAgents(agentsData);
        setOptions(optionsData);
        setAvailableDocuments(documentsData);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const tick = () => {
      const now = new Date();
      const hkt = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Hong_Kong" }));
      const hours = String(hkt.getHours()).padStart(2, "0");
      const minutes = String(hkt.getMinutes()).padStart(2, "0");
      setSessionTime(`${hours}:${minutes}`);
    };
    tick();
    const interval = setInterval(tick, 60000);
    return () => clearInterval(interval);
  }, []);

  const featured = analyses.slice(0, 3);
  const companyName = useMemo(
    () => inferCompanyName(requestText, uploadedDocument),
    [requestText, uploadedDocument],
  );
  const focusDisplay = useMemo(() => inferFocus(requestText), [requestText]);
  const activeRuleSet =
    options?.risk_rule_sets.find((item) => item.active) ?? options?.risk_rule_sets[0];
  const deepDepth = options?.analysis_depths.find((item) => item.depth === "deep");
  const canConfirm = Boolean(
    uploadedDocument && uploadedDocument.block_count > 0 && requestText.trim() && !uploading,
  );

  return (
    <div className="relative isolate">
      <div className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[560px] terminal-grid opacity-70" />
      <div className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[560px] bg-gradient-to-b from-white via-white/60 to-transparent" />
      <div className="pointer-events-none absolute right-8 top-16 -z-10 hidden lg:block">
        <div className="size-56 rounded-full border border-[color:var(--gold)]/30" />
        <div className="absolute inset-6 rounded-full border border-[color:var(--gold)]/15" />
      </div>

      <div className="mx-auto max-w-6xl px-6 pb-20 pt-10 sm:pt-14">
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center gap-2 rounded-sm border border-[color:var(--gold)]/40 bg-white px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.2em] text-[color:var(--navy)]">
            <span className="size-1.5 rounded-full bg-[color:var(--gold)]" />
            Live · Prospectus Risk Desk
          </span>
          <span className="hidden text-[11px] uppercase tracking-[0.2em] text-muted-foreground sm:inline">
            Session · Soal2 · HKT {sessionTime || "--:--"}
          </span>
        </div>

        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="mt-10 text-center"
        >
          <h1 className="mx-auto max-w-4xl font-serif text-[40px] font-semibold leading-[1.1] tracking-tight text-[color:var(--navy)] sm:text-[52px]">
            Institutional IPO
            <br className="hidden sm:block" /> Risk Intelligence
          </h1>
          <div className="mx-auto mt-6 h-px w-16 bg-[color:var(--gold)]" />
          <p className="mx-auto mt-6 max-w-2xl text-[13px] leading-relaxed text-muted-foreground sm:text-sm">
            上传招股书 PDF，确认分析范围后直接派发 Agent 专家团 · 证据先行 · 链路可追溯 ·
            无证据不立论
          </p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1, duration: 0.5 }}
          className="mx-auto mt-10 max-w-3xl"
        >
          <div
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              const file = event.dataTransfer.files?.[0];
              if (file) void handlePdfUpload(file);
            }}
            className={`soft-ring overflow-hidden rounded-md border bg-white transition ${
              dragging
                ? "border-[color:var(--gold)] ring-4 ring-[color:var(--gold)]/15"
                : "border-border"
            }`}
          >
            <div className="flex items-center justify-between border-b border-border/80 bg-[color:var(--muted)]/60 px-4 py-2">
              <div className="flex items-center gap-2">
                <span className="size-1.5 rounded-full bg-[color:var(--gold)]" />
                <span className="eyebrow text-[color:var(--navy)]/70">
                  PDF IPO Analysis Terminal
                </span>
              </div>
              <span className="tabular text-[10px] uppercase tracking-wider text-muted-foreground">
                SESSION {sessionId}
              </span>
            </div>

            <textarea
              value={requestText}
              onChange={(event) => setRequestText(event.target.value)}
              placeholder="请上传 PDF，并说明：对该 PDF 所示企业进行 IPO 上市风险审核。"
              rows={4}
              className="w-full resize-none bg-transparent px-5 py-4 text-[13.5px] leading-relaxed text-foreground placeholder:text-muted-foreground focus:outline-none"
            />

            <div className="border-t border-border bg-white px-4 py-3">
              <input
                ref={fileInputRef}
                type="file"
                accept="application/pdf,.pdf"
                className="hidden"
                onChange={(event) => {
                  const file = event.currentTarget.files?.[0];
                  if (file) void handlePdfUpload(file);
                  event.currentTarget.value = "";
                }}
              />

              {uploadedDocument ? (
                <div className="mb-3 flex items-center gap-3 rounded-sm border border-[color:var(--gold)]/30 bg-[color:var(--gold)]/8 px-3 py-2 text-left">
                  <FileText className="size-4 shrink-0 text-[color:var(--gold)]" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[12.5px] font-medium text-[color:var(--navy)]">
                      {uploadedDocument.filename}
                    </div>
                    <div className="mt-0.5 text-[10.5px] text-muted-foreground">
                      {uploadedDocument.page_count || "--"} 页 ·{" "}
                      {formatBytes(uploadedDocument.file_size_bytes)} · 已接入
                    </div>
                  </div>
                  <CheckCircle2 className="size-4 shrink-0 text-[color:var(--risk-low)]" />
                </div>
              ) : null}

              {availableDocuments.length > 0 ? (
                <label className="mb-3 block text-left">
                  <span className="mb-1.5 block text-[11px] font-medium text-muted-foreground">
                    选择数据库中已有的招股书（无需重新上传或解析）
                  </span>
                  <select
                    aria-label="选择已有招股书"
                    value={uploadedDocument?.document_id ?? ""}
                    onChange={(event) => {
                      const selected = availableDocuments.find(
                        (document) => document.document_id === event.target.value,
                      );
                      setUploadedDocument(selected ?? null);
                      processingDocumentIdRef.current =
                        selected && selected.block_count <= 0 ? selected.document_id : null;
                      setDocumentProcessing(Boolean(selected && selected.block_count <= 0));
                      setFlowError(
                        selected?.processing_status === "failed"
                          ? "该文档的 MinerU 解析失败，请检查后端日志后重新上传。"
                          : null,
                      );
                      if (selected && selected.block_count <= 0) {
                        void monitorDocumentProcessing(selected.document_id);
                      }
                    }}
                    className="h-9 w-full rounded-sm border border-border bg-background px-3 text-[12px] text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  >
                    <option value="">请选择已有 PDF</option>
                    {availableDocuments.map((document) => (
                      <option key={document.document_id} value={document.document_id}>
                        {document.filename} · {document.page_count} 页 ·{" "}
                        {document.block_count > 0
                          ? `${document.block_count} 个检索块`
                          : document.processing_status === "failed"
                            ? "解析失败"
                            : "MinerU 后台解析中"}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}

              <div className="flex flex-wrap items-center gap-3">
                <span className="text-[11px] text-muted-foreground">
                  {uploadedDocument
                    ? documentProcessing
                      ? "PDF 已上传；MinerU 正在后台解析并写入 PostgreSQL，可继续浏览页面"
                      : "PDF 已选择；点击启动后确认分析范围"
                    : "选择或拖拽招股书 PDF 后，即可启动 IPO 风险审核"}
                </span>
                <div className="ml-auto flex items-center gap-2">
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    disabled={uploading}
                    className="inline-flex h-9 items-center gap-2 rounded-sm border border-border bg-background px-3 text-[12px] font-medium text-muted-foreground hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {uploading ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <UploadCloud className="size-3.5" />
                    )}
                    {uploadedDocument ? "上传其他 PDF" : "上传 PDF"}
                  </button>
                  <button
                    onClick={() => {
                      setFlowError(null);
                      setBlockingQuestions([]);
                      setConfirmOpen(true);
                    }}
                    disabled={!canConfirm}
                    className="inline-flex h-9 items-center gap-2 rounded-sm bg-[color:var(--gold)] px-4 text-[13px] font-semibold text-[color:var(--navy-deep)] transition hover:bg-[#b8934b] disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <Send className="size-3.5" />
                    启动分析
                  </button>
                </div>
              </div>
            </div>
          </div>

          {flowError && (
            <div className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-[11.5px] text-destructive">
              {flowError}
            </div>
          )}

          <div className="mt-3 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
            <span>{agents.length || 12} 位 Agent 专家协同</span>
            <span className="text-[color:var(--gold)]/60">|</span>
            <span>招股书 PDF 证据抽取</span>
            <span className="text-[color:var(--gold)]/60">|</span>
            <span>证据先行 · 无证据不立论</span>
            <span className="text-[color:var(--gold)]/60">|</span>
            <span>风险规则快照冻结</span>
          </div>
        </motion.div>

        <div className="mt-14 flex flex-col items-center gap-3">
          <div className="eyebrow text-muted-foreground">Expert Guild · Agent 专家团</div>
          <div className="flex -space-x-1.5">
            {agents.slice(0, 10).map((a) => (
              <div
                key={a.id}
                title={`${a.name} · ${a.role}`}
                style={{ background: a.accent }}
                className="grid size-7 place-items-center rounded-sm text-[11px] font-medium text-white ring-1 ring-[color:var(--gold)]/40"
              >
                {a.avatar}
              </div>
            ))}
            <Link
              to="/experts"
              className="ml-3 inline-flex items-center rounded-sm border border-[color:var(--gold)]/50 bg-white px-3 text-[11px] font-medium text-[color:var(--navy)] hover:bg-[color:var(--gold)]/10"
            >
              查看全部 →
            </Link>
          </div>
        </div>

        <div className="mt-16">
          <div className="flex items-end justify-between">
            <div>
              <div className="eyebrow text-muted-foreground">Recent Analyses</div>
              <h2 className="mt-1 font-serif text-xl font-semibold text-[color:var(--navy)]">
                近期分析
              </h2>
            </div>
            <Link
              to="/analyses"
              className="text-[12px] font-medium text-[color:var(--navy)] hover:text-[color:var(--gold)]"
            >
              查看全部 →
            </Link>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {featured.map((a) => (
              <Link
                key={a.id}
                to="/analyses/$id/report"
                params={{ id: a.id }}
                className="group relative overflow-hidden rounded-md border border-border bg-white transition hover:border-[color:var(--navy)]/40"
              >
                <span
                  className="block h-[3px] w-full"
                  style={{
                    background:
                      a.riskLevel === "high"
                        ? "var(--risk-high)"
                        : a.riskLevel === "mid"
                          ? "var(--risk-mid)"
                          : "var(--risk-low)",
                  }}
                />
                <div className="p-4">
                  <div className="flex items-center justify-between text-[10.5px] uppercase tracking-wider text-muted-foreground">
                    <span className="tabular text-[color:var(--navy)]">{a.code}</span>
                    <span className="tabular">{a.createdAt}</span>
                  </div>
                  <div className="mt-2 text-[14px] font-semibold leading-snug text-[color:var(--navy)]">
                    {a.name} <span className="text-muted-foreground">· IPO 风险报告</span>
                  </div>
                  <div className="mt-1 text-[11.5px] text-muted-foreground">
                    {a.industry} · {a.experts} 位专家 ·{" "}
                    {a.mode === "expert" ? "专家级" : a.mode === "deep" ? "深度" : "快速"}
                  </div>
                  <div className="mt-4 flex items-center justify-between border-t border-border/80 pt-3 text-[11px]">
                    <div className="flex gap-4 tabular text-muted-foreground">
                      <span>
                        <span className="text-[color:var(--navy)]">{a.evidenceCount}</span> 证据
                      </span>
                      <span>
                        <span className="text-[color:var(--navy)]">{a.highConfidence}</span> 高置信
                      </span>
                    </div>
                    <span className="font-medium text-[color:var(--navy)] transition group-hover:text-[color:var(--gold)]">
                      Open →
                    </span>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        </div>
      </div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="font-serif text-2xl">
              开始前，请确认本次 IPO 风险审核
            </DialogTitle>
            <DialogDescription>
              确认后将冻结输入、规则快照和数据截止日期，并跳入实时 Agent 协作界面。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="rounded-md border border-border bg-muted/35 p-4">
              <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                你的需求
              </div>
              <p className="mt-2 text-sm leading-relaxed text-foreground">{requestText}</p>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <ConfirmItem icon={Building2} label="分析对象" value={companyName} />
              <ConfirmItem
                icon={FileText}
                label="PDF 材料"
                value={uploadedDocument ? uploadedDocument.filename : "未上传"}
              />
              <ConfirmItem
                icon={ShieldAlert}
                label="风险规则"
                value={
                  activeRuleSet
                    ? `${activeRuleSet.name} · ${activeRuleSet.version} · ${activeRuleSet.rule_count} 条`
                    : "默认 active 规则"
                }
              />
              <ConfirmItem
                icon={LineChart}
                label="分析深度"
                value={
                  deepDepth
                    ? `${deepDepth.label} · 上限 ${deepDepth.max_findings} 条 finding`
                    : "深度"
                }
              />
              <ConfirmItem icon={Sparkles} label="审核重点" value={focusDisplay} />
              <ConfirmItem
                icon={Users}
                label="Agent 队伍"
                value={`战略层按任务动态选用 · ${options?.agents.length ?? agents.length} 位可用专家`}
              />
            </div>

            {blockingQuestions.length > 0 ? (
              <div className="rounded-md border border-[color:var(--risk-mid)]/30 bg-[color:var(--risk-mid)]/8 p-3 text-xs text-[oklch(0.45_0.12_75)]">
                <div className="mb-1 flex items-center gap-1.5 font-medium">
                  <AlertCircle className="size-3.5" /> 后端仍需要补充信息
                </div>
                {blockingQuestions.map((question) => (
                  <div key={question}>- {question}</div>
                ))}
              </div>
            ) : null}
          </div>

          <DialogFooter>
            <button
              type="button"
              onClick={() => setConfirmOpen(false)}
              className="inline-flex h-9 items-center justify-center rounded-sm border border-border bg-background px-4 text-sm font-medium hover:bg-muted"
            >
              返回修改
            </button>
            <button
              type="button"
              onClick={() => void handleCreateDraft()}
              disabled={dispatching || !uploadedDocument}
              className="inline-flex h-9 items-center justify-center gap-2 rounded-sm bg-[color:var(--navy)] px-4 text-sm font-semibold text-white hover:bg-[color:var(--navy-deep)] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {dispatching ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <ArrowRight className="size-4" />
              )}
              开始分析
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );

  async function handlePdfUpload(file: File) {
    if (file.type && file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      setFlowError("请上传 PDF 文件。");
      return;
    }
    setUploading(true);
    setFlowError(null);
    setBlockingQuestions([]);
    try {
      const document = await uploadDocument(file);
      setUploadedDocument(document);
      setAvailableDocuments((current) => [
        document,
        ...current.filter((item) => item.document_id !== document.document_id),
      ]);
      if (document.block_count <= 0) {
        void monitorDocumentProcessing(document.document_id);
      } else {
        setDocumentProcessing(false);
      }
    } catch (err) {
      setFlowError(err instanceof Error ? err.message : "PDF 上传失败");
    } finally {
      setUploading(false);
    }
  }

  async function monitorDocumentProcessing(documentId: string) {
    processingDocumentIdRef.current = documentId;
    setDocumentProcessing(true);
    try {
      const ready = await waitForDocumentReady(documentId, (current) => {
        setUploadedDocument((selected) =>
          selected?.document_id === documentId ? current : selected,
        );
      });
      setUploadedDocument((selected) => (selected?.document_id === documentId ? ready : selected));
      setAvailableDocuments((current) => [
        ready,
        ...current.filter((item) => item.document_id !== ready.document_id),
      ]);
    } catch (err) {
      setFlowError(err instanceof Error ? err.message : "MinerU 解析失败");
    } finally {
      if (processingDocumentIdRef.current === documentId) {
        processingDocumentIdRef.current = null;
        setDocumentProcessing(false);
      }
    }
  }

  async function handleCreateDraft() {
    if (!uploadedDocument) return;
    setDispatching(true);
    setFlowError(null);
    setBlockingQuestions([]);
    try {
      const run = await createAnalysisDraft({
        company_name: companyName,
        document_text: "",
        document_id: uploadedDocument.document_id,
        ipo_project_hint: requestText.trim(),
        confirm_defaults: true,
        risk_rule_set_id: activeRuleSet?.rule_set_id,
        analysis_depth: "deep",
        structured_data: {
          focus_areas: focusDisplay,
          source_filename: uploadedDocument.filename,
        },
        max_findings: deepDepth?.max_findings ?? 40,
      });
      if (run.status === "clarifying") {
        const questions = (run as typeof run & { blocking_questions?: string[] })
          .blocking_questions ?? ["请补充公司名称、PDF 材料或默认配置确认。"];
        setBlockingQuestions(questions);
        return;
      }
      setConfirmOpen(false);
      await navigate({ to: "/analyses/$id", params: { id: run.run_id } });
    } catch (err) {
      setFlowError(err instanceof Error ? err.message : "创建分析任务失败");
    } finally {
      setDispatching(false);
    }
  }
}

function ConfirmItem({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Building2;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <div className="flex items-center gap-2 text-[11px] font-medium text-muted-foreground">
        <Icon className="size-3.5 text-[color:var(--gold)]" /> {label}
      </div>
      <div className="mt-1.5 line-clamp-2 text-sm font-medium text-[color:var(--navy)]">
        {value}
      </div>
    </div>
  );
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
