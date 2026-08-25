import { createFileRoute, Link, notFound } from "@tanstack/react-router";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  BarChart,
  Bar,
  Cell,
  AreaChart,
  Area,
} from "recharts";
import { ArrowLeft, Download, RefreshCw, AlertTriangle, CheckCircle2 } from "lucide-react";
import { getBacktests, getBacktestDetailData, reproduceBacktest, startBacktest } from "@/lib/data-service";
import { API_BASE_URL } from "@/lib/env";
import { type BacktestDetailResponse, type RiskLevel } from "@/lib/types";
import { RiskBadge } from "@/components/risk-badge";
import { useState } from "react";

export const Route = createFileRoute("/backtest/$id")({
  loader: async ({ params }) => {
    const backtests = await getBacktests();
    const b = backtests.find((x) => x.id === params.id);
    if (!b) throw notFound();
    const response = await getBacktestDetailData(params.id);
    return { backtest: b, detail: response?.visuals ?? null, workflow: response };
  },
  head: ({ params }) => ({ meta: [{ title: `${params.id} · 回测报告 · ProspectAlpha` }] }),
  component: BacktestDetail,
  notFoundComponent: () => (
    <div className="p-8 text-sm text-muted-foreground">没有找到这个回测</div>
  ),
});

function BacktestDetail() {
  const navigate = Route.useNavigate();
  const { backtest: b, detail, workflow } = Route.useLoaderData() as {
    backtest: {
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
      status: string;
      author: string;
    };
    detail: {
      roc: { fpr: number; tpr: number }[];
      groupRet: { d: string; high: number; mid: number; low: number; hsi: number }[];
      lift: { p: string; lift: number; baseline: number }[];
      cm: { label: string; n: number; hint: string; tone: string }[];
      fnSamples: {
        code: string;
        name: string;
        actual: number;
        score: number;
        reason: string;
        risk: string;
      }[];
      fpSamples: {
        code: string;
        name: string;
        actual: number;
        score: number;
        reason: string;
        risk: string;
      }[];
      palette: string[];
      hoursSaved: number;
      factorContributions: { f: string; v: number }[];
      precision: number;
      recall: number;
      f1: number;
    } | null;
    workflow: BacktestDetailResponse | null;
  };

  const isWorkflowPending = b.status !== "完成" && b.status !== "失败";
  const isSingleCase = b.samples === 1;

  return (
    <div className="px-6 py-8 sm:px-10">
      <Link
        to="/backtest"
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" /> 返回回测中心
      </Link>

      <div className="mt-4 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-semibold">{b.name}</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            {b.model} · {b.modelVersion} · 创建于 {b.createdAt} · {b.author}
          </p>
        </div>
        <div className="flex gap-2">
          <a href={`${API_BASE_URL}/backtests/${b.id}/artifact`} target="_blank" rel="noreferrer" className={`inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs hover:bg-accent ${b.status !== "完成" ? "pointer-events-none opacity-50" : ""}`}>
            <Download className="size-3.5" /> 预览审计报告
          </a>
          <a href={`${API_BASE_URL}/backtests/${b.id}/artifact?download=1`} onClick={(event) => { event.preventDefault(); void downloadAuditHtml(b.id, b.name); }} className={`inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs hover:bg-accent ${b.status !== "完成" ? "pointer-events-none opacity-50" : ""}`}>
            <Download className="size-3.5" /> 下载 HTML
          </a>
          {b.status === "完成" && <button onClick={() => void reproduceBacktest(b.id).then((job) => navigate({ to: "/backtest/$id", params: { id: job.backtestId } }))} className="inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground"><RefreshCw className="size-3.5" /> 严格复现</button>}
        </div>
      </div>

      {isWorkflowPending && <WorkflowPanel backtestId={b.id} status={b.status} workflow={workflow} />}
      {!isWorkflowPending && (isSingleCase ? <CaseReplay sample={workflow?.samples?.[0]} /> : <>

      {/* KPI strip */}
      <div className="mt-6 grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Kpi label="样本量" value={b.samples.toString()} />
        <Kpi label="AUC" value={b.auc.toFixed(3)} accent />
        <Kpi label="KS" value={b.ks.toFixed(3)} />
        <Kpi label="Lift@Top20%" value={`${b.lift10.toFixed(1)}×`} />
        <Kpi label="T+5 下跌召回" value={`${(b.recall * 100).toFixed(0)}%`} />
        <Kpi label="节省工时" value={`${detail?.hoursSaved ?? 0} h`} />
      </div>

      {workflow?.weightComparison?.hidden_first && (
        <Card className="mt-4" title="隐性风险优先模型相对 D1 基线提升" caption="同一批冻结样本、同一行情标签；置信度不参与评分。">
          <div className="grid gap-3 sm:grid-cols-4">
            {(["auc", "ks", "lift_top20", "recall"] as const).map((metric) => (
              <div key={metric} className="rounded-xl bg-muted/40 p-3 text-xs">
                <div className="uppercase text-muted-foreground">{metric}</div>
                <div className="mt-2 tabular">D1 {Number(workflow.weightComparison?.d1_only?.[metric] ?? 0).toFixed(3)} → 完整 {Number(workflow.weightComparison?.hidden_first?.[metric] ?? 0).toFixed(3)}</div>
                <div className="mt-1 font-medium text-primary">提升 {Number(workflow.weightComparison?.uplift?.[metric] ?? 0).toFixed(3)}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Group returns + ROC */}
      <div className="mt-8 grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <Card
          title="风险分层 T+5 收益"
          caption="按高 / 中 / 低风险分层统计的 T+5 平均收益（%）；未纳入基准指数。"
        >
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={detail?.groupRet ?? []}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="d" stroke="var(--muted-foreground)" fontSize={11} />
                <YAxis stroke="var(--muted-foreground)" fontSize={11} unit="%" />
                <Tooltip
                  contentStyle={{
                    background: "var(--card)",
                    border: "1px solid var(--border)",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line
                  type="monotone"
                  dataKey="high"
                  name="高风险组"
                  stroke="oklch(var(--risk-high))"
                  strokeWidth={2}
                  dot
                />
                <Line
                  type="monotone"
                  dataKey="mid"
                  name="中风险组"
                  stroke="oklch(var(--risk-medium))"
                  strokeWidth={2}
                  dot
                />
                <Line
                  type="monotone"
                  dataKey="low"
                  name="低风险组"
                  stroke="oklch(var(--risk-low))"
                  strokeWidth={2}
                  dot
                />
                <Line
                  type="monotone"
                  dataKey="hsi"
                  name="恒生指数"
                  stroke="var(--muted-foreground)"
                  strokeDasharray="4 4"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card title="ROC 曲线" caption={`AUC = ${b.auc.toFixed(3)}`}>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={detail?.roc ?? []}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis dataKey="fpr" stroke="var(--muted-foreground)" fontSize={11} />
                <YAxis stroke="var(--muted-foreground)" fontSize={11} />
                <Tooltip
                  contentStyle={{
                    background: "var(--card)",
                    border: "1px solid var(--border)",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="tpr"
                  stroke={detail?.palette?.[0] ?? "var(--chart-1)"}
                  fill={detail?.palette?.[0] ?? "var(--chart-1)"}
                  fillOpacity={0.25}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      {/* Confusion + Lift */}
      <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_1.4fr]">
        <Card
          title="混淆矩阵"
          caption={`Top 20% 人工复核容量 · 样本量 ${detail?.cm?.reduce((s, c) => s + c.n, 0) ?? 0}`}
        >
          <div className="grid grid-cols-2 gap-2 text-xs">
            {(detail?.cm ?? []).map((c) => (
              <div
                key={c.label}
                className={`rounded-xl p-4 ${c.tone === "ok" ? "bg-primary/10" : "bg-[color:var(--risk-mid)]/15"}`}
              >
                <div className="text-[11px] text-muted-foreground">{c.label}</div>
                <div className="mt-1 font-serif text-3xl font-semibold tabular">{c.n}</div>
                <div className="mt-1 text-[11px]">{c.hint}</div>
              </div>
            ))}
          </div>
          <div className="mt-4 grid grid-cols-3 gap-2 text-center text-xs">
            <Mini label="精确率" value={`${((detail?.precision ?? 0) * 100).toFixed(1)}%`} />
            <Mini label="召回率" value={`${((detail?.recall ?? 0) * 100).toFixed(1)}%`} />
            <Mini label="F1" value={`${(detail?.f1 ?? 0).toFixed(3)}`} />
          </div>
        </Card>

        <Card title="Lift / Gain" caption="按预测分数分位 · baseline = 1.0">
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={detail?.lift ?? []}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="p" stroke="var(--muted-foreground)" fontSize={11} />
                <YAxis stroke="var(--muted-foreground)" fontSize={11} />
                <Tooltip
                  contentStyle={{
                    background: "var(--card)",
                    border: "1px solid var(--border)",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Bar dataKey="lift" radius={[4, 4, 0, 0]}>
                  {(detail?.lift ?? []).map((_, i) => (
                    <Cell
                      key={i}
                      fill={detail?.palette?.[0] ?? "var(--chart-1)"}
                      fillOpacity={1 - i * 0.07}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      {/* Factor contribution */}
      <Card className="mt-4" title="风险分层事件率" caption="各风险等级的 T+5 显著下跌事件率">
        {(detail?.factorContributions?.length ?? 0) > 0 ? (
          <div className="space-y-2">
            {detail!.factorContributions.map((s) => (
              <div key={s.f} className="flex items-center gap-3 text-xs">
                <span className="w-44 shrink-0 text-muted-foreground">{s.f}</span>
                <div className="h-3 flex-1 overflow-hidden rounded-full bg-muted">
                  <div className="h-full bg-primary" style={{ width: `${s.v * 100 * 8}%` }} />
                </div>
                <span className="w-14 text-right tabular">+{s.v.toFixed(3)}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="py-8 text-center text-xs text-muted-foreground">暂无风险因子数据</div>
        )}
      </Card>

      <Card className="mt-4" title="行业队列" caption="按分析前已确认并冻结的 IndustryProfile 分组；少于 10 家不发布数值结论。">
        <div className="divide-y divide-border text-xs">
          {(workflow?.industryCohorts ?? []).map((cohort) => (
            <div key={cohort.code} className="flex flex-wrap items-center gap-3 py-3">
              <span className="font-medium">{cohort.name}（{cohort.code}）</span>
              <span className="text-muted-foreground">{cohort.samples} 家</span>
              <span className="ml-auto text-muted-foreground">{cohort.status === "published" ? `AUC ${Number(cohort.auc ?? 0).toFixed(3)} · KS ${Number(cohort.ks ?? 0).toFixed(3)} · Lift ${Number(cohort.lift_top20 ?? 0).toFixed(3)}` : "样本不足"}</span>
            </div>
          ))}
          {!workflow?.industryCohorts?.length && <p className="py-5 text-center text-muted-foreground">暂无行业队列</p>}
        </div>
      </Card>

      {/* Misclassification */}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card
          title="漏报样本 · False Negative"
          icon={<AlertTriangle className="size-4 text-[color:var(--risk-high)]" />}
          caption="实际发生 T+5 显著下跌但未进入 Top 20%，需复盘特征覆盖"
        >
          <Errors
            rows={
              detail?.fnSamples as {
                code: string;
                name: string;
                actual: number;
                score: number;
                reason: string;
                risk: RiskLevel;
              }[]
            }
          />
        </Card>
        <Card
          title="误报样本 · False Positive"
          icon={<CheckCircle2 className="size-4 text-[color:var(--risk-medium)]" />}
          caption="进入 Top 20% 人工复核但未发生 T+5 显著下跌"
        >
          <Errors
            rows={
              detail?.fpSamples as {
                code: string;
                name: string;
                actual: number;
                score: number;
                reason: string;
                risk: RiskLevel;
              }[]
            }
          />
        </Card>
      </div>

      {/* Reproduce */}
      <Card
        className="mt-4"
        title="参数配置 · 可复现"
        caption="评分卡版本和评估口径"
      >
        <pre className="overflow-auto rounded-md bg-muted/50 p-4 font-mono text-[11px]">{`backtest_id: ${b.id}
model: ${b.modelVersion}
eval:
  horizon: T+5（实际交易日）
  positive: "窗口内最低价较发行价跌幅 <= -10%"
  alert_capacity: "风险分 Top 20%"
  score_inputs: "已验证招股书 finding；市场数据缺失时明确标记"
  metrics: [AUC, KS, Lift@Top20%, Recall@Top20%]`}</pre>
      </Card>
      </>)}
    </div>
  );
}

async function downloadAuditHtml(backtestId: string, name: string) {
  const response = await fetch(`${API_BASE_URL}/backtests/${backtestId}/artifact?download=1`);
  if (!response.ok) throw new Error("审计报告下载失败");
  const blob = new Blob([await response.text()], { type: "text/html;charset=utf-8" });
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = `${name}-审计报告-${backtestId}.html`;
  link.click();
  URL.revokeObjectURL(objectUrl);
}

function WorkflowPanel({ backtestId, status, workflow }: { backtestId: string; status: string; workflow: { candidates?: { code: string; scoreId: string | null; status: string; reason: string | null }[]; jobs?: { status: string; progress: number; stage: string; error: string | null }[] } | null }) {
  const [saving, setSaving] = useState(false);
  const navigate = Route.useNavigate();
  const refresh = () => navigate({ to: "/backtest/$id", params: { id: backtestId }, replace: true });
  const job = workflow?.jobs?.[0];
  const start = async () => { setSaving(true); try { await startBacktest(backtestId); await refresh(); } finally { setSaving(false); } };
  return <Card className="mt-6" title="回测工作流" caption="候选评分必须在上市日前的信息截止日内冻结；结果会写入不可变快照。">
    <div className="text-sm">当前状态：<span className="font-medium">{status}</span>{job && <span className="ml-3 text-muted-foreground">{job.stage} · {job.progress}%</span>}</div>
    {job?.error && <p className="mt-2 text-xs text-destructive">{job.error}</p>}
    {status === "待运行" && <button disabled={saving} onClick={() => void start()} className="mt-4 rounded-full bg-primary px-4 py-2 text-xs font-medium text-primary-foreground disabled:opacity-50">启动回测</button>}
    {(status === "构建候选中" || status === "运行中") && <button onClick={() => void refresh()} className="mt-4 rounded-full border border-border px-4 py-2 text-xs">刷新状态</button>}
  </Card>;
}

function CaseReplay({ sample }: { sample: NonNullable<BacktestDetailResponse["samples"]>[number] | undefined }) {
  if (!sample) return <Card className="mt-6" title="个案回放">暂无可展示的样本快照。</Card>;
  const outcome = sample.outcomeProfile ?? {};
  const attribution = sample.retrospectiveAttribution ?? {};
  const retrospectiveSources = Array.isArray(attribution.sources) ? attribution.sources as Record<string, unknown>[] : [];
  return <><Card className="mt-6" title="IPO 风险个案回放" caption="上市后信息只用于解释，不修改上市前冻结分数，也不证明风险指控为真。"><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Kpi label="冻结风险分" value={String(sample.score)} accent /><Kpi label="T+5 收益" value={`${sample.returnPct.toFixed(2)}%`} /><Kpi label="最大回撤" value={`${sample.maxDrawdownPct.toFixed(2)}%`} /><Kpi label="预警命中" value={sample.actualDecline ? "是" : "否"} /></div><div className="mt-5 rounded-xl border border-border p-4 text-sm"><div className="font-medium">{sample.name} · {sample.code} <RiskBadge level={sample.risk} className="ml-2" /></div><p className="mt-3 text-xs text-muted-foreground">实际 T+5 显著下跌：{sample.actualDecline ? "是" : "否"} · 窗口内破发：{sample.breaking ? "是" : "否"}</p><p className="mt-2 text-xs text-muted-foreground">评分信息截止日：{sample.analysisAsOfDate ?? "未记录"} · 来源分析：{sample.analysisRunId ?? "未记录"}</p><p className="mt-3 text-xs text-muted-foreground">风险因子：{sample.reason}</p></div></Card><Card className="mt-4" title="公司特有隐性风险与行情标签"><div className="space-y-3 text-xs">{(sample.specificRisks ?? []).map((risk, index) => <div key={String(risk.finding_id ?? index)} className="rounded-lg border border-border p-3"><div className="font-medium">{String(risk.title ?? risk.risk_type ?? "隐性风险")}</div><p className="mt-1 text-muted-foreground">{String(risk.reasoning_summary ?? risk.causal_chain ?? "")}</p><p className="mt-2 text-muted-foreground">招股书证据：{Array.isArray(risk.evidence_ids) ? risk.evidence_ids.join("、") : "未记录"} · 预期影响：{Array.isArray(risk.expected_market_effects) ? risk.expected_market_effects.join("、") : "未记录"}</p></div>)}{!sample.specificRisks?.length && <p className="text-muted-foreground">未冻结公司特有风险。</p>}<div className="rounded-lg bg-muted/40 p-3">T+5：{formatOutcome(outcome["T+5"])} · T+20：{formatOutcome(outcome["T+20"])} · T+60：{formatOutcome(outcome["T+60"])}</div><div className="rounded-lg bg-muted/40 p-3">事后归因：{String(attribution.status ?? "insufficient")} · 查询 {String(attribution.searches_used ?? 0)}/6 次。{String(attribution.note ?? "")}{retrospectiveSources.map((source, index) => <a key={String(source.source_id ?? index)} href={String(source.url ?? "#")} target="_blank" rel="noreferrer" className="ml-2 text-primary underline underline-offset-2">{String(source.title ?? source.risk_title ?? "事后来源")}</a>)}</div></div></Card><Card className="mt-4" title="回放口径"><pre className="overflow-auto rounded-md bg-muted/50 p-4 font-mono text-[11px]">{`prediction: 仅使用上市前冻结信息\nlabels: T+5 主指标；T+20/T+60 辅助\nretrospective: 仅作归因，不回写评分\nmode: single_company_case_replay`}</pre></Card></>;
}

function formatOutcome(value: unknown) {
  if (!value || typeof value !== "object") return "无标签";
  const row = value as Record<string, unknown>;
  return `${Number(row.return_pct ?? 0).toFixed(2)}% / 最大回撤 ${Number(row.max_drawdown_pct ?? 0).toFixed(2)}%`;
}

function Card({
  title,
  caption,
  icon,
  className = "",
  children,
}: {
  title: string;
  caption?: string;
  icon?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={`rounded-2xl border border-border bg-card p-5 ${className}`}>
      <div className="mb-4 flex items-center gap-2">
        {icon}
        <div>
          <h3 className="font-serif text-base font-semibold">{title}</h3>
          {caption && <p className="mt-0.5 text-[11px] text-muted-foreground">{caption}</p>}
        </div>
      </div>
      {children}
    </section>
  );
}

function Kpi({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div
      className={`rounded-xl border p-4 ${accent ? "border-primary/30 bg-primary/5" : "border-border bg-card"}`}
    >
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="mt-1 font-serif text-2xl font-semibold tabular">{value}</div>
    </div>
  );
}
function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-muted/40 p-3">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className="mt-1 font-serif text-lg font-semibold tabular">{value}</div>
    </div>
  );
}
function Errors({
  rows,
}: {
  rows: {
    code: string;
    name: string;
    actual: number;
    score: number;
    reason: string;
    risk: RiskLevel;
  }[];
}) {
  return (
    <div className="divide-y divide-border">
      {rows.map((r) => (
        <div key={r.code} className="py-3">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="tabular font-medium">{r.code}</span>
            <span className="text-muted-foreground">{r.name}</span>
            <RiskBadge level={r.risk} className="ml-1" />
            <span className="ml-auto tabular text-muted-foreground">
              预测分 {r.score} · 实际 {r.actual}%
            </span>
          </div>
          <p className="mt-1.5 text-xs text-muted-foreground">{r.reason}</p>
        </div>
      ))}
    </div>
  );
}
