import { createFileRoute } from "@tanstack/react-router";
import { Search, FileText, ExternalLink } from "lucide-react";
import { useState, useEffect } from "react";
import { getEvidenceDetailItems } from "@/lib/data-service";

export const Route = createFileRoute("/analyses/$id/evidence")({
  loader: async ({ params }) => {
    const items = await getEvidenceDetailItems(params.id);
    return { items };
  },
  component: AnalysisEvidence,
  head: () => ({ meta: [{ title: "证据库 · IPO 风险分析" }] }),
});

function AnalysisEvidence() {
  const { items } = Route.useLoaderData() as {
    items: {
      id: string;
      section: string;
      page: number;
      conf: number;
      snippet: string;
      risk: string;
    }[];
  };
  return (
    <div className="grid h-[calc(100vh-3.5rem-14.5rem)] min-h-[640px] grid-cols-[260px_1fr_380px]">
      <aside className="border-r border-border bg-card/40 p-5">
        <h3 className="font-serif text-base font-semibold">本分析证据</h3>
        <p className="mt-1 text-xs text-muted-foreground">312 条 · 7 个章节</p>
        <div className="relative mt-4">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            placeholder="关键词检索"
            className="h-9 w-full rounded-md border border-border bg-background pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>

        <div className="mt-6 space-y-3 text-sm">
          {[
            "风险因素 · 47",
            "财务资料 · 88",
            "业务 · 64",
            "股本 · 23",
            "董事及高管 · 19",
            "管理层讨论 · 41",
            "募集资金用途 · 30",
          ].map((g) => (
            <button
              key={g}
              className="flex w-full justify-between rounded-md px-2 py-1 text-left text-muted-foreground hover:bg-accent"
            >
              <span>{g.split(" · ")[0]}</span>
              <span className="text-[11px] tabular">{g.split(" · ")[1]}</span>
            </button>
          ))}
        </div>
      </aside>

      <section className="overflow-y-auto p-6">
        <div className="space-y-3">
          {items.map((e) => (
            <article key={e.id} className="rounded-xl border border-border bg-card p-4">
              <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                <span className="tabular font-medium text-foreground">[{e.id}]</span>
                <span>·</span>
                <span>
                  {e.section} P.{e.page}
                </span>
                <span className="ml-auto inline-flex items-center rounded-full bg-primary/8 px-2 py-0.5 font-medium text-primary tabular">
                  置信度 {e.conf.toFixed(2)}
                </span>
              </div>
              <p className="mt-3 text-sm leading-relaxed">{e.snippet}</p>
              <div className="mt-3 flex items-center justify-between border-t border-border pt-3">
                <span className="rounded-full bg-[color:var(--risk-mid)]/15 px-2 py-0.5 text-[10px] text-[oklch(0.45_0.12_75)]">
                  {e.risk}
                </span>
                <button className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
                  原文截图 <ExternalLink className="size-3" />
                </button>
              </div>
            </article>
          ))}
        </div>
      </section>

      <aside className="overflow-y-auto border-l border-border bg-card/40 p-5">
        <h3 className="flex items-center gap-2 font-serif text-base font-semibold">
          <FileText className="size-4 text-primary" /> 原文预览
        </h3>
        <div className="mt-4 aspect-[3/4] overflow-hidden rounded-lg border border-border bg-paper">
          <div className="grid h-full place-items-center bg-gradient-to-br from-[color:var(--sage-soft)]/30 to-[color:var(--amber-soft)]/30 text-center text-xs text-muted-foreground">
            <div>
              <FileText className="mx-auto size-8 text-primary/40" />
              <p className="mt-3 px-4 leading-relaxed">招股书 PDF 第 218 页 · bbox 高亮</p>
            </div>
          </div>
        </div>
      </aside>
    </div>
  );
}
