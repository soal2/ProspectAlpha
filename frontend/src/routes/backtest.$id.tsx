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
import { getBacktests, getBacktestDetailData } from "@/lib/data-service";
import { RiskBadge } from "@/components/risk-badge";

export const Route = createFileRoute("/backtest/$id")({
  loader: async ({ params }) => {
    const backtests = await getBacktests();
    const b = backtests.find((x) => x.id === params.id);
    if (!b) throw notFound();
    const detail = await getBacktestDetailData(params.id);
    return { backtest: b, detail };
  },
  head: ({ params }) => ({ meta: [{ title: `${params.id} · 回测报告 · Verda` }] }),
  component: BacktestDetail,
  notFoundComponent: () => (
    <div className="p-8 text-sm text-muted-foreground">没有找到这个回测</div>
  ),
});

function BacktestDetail() {
  const { backtest: b, detail } = Route.useLoaderData() as {
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
  };

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
          <button className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs hover:bg-accent">
            <Download className="size-3.5" /> 导出 PDF
          </button>
          <button className="inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground">
            <RefreshCw className="size-3.5" /> 一键复现
          </button>
        </div>
      </div>

      {/* KPI strip */}
      <div className="mt-6 grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Kpi label="样本量" value={b.samples.toString()} />
        <Kpi label="AUC" value={b.auc.toFixed(3)} accent />
        <Kpi label="KS" value={b.ks.toFixed(3)} />
        <Kpi label="Lift@10" value={`${b.lift10.toFixed(1)}×`} />
        <Kpi label="破发召回" value={`${(b.recall * 100).toFixed(0)}%`} />
        <Kpi label="节省工时" value={`${detail?.hoursSaved ?? 0} h`} />
      </div>

      {/* Group returns + ROC */}
      <div className="mt-8 grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <Card
          title="分组累计收益（vs HSI）"
          caption="高 / 中 / 低风险组在 T+1 / T+5 / T+20 / T+60 的累计收益（%）"
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
                  stroke="oklch(var(--risk-mid))"
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
          caption={`样本量 ${detail?.cm?.reduce((s, c) => s + c.n, 0) ?? 0} · 阈值 0.5`}
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
      <Card className="mt-4" title="风险因子贡献排行" caption="对模型 AUC 的边际贡献">
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

      {/* Misclassification */}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card
          title="漏报样本 · False Negative"
          icon={<AlertTriangle className="size-4 text-[color:var(--risk-high)]" />}
          caption="实际破发但被模型预判为低风险，需复盘特征覆盖"
        >
          <Errors
            rows={
              detail?.fnSamples as {
                code: string;
                name: string;
                actual: number;
                score: number;
                reason: string;
                risk: "high" | "mid" | "low";
              }[]
            }
          />
        </Card>
        <Card
          title="误报样本 · False Positive"
          icon={<CheckCircle2 className="size-4 text-[color:var(--risk-mid)]" />}
          caption="模型给出高风险但实际未破发，多源于护盘 / 基石锁定"
        >
          <Errors
            rows={
              detail?.fpSamples as {
                code: string;
                name: string;
                actual: number;
                score: number;
                reason: string;
                risk: "high" | "mid" | "low";
              }[]
            }
          />
        </Card>
      </div>

      {/* Reproduce */}
      <Card
        className="mt-4"
        title="参数配置 · 可复现"
        caption="所有参数已持久化，点击复现可在新任务里继承"
      >
        <pre className="overflow-auto rounded-md bg-muted/50 p-4 font-mono text-[11px]">{`backtest_id: ${b.id}
model: ${b.modelVersion}
window: 2024-01-01 ~ 2026-06-30
thresholds:
  high: 0.60
  mid:  0.35
features_hash: 7f3a-9c21-bd84-12ee
eval:
  windows: [T+1, T+5, T+20, T+60]
  positive: "破发 (close < offer)"
  metrics:  [AUC, KS, Lift@10, Recall@TopK]`}</pre>
      </Card>
    </div>
  );
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
    risk: "high" | "mid" | "low";
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
