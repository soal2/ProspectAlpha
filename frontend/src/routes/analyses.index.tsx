import { createFileRoute, Link } from "@tanstack/react-router";
import { Plus, Search, ListFilter, Network } from "lucide-react";
import { useState, useEffect } from "react";
import { getAnalyses } from "@/lib/data-service";
import { type AnalysisSummary } from "@/mocks";
import { RiskBadge } from "@/components/risk-badge";

export const Route = createFileRoute("/analyses/")({
  head: () => ({
    meta: [
      { title: "我的分析 · ProspectAlpha IPO RiskAgent" },
      {
        name: "description",
        content: "全部已完成的港股 IPO 风险分析报告，结论可溯源，证据可沉淀。",
      },
    ],
  }),
  component: AnalysesIndex,
});

const filters = ["全部", "高风险", "中风险", "低风险", "专家级", "近 7 天"];

function AnalysesIndex() {
  const [analyses, setAnalyses] = useState<AnalysisSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getAnalyses().then((data) => {
      if (!cancelled) {
        setAnalyses(data);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <div className="px-6 py-8 sm:px-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-semibold">我的分析</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            所有已完成的港股 IPO 风险分析报告 · 结论可溯源、证据可沉淀
          </p>
        </div>
        <Link
          to="/intake"
          className="inline-flex items-center gap-2 rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:bg-primary/90"
        >
          <Plus className="size-4" /> 发起新分析
        </Link>
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            placeholder="搜索公司、股票代码或行业"
            className="h-9 w-72 rounded-full border border-border bg-card pl-9 pr-3 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        {filters.map((f, i) => (
          <button
            key={f}
            className={`inline-flex h-8 items-center rounded-full px-3 text-xs font-medium ${i === 0 ? "bg-primary text-primary-foreground" : "border border-border bg-card text-muted-foreground hover:bg-accent"}`}
          >
            {f}
          </button>
        ))}
        <button className="ml-auto inline-flex h-8 items-center gap-1.5 rounded-full border border-border bg-card px-3 text-xs text-muted-foreground hover:bg-accent">
          <ListFilter className="size-3.5" /> 高级筛选
        </button>
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {analyses.map((a) => (
          <article
            key={a.id}
            className="group flex flex-col overflow-hidden rounded-2xl border border-border bg-card transition hover:border-primary/30 hover:shadow-sm"
          >
            <div className="relative h-32 bg-gradient-to-br from-[color:var(--sage-soft)] via-[color:var(--amber-soft)]/40 to-[color:var(--paper)]">
              <div className="absolute inset-0 grid-paper opacity-60" />
              <div className="absolute left-4 top-4">
                <RiskBadge level={a.riskLevel} />
              </div>
              <div className="absolute right-4 top-4 rounded-md bg-background/80 px-2 py-1 text-[11px] tabular text-muted-foreground backdrop-blur">
                {a.code}
              </div>
              <div className="absolute bottom-3 right-4 font-serif text-3xl font-semibold tabular text-foreground/80">
                {a.riskScore}
                <span className="text-xs text-muted-foreground">/100</span>
              </div>
            </div>
            <div className="flex flex-1 flex-col p-4">
              <h3 className="font-medium leading-snug">{a.name} · IPO 风险报告</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                {a.industry} · 上市 {a.listingDate}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                基于 {a.evidenceCount} 条证据 · {a.experts} 位专家协作生成 ·{" "}
                {a.mode === "expert" ? "专家级模式" : a.mode === "deep" ? "深度模式" : "快速模式"}
              </p>
              <div className="mt-4 flex items-center justify-between border-t border-border pt-3 text-xs text-muted-foreground">
                <div className="flex items-center gap-3 tabular">
                  <span>{a.evidenceCount} 证据</span>
                  <span>·</span>
                  <span>{a.highConfidence} 高置信</span>
                </div>
                <div className="flex items-center gap-1">
                  <Link
                    to="/analyses/$id/trace"
                    params={{ id: a.id }}
                    className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-primary hover:bg-primary/8"
                  >
                    <Network className="size-3" /> 链路
                  </Link>
                  <Link
                    to="/analyses/$id/report"
                    params={{ id: a.id }}
                    className="inline-flex items-center rounded-full bg-primary/8 px-2.5 py-1 text-primary hover:bg-primary/12"
                  >
                    报告 →
                  </Link>
                </div>
              </div>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
