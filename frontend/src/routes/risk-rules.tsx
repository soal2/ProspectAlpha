import { createFileRoute } from "@tanstack/react-router";
import { ChevronRight, Code2 } from "lucide-react";
import { useState, useEffect } from "react";
import { getRiskRulesData } from "@/lib/data-service";

export const Route = createFileRoute("/risk-rules")({
  head: () => ({
    meta: [
      { title: "风险体系配置 · Verda IPO RiskAgent" },
      { name: "description", content: "金融专家定义风险分类、阈值、权重 · 技术团队负责工程化抽取" },
    ],
  }),
  component: RiskRulesPage,
});

interface Rule {
  code: string;
  label: string;
  high: string;
  mid: string;
  weight: number;
  source: string;
}

function RiskRulesPage() {
  const [taxonomy, setTaxonomy] = useState<
    { key: string; label: string; color: string; rules: Rule[] }[]
  >([]);
  const [active, setActive] = useState("financial");

  useEffect(() => {
    let cancelled = false;
    getRiskRulesData().then((d) => {
      if (!cancelled) setTaxonomy(d.taxonomy);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const group = taxonomy.find((g) => g.key === active) ?? taxonomy[0];

  if (!group) {
    return (
      <div className="flex h-[calc(100vh-3.5rem)] items-center justify-center text-sm text-muted-foreground">
        加载中...
      </div>
    );
  }

  return (
    <div className="grid h-[calc(100vh-3.5rem)] grid-cols-[260px_1fr_380px]">
      <aside className="border-r border-border bg-card/40 p-5">
        <h2 className="font-serif text-lg font-semibold">风险体系</h2>
        <p className="mt-1 text-xs text-muted-foreground">4 大类</p>
        <div className="mt-4 space-y-1">
          {taxonomy.map((g) => (
            <button
              key={g.key}
              onClick={() => setActive(g.key)}
              className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm ${active === g.key ? "bg-primary/8 text-primary" : "text-muted-foreground hover:bg-accent"}`}
            >
              <span className="flex items-center gap-2">
                <span className="size-2 rounded-full" style={{ background: g.color }} />
                {g.label}
              </span>
              <span className="text-[11px] tabular">{g.rules.length}</span>
            </button>
          ))}
        </div>

        <div className="mt-8 rounded-lg border border-border bg-background p-3 text-xs">
          <div className="font-medium">权重分布</div>
          <div className="mt-3 space-y-2">
            {taxonomy.map((g) => {
              const sum = g.rules.reduce((s, r) => s + r.weight, 0);
              return (
                <div key={g.key}>
                  <div className="flex justify-between text-muted-foreground">
                    <span>{g.label}</span>
                    <span className="tabular">{sum}</span>
                  </div>
                  <div className="mt-1 h-1 rounded-full bg-muted">
                    <div
                      className="h-1 rounded-full"
                      style={{ width: `${sum}%`, background: g.color }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </aside>

      <section className="overflow-y-auto p-6">
        <div className="flex items-end justify-between">
          <div>
            <h1 className="font-serif text-2xl font-semibold">{group.label}</h1>
            <p className="mt-1 text-xs text-muted-foreground">
              共 {group.rules.length} 条规则 · 总权重{" "}
              {group.rules.reduce((s, r) => s + r.weight, 0)}
            </p>
          </div>
          <button className="rounded-full bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90">
            + 新增规则
          </button>
        </div>

        <div className="mt-5 space-y-3">
          {group.rules.length > 0 ? (
            group.rules.map((r) => (
              <div key={r.code} className="rounded-xl border border-border bg-card p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <div className="font-medium">
                      {r.label}{" "}
                      <span className="ml-2 text-[11px] tabular text-muted-foreground">
                        {r.code}
                      </span>
                    </div>
                    <div className="mt-0.5 text-[11px] text-muted-foreground">
                      数据来源：{r.source}
                    </div>
                  </div>
                  <div className="flex items-center gap-3 text-xs">
                    <span className="text-muted-foreground">权重</span>
                    <input
                      type="range"
                      min={0}
                      max={20}
                      defaultValue={r.weight}
                      className="accent-[color:var(--primary)]"
                    />
                    <span className="w-6 tabular text-right">{r.weight}</span>
                  </div>
                </div>
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  <div className="flex items-center gap-2 rounded-lg bg-[color:var(--risk-high)]/8 px-3 py-2 text-xs">
                    <span className="text-[color:var(--risk-high)]">高风险阈值</span>
                    <ChevronRight className="size-3 text-muted-foreground" />
                    <span className="font-medium tabular">{r.high}</span>
                  </div>
                  <div className="flex items-center gap-2 rounded-lg bg-[color:var(--risk-mid)]/12 px-3 py-2 text-xs">
                    <span className="text-[oklch(0.45_0.12_75)]">中风险阈值</span>
                    <ChevronRight className="size-3 text-muted-foreground" />
                    <span className="font-medium tabular">{r.mid}</span>
                  </div>
                </div>
              </div>
            ))
          ) : (
            <div className="py-12 text-center text-xs text-muted-foreground">暂无规则数据</div>
          )}
        </div>
      </section>

      {/* <aside className="overflow-y-auto border-l border-border bg-[oklch(0.18_0.018_160)] p-5 text-xs leading-relaxed text-[color:var(--paper)]">
        <h3 className="flex items-center gap-2 font-serif text-sm font-semibold text-[color:var(--sage-soft)]"><Code2 className="size-4" /> YAML 实时预览</h3>
        <pre className="mt-3 font-mono text-[11px] text-[oklch(0.92_0.025_140)]">{`risk_taxonomy:
  ${group.key}:`}
{group.rules.map((r) => `
    - code: ${r.code}
      label: "${r.label}"
      weight: ${r.weight}
      thresholds:
        high: "${r.high}"
        mid:  "${r.mid}"
      source: "${r.source}"`).join("")}
        </pre>
      </aside> */}
    </div>
  );
}
