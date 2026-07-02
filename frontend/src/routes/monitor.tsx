import { createFileRoute } from "@tanstack/react-router";
import {
  Target,
  Database,
  Sparkles,
  ShieldCheck,
  Clock,
  Zap,
  Layers,
  Activity,
} from "lucide-react";
import { useState, useEffect } from "react";
import { getAnalyses, getDashboardStats } from "@/lib/data-service";
import { type AnalysisSummary } from "@/mocks";
import { RiskBadge } from "@/components/risk-badge";

export const Route = createFileRoute("/monitor")({
  head: () => ({
    meta: [
      { title: "风险监控中心 · Verda IPO RiskAgent" },
      {
        name: "description",
        content: "把每一次招股书分析沉淀为可复用的风险记忆 · 覆盖、证据、闭环价值与持续追踪",
      },
    ],
  }),
  component: MonitorPage,
});

function MonitorPage() {
  const [analyses, setAnalyses] = useState<AnalysisSummary[]>([]);
  const [stats, setStats] = useState<{
    totalAnalyses: number;
    evidenceCount: number;
    conclusions: number;
    crossValidationRate: number;
    hoursSaved: number;
    efficiency: number;
    avgSources: number;
    tokenK: number;
  } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getAnalyses(), getDashboardStats()]).then(([a, s]) => {
      if (!cancelled) {
        setAnalyses(a);
        setStats(s);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <div className="px-6 py-8 sm:px-10">
      <h1 className="font-serif text-3xl font-semibold">风险监控中心</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        把每一次招股书分析沉淀为可复用的风险记忆 —— 覆盖广度、证据厚度、链路可信度与持续追踪
      </p>

      <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {stats && (
          <>
            <KpiCard
              icon={Target}
              label="覆盖公司"
              value={stats.totalAnalyses}
              caption="已沉淀分析的港股 IPO 标的"
            />
            <KpiCard
              icon={Database}
              label="证据沉淀"
              value={stats.evidenceCount}
              caption={`累计联网取证 · 平均每篇报告 ${(stats.evidenceCount / stats.totalAnalyses).toFixed(1)} 条`}
            />
            <KpiCard
              icon={Sparkles}
              label="产出结论"
              value={stats.conclusions}
              caption="全部报告输出的风险结论总数"
            />
            <KpiCard
              icon={ShieldCheck}
              label="交叉验证率"
              value={`${stats.crossValidationRate}%`}
              caption="≥2 个独立来源相互印证的结论占比"
            />
          </>
        )}
      </div>

      <section className="mt-8 rounded-2xl border border-border bg-card p-6">
        <div className="flex items-center gap-2">
          <Activity className="size-4 text-primary" />
          <h2 className="font-serif text-lg font-semibold">业务闭环价值</h2>
          <span className="text-xs text-muted-foreground">
            相比传统人工招股书风控的真实可量化提升 · 公式透明可解释
          </span>
        </div>
        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {stats && (
            <>
              <ValueTile
                icon={Clock}
                value={`${stats.hoursSaved}`}
                unit="小时"
                label="累计节省人力"
              />
              <ValueTile icon={Zap} value={`${stats.efficiency}`} unit="×" label="平均效率提升" />
              <ValueTile
                icon={Layers}
                value={`${stats.avgSources}`}
                unit="×"
                label="平均信源覆盖"
              />
              <ValueTile
                icon={Sparkles}
                value={`${stats.tokenK}`}
                unit="K"
                label="累计 Token 算力"
              />
            </>
          )}
        </div>
      </section>

      <section className="mt-8 rounded-2xl border border-border bg-card p-6">
        <h2 className="font-serif text-lg font-semibold">每次分析概览</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          每份报告的关键数据 · 一键查看报告与决策链路日志
        </p>
        <div className="mt-4 overflow-hidden rounded-xl border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2 text-left font-medium">公司</th>
                <th className="px-4 py-2 text-left font-medium">行业</th>
                <th className="px-4 py-2 text-left font-medium">风险等级</th>
                <th className="px-4 py-2 text-right font-medium">证据</th>
                <th className="px-4 py-2 text-right font-medium">高置信</th>
                <th className="px-4 py-2 text-right font-medium">专家</th>
                <th className="px-4 py-2 text-right font-medium">创建时间</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {analyses.map((a) => (
                <tr key={a.id} className="hover:bg-muted/30">
                  <td className="px-4 py-2.5">
                    <div className="font-medium">{a.name}</div>
                    <div className="text-[11px] text-muted-foreground tabular">{a.code}</div>
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">{a.industry}</td>
                  <td className="px-4 py-2.5">
                    <RiskBadge level={a.riskLevel} />
                  </td>
                  <td className="px-4 py-2.5 text-right tabular">{a.evidenceCount}</td>
                  <td className="px-4 py-2.5 text-right tabular">{a.highConfidence}</td>
                  <td className="px-4 py-2.5 text-right tabular">{a.experts}</td>
                  <td className="px-4 py-2.5 text-right text-xs text-muted-foreground tabular">
                    {a.createdAt}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function KpiCard({
  icon: Icon,
  label,
  value,
  caption,
}: {
  icon: typeof Target;
  label: string;
  value: string | number;
  caption: string;
}) {
  return (
    <div className="rounded-2xl border border-border bg-card p-5">
      <div className="grid size-9 place-items-center rounded-xl bg-primary/8 text-primary">
        <Icon className="size-4" />
      </div>
      <div className="mt-4 font-serif text-4xl font-semibold tabular">{value}</div>
      <div className="mt-1 text-sm font-medium">{label}</div>
      <div className="mt-2 text-xs text-muted-foreground">{caption}</div>
    </div>
  );
}

function ValueTile({
  icon: Icon,
  value,
  unit,
  label,
}: {
  icon: typeof Target;
  value: string;
  unit: string;
  label: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-background p-4">
      <div className="grid size-8 place-items-center rounded-lg bg-primary/8 text-primary">
        <Icon className="size-3.5" />
      </div>
      <div className="mt-3 font-serif text-2xl font-semibold tabular">
        {value}
        <span className="ml-0.5 text-xs text-muted-foreground">{unit}</span>
      </div>
      <div className="mt-1 text-xs text-muted-foreground">{label}</div>
    </div>
  );
}
