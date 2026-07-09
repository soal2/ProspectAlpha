import { createFileRoute, Link, Outlet, useRouterState } from "@tanstack/react-router";
import { Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { getAgents } from "@/lib/data-service";
import { type Agent } from "@/lib/types";

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

const layerMeta = [
  { key: "all", label: "全部", suffix: "专家" },
  { key: "L3", label: "决策层 L3", suffix: "统筹终审" },
  { key: "L2", label: "策略层 L2", suffix: "方法顾问" },
  { key: "L1", label: "执行层 L1", suffix: "行业职能" },
];

function ExpertsPage() {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  if (pathname !== "/experts") return <Outlet />;
  return <ExpertsIndexPage />;
}

function ExpertsIndexPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedLayer, setSelectedLayer] = useState("all");
  const [query, setQuery] = useState("");

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

  const layerCounts = useMemo(
    () => ({
      all: agents.length,
      L3: agents.filter((agent) => agent.layer === "L3").length,
      L2: agents.filter((agent) => agent.layer === "L2").length,
      L1: agents.filter((agent) => agent.layer === "L1").length,
    }),
    [agents],
  );

  const filteredAgents = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return agents.filter((agent) => {
      const matchesLayer = selectedLayer === "all" || agent.layer === selectedLayer;
      if (!matchesLayer) return false;
      if (!normalizedQuery) return true;

      return [agent.name, agent.role, agent.layer, agent.desc, ...agent.expertise]
        .join(" ")
        .toLowerCase()
        .includes(normalizedQuery);
    });
  }, [agents, query, selectedLayer]);

  const hasFilter = selectedLayer !== "all" || query.trim().length > 0;

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
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索专家、技能或知识标签"
            className="h-9 w-72 rounded-full border border-border bg-card pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        {layerMeta.map((layer) => {
          const active = selectedLayer === layer.key;
          const count = layerCounts[layer.key as keyof typeof layerCounts];
          return (
            <button
              key={layer.key}
              type="button"
              aria-pressed={active}
              onClick={() => setSelectedLayer(layer.key)}
              className={`flex flex-col items-center rounded-2xl px-4 py-2 text-xs font-medium transition ${active ? "bg-primary text-primary-foreground" : "border border-border bg-card hover:bg-accent"}`}
            >
              <span>{layer.label}</span>
              <span
                className={`mt-0.5 text-[10px] ${active ? "text-primary-foreground/80" : "text-muted-foreground"}`}
              >
                {count} 位{layer.key === "all" ? "" : " · "}
                {layer.suffix}
              </span>
            </button>
          );
        })}
      </div>

      <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {filteredAgents.map((a) => (
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

      {!loading && filteredAgents.length === 0 && (
        <div className="mt-8 rounded-2xl border border-dashed border-border bg-card px-6 py-10 text-center text-sm text-muted-foreground">
          {hasFilter ? "没有找到匹配的专家，请调整筛选或搜索关键词。" : "暂无专家数据。"}
        </div>
      )}
    </div>
  );
}
