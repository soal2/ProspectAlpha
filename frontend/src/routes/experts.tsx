import { createFileRoute, Link } from "@tanstack/react-router";
import { Search } from "lucide-react";
import { useState, useEffect } from "react";
import { getAgents } from "@/lib/data-service";
import { type Agent } from "@/mocks";

export const Route = createFileRoute("/experts")({
  head: () => ({
    meta: [
      { title: "Agent 专家公会 · ProspectAlpha IPO RiskAgent" },
      {
        name: "description",
        content: "12 位 IPO 风险分析专家 · 三层协作架构 · 决策 / 策略 / 执行",
      },
    ],
  }),
  component: ExpertsPage,
});

const layers = [
  { key: "all", label: "全部", sub: "12 位专家" },
  { key: "L3", label: "决策层 L3", sub: "3 位 · 统筹终审" },
  { key: "L2", label: "策略层 L2", sub: "3 位 · 方法顾问" },
  { key: "L1", label: "执行层 L1", sub: "6 位 · 行业职能" },
];

function ExpertsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getAgents().then((data) => {
      if (!cancelled) {
        setAgents(data);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <div className="px-6 py-8 sm:px-10">
      <h1 className="font-serif text-3xl font-semibold">Agent 专家公会</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        12 位 IPO 风险分析专家 · 三层协作架构 · 决策 / 策略 / 执行
      </p>

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            placeholder="搜索专家、技能或知识标签"
            className="h-9 w-72 rounded-full border border-border bg-card pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        {layers.map((l, i) => (
          <button
            key={l.key}
            className={`flex flex-col items-center rounded-2xl px-4 py-2 text-xs font-medium ${i === 0 ? "bg-primary text-primary-foreground" : "border border-border bg-card hover:bg-accent"}`}
          >
            <span>{l.label}</span>
            <span
              className={`mt-0.5 text-[10px] ${i === 0 ? "text-primary-foreground/80" : "text-muted-foreground"}`}
            >
              {l.sub}
            </span>
          </button>
        ))}
      </div>

      <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {agents.map((a) => (
          <Link
            key={a.id}
            to="/experts/$id"
            params={{ id: a.id }}
            className="group relative rounded-2xl border border-border bg-card p-5 transition hover:border-primary/30 hover:shadow-sm"
          >
            <span className="absolute right-4 top-4 rounded-md bg-primary/8 px-1.5 py-0.5 text-[10px] font-semibold tabular text-primary">
              {a.layer}
            </span>
            <div
              style={{ background: a.accent }}
              className="grid size-14 place-items-center rounded-2xl text-lg font-medium text-white"
            >
              {a.avatar}
            </div>
            <div className="mt-4">
              <div className="text-base font-medium">{a.name}</div>
              <div className="mt-0.5 text-xs text-muted-foreground">{a.role}</div>
            </div>
            <p className="mt-3 text-xs leading-relaxed text-muted-foreground line-clamp-2">
              {a.desc}
            </p>
            <div className="mt-4 flex items-center justify-between border-t border-border pt-3 text-[11px] text-muted-foreground">
              <span className="tabular">{a.callCount} 次调用</span>
              <span className="tabular">均 {a.avgTokens} tok</span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
