import { createFileRoute, Link, Outlet, useRouterState, notFound } from "@tanstack/react-router";
import { ArrowLeft, ShieldAlert, FileText, Network, Database, PlayCircle } from "lucide-react";
import { analyses } from "@/mocks/data";
import { RiskBadge } from "@/components/risk-badge";

export const Route = createFileRoute("/analyses/$id")({
  loader: ({ params }) => {
    const a = analyses.find((x) => x.id === params.id);
    if (!a) throw notFound();
    return { analysis: a };
  },
  head: ({ params }) => ({
    meta: [{ title: `${params.id} · IPO 风险分析` }],
  }),
  component: AnalysisShell,
  notFoundComponent: () => <div className="p-8 text-sm text-muted-foreground">没有找到这份分析</div>,
});

const tabs: { to: "/analyses/$id" | "/analyses/$id/report" | "/analyses/$id/trace" | "/analyses/$id/evidence"; label: string; icon: typeof PlayCircle; exact?: boolean }[] = [
  { to: "/analyses/$id", label: "实时流", icon: PlayCircle, exact: true },
  { to: "/analyses/$id/report", label: "IPO 风险报告", icon: FileText },
  { to: "/analyses/$id/trace", label: "决策链路", icon: Network },
  { to: "/analyses/$id/evidence", label: "证据库", icon: Database },
];

function AnalysisShell() {
  const { analysis } = Route.useLoaderData();
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const isActive = (to: string, exact?: boolean) => {
    const full = to.replace("$id", analysis.id);
    return exact ? pathname === full : pathname.startsWith(full);
  };

  return (
    <div className="flex flex-col">
      <div className="border-b border-border bg-card/60 px-6 py-4 sm:px-8">
        <Link to="/analyses" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-3.5" /> 返回分析列表
        </Link>
        <div className="mt-3 grid grid-cols-[minmax(0,1fr)_auto] items-end gap-4 sm:flex sm:flex-wrap sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] tabular text-muted-foreground">{analysis.code}</span>
              <RiskBadge level={analysis.riskLevel} />
              <span className="text-[11px] text-muted-foreground">{analysis.experts} 位专家 · {analysis.mode === "expert" ? "专家级" : analysis.mode === "deep" ? "深度" : "快速"}模式 · 任务 {analysis.id}</span>
            </div>
            <h1 className="mt-2 font-serif text-2xl font-semibold leading-tight">
              {analysis.name} · IPO 风险报告
            </h1>
            <p className="mt-1 text-xs text-muted-foreground">{analysis.industry} · 上市 {analysis.listingDate} · 发行价 HK$ {analysis.offerPrice} · 发行规模 {analysis.offerSize}</p>
          </div>
          <div className="flex shrink-0 items-center gap-4">
            <Gauge score={analysis.riskScore} level={analysis.riskLevel} />
            <button className="inline-flex h-9 items-center gap-1.5 rounded-full bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90">
              <ShieldAlert className="size-4" /> 触发再分析
            </button>
          </div>
        </div>

        <nav className="-mb-px mt-5 flex gap-1 overflow-x-auto">
          {tabs.map((t) => (
            <Link key={t.label} to={t.to} params={{ id: analysis.id }} className={`flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition ${isActive(t.to, t.exact) ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
              <t.icon className="size-3.5" /> {t.label}
            </Link>
          ))}
        </nav>
      </div>

      <div className="min-w-0 flex-1">
        <Outlet />
      </div>
    </div>
  );
}

function Gauge({ score, level }: { score: number; level: "high" | "mid" | "low" }) {
  const color = level === "high" ? "var(--risk-high)" : level === "mid" ? "var(--risk-mid)" : "var(--risk-low)";
  const circumference = 2 * Math.PI * 26;
  const offset = circumference - (score / 100) * circumference;
  return (
    <div className="relative grid size-16 place-items-center">
      <svg className="-rotate-90" width={64} height={64}>
        <circle cx={32} cy={32} r={26} stroke="var(--border)" strokeWidth={6} fill="none" />
        <circle cx={32} cy={32} r={26} stroke={`oklch(${color})`} strokeWidth={6} fill="none" strokeDasharray={circumference} strokeDashoffset={offset} strokeLinecap="round" />
      </svg>
      <div className="absolute text-center">
        <div className="font-serif text-lg font-semibold leading-none tabular">{score}</div>
        <div className="text-[9px] text-muted-foreground">RISK</div>
      </div>
    </div>
  );
}
