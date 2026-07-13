import { createFileRoute } from "@tanstack/react-router";
import { Search, FileText } from "lucide-react";
import { useState, useEffect } from "react";
import { PdfEvidenceViewer } from "@/components/pdf-evidence-viewer";
import { getKnowledgeData } from "@/lib/data-service";
import type { KnowledgeData } from "@/lib/types";

type SortBy = "time" | "confidence";

export const Route = createFileRoute("/evidence")({
  head: () => ({
    meta: [
      { title: "证据知识库 · ProspectAlpha IPO RiskAgent" },
      {
        name: "description",
        content: "全量招股书原文证据 · 跨公司、跨章节、跨风险类别的检索与复用",
      },
    ],
  }),
  component: EvidencePage,
});

function EvidencePage() {
  const [data, setData] = useState<KnowledgeData | null>(null);

  const [sortBy, setSortBy] = useState<SortBy>("confidence");
  const [selectedId, setSelectedId] = useState("");
  const [activePage, setActivePage] = useState(0);

  const facetCount = (label: string) => {
    if (!data) return 0;
    if (label === "公司") return data.stats.totalCompanies;
    if (label === "置信度") return 4;
    if (label === "招股书章节")
      return new Set(data.evidence.map((e) => e.section).filter(Boolean)).size;
    if (label === "风险类别") return new Set(data.evidence.map((e) => e.risk).filter(Boolean)).size;
    return 0;
  };

  useEffect(() => {
    let cancelled = false;
    getKnowledgeData().then((d) => {
      if (!cancelled) {
        setData(d);
        setSelectedId(d.evidence[0]?.id ?? "");
        setActivePage(d.evidence[0]?.page ?? 0);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);
  const selected = data?.evidence.find((item) => item.id === selectedId) ?? data?.evidence[0];
  const evidence = data?.evidence.slice() ?? [];
  if (sortBy === "confidence") evidence.sort((left, right) => right.conf - left.conf);
  const activeBboxes = selected
    ? selected.bboxRegions
        .filter((region) => region.page === activePage)
        .map((region) => region.bbox)
    : [];
  return (
    <div className="grid h-[calc(100vh-3.5rem)] grid-cols-[260px_1fr_360px]">
      {/* facets */}
      <aside className="border-r border-border bg-card/40 p-5">
        <h2 className="font-serif text-lg font-semibold">证据知识库</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          {data?.stats.totalEvidence} 条证据 · {data?.stats.totalCompanies} 家公司
        </p>
        <div className="relative mt-4">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            placeholder="关键词搜索"
            className="h-9 w-full rounded-lg border border-border bg-background pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="mt-6 space-y-5">
          {data?.facets.map((f) => (
            <div key={f.label}>
              <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                {f.label}
              </div>
              <ul className="mt-2 space-y-1 text-sm">
                {f.items.map((it, i) => (
                  <li key={it}>
                    <button
                      className={`flex w-full items-center justify-between rounded-md px-2 py-1 text-left ${i === 0 ? "bg-primary/8 text-primary" : "text-muted-foreground hover:bg-accent"}`}
                    >
                      <span>{it}</span>
                      <span className="text-[10px] tabular">{facetCount(f.label)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </aside>

      {/* evidence list */}
      <section className="overflow-y-auto p-6">
        <div className="flex items-end justify-between">
          <div>
            <h3 className="font-serif text-xl font-semibold">最近沉淀的证据</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {data?.evidence.length ?? 0} / {data?.stats.totalEvidence ?? 0} 条 · 按
              {sortBy === "time" ? "时间" : "置信度"}排序
            </p>
          </div>
          <div className="flex gap-2 text-xs">
            <button
              onClick={() => {
                setSortBy("time");
              }}
              className={`rounded-full px-3 py-1.5 ${
                sortBy === "time"
                  ? "bg-primary text-primary-foreground"
                  : "border border-border bg-card hover:bg-accent"
              }`}
            >
              按时间
            </button>
            <button
              onClick={() => {
                setSortBy("confidence");
              }}
              className={`rounded-full px-3 py-1.5 ${
                sortBy === "confidence"
                  ? "bg-primary text-primary-foreground"
                  : "border border-border bg-card hover:bg-accent"
              }`}
            >
              按置信度
            </button>
          </div>
        </div>
        <div className="mt-5 space-y-3">
          {evidence.map((e) => (
            <button
              type="button"
              key={e.id}
              onClick={() => {
                setSelectedId(e.id);
                setActivePage(e.page);
              }}
              className={`w-full rounded-xl border bg-card p-4 text-left transition ${selected?.id === e.id ? "border-primary ring-1 ring-primary/20" : "border-border hover:border-primary/30"}`}
            >
              <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                <span className="tabular font-medium text-foreground">{e.code}</span>
                <span>·</span>
                <span>{e.name}</span>
                <span>·</span>
                <span>
                  {e.section} P.{e.page + 1}
                </span>
                <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-primary/8 px-2 py-0.5 font-medium text-primary tabular">
                  置信度 {e.conf.toFixed(2)}
                </span>
              </div>
              <p className="mt-3 text-sm leading-relaxed text-foreground">{e.snippet}</p>
              <div className="mt-3 flex items-center justify-between border-t border-border pt-3">
                <div className="flex flex-wrap gap-1.5">
                  <span className="rounded-full bg-[color:var(--risk-mid)]/15 px-2 py-0.5 text-[10px] text-[oklch(0.45_0.12_75)]">
                    {e.risk}
                  </span>
                  <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground tabular">
                    [{e.id}]
                  </span>
                </div>
                <span className="text-xs text-primary">查看原文</span>
              </div>
            </button>
          ))}
        </div>
      </section>

      {/* preview */}
      <aside className="border-l border-border bg-card/40 p-5">
        <h3 className="flex items-center gap-2 font-serif text-base font-semibold">
          <FileText className="size-4 text-primary" /> 原文预览
        </h3>
        {selected ? (
          <>
            <div className="mt-1 text-[11px] text-muted-foreground">
              {selected.id} · {selected.code} · {selected.section} · P.{activePage + 1}
            </div>
            {selected.bboxRegions.length > 1 && (
              <div className="mt-3 flex flex-wrap gap-1">
                {[...new Set(selected.bboxRegions.map((region) => region.page))].map((page) => (
                  <button
                    type="button"
                    key={page}
                    onClick={() => setActivePage(page)}
                    className={`rounded px-2 py-1 text-[11px] ${activePage === page ? "bg-primary text-primary-foreground" : "bg-muted"}`}
                  >
                    P.{page + 1}
                  </button>
                ))}
              </div>
            )}
            <div className="mt-4 aspect-[3/4] overflow-hidden rounded-lg border border-border bg-paper">
              <PdfEvidenceViewer
                pdfUrl={selected.pdfUrl}
                pageIndex={activePage}
                bboxes={activeBboxes}
                coordinateSpace={selected.bboxCoordinateSpace}
              />
            </div>
            <div className="mt-4 rounded-lg border border-border bg-background p-3 text-xs">
              <div className="font-medium">证据链接到的结论</div>
              <ul className="mt-2 space-y-1 text-muted-foreground">
                <li>· {selected.risk}</li>
              </ul>
            </div>
          </>
        ) : (
          <div className="mt-4 aspect-[3/4] overflow-hidden rounded-lg border border-border bg-paper">
            <div className="grid h-full place-items-center text-center text-xs text-muted-foreground">
              <div>
                <FileText className="mx-auto size-8 text-primary/40" />
                <p className="mt-3">选择证据查看原文预览</p>
              </div>
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}
