import { createFileRoute } from "@tanstack/react-router";
import { FileText, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { PdfEvidenceViewer } from "@/components/pdf-evidence-viewer";
import { getEvidenceDetailItems } from "@/lib/data-service";
import type { EvidenceDetailItem } from "@/lib/types";

export const Route = createFileRoute("/analyses/$id/evidence")({
  loader: async ({ params }) => ({ items: await getEvidenceDetailItems(params.id) }),
  component: AnalysisEvidence,
  head: () => ({ meta: [{ title: "证据库 · IPO 风险分析" }] }),
});

function AnalysisEvidence() {
  const { items } = Route.useLoaderData() as { items: EvidenceDetailItem[] };
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState(items[0]?.id ?? "");
  const [activePage, setActivePage] = useState(items[0]?.page ?? 0);
  const filteredItems = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return items;
    return items.filter((item) =>
      [item.id, item.section, item.snippet, item.risk].some((value) =>
        value.toLowerCase().includes(keyword),
      ),
    );
  }, [items, query]);
  const selected = items.find((item) => item.id === selectedId) ?? items[0];
  const sections = new Map<string, number>();
  items.forEach((item) => sections.set(item.section, (sections.get(item.section) ?? 0) + 1));
  const activeBboxes = selected
    ? selected.bboxRegions
        .filter((region) => region.page === activePage)
        .map((region) => region.bbox)
    : [];

  const selectEvidence = (item: EvidenceDetailItem) => {
    setSelectedId(item.id);
    setActivePage(item.page);
  };

  return (
    <div className="grid h-[calc(100vh-3.5rem-14.5rem)] min-h-[640px] grid-cols-[260px_1fr_380px]">
      <aside className="border-r border-border bg-card/40 p-5">
        <h3 className="font-serif text-base font-semibold">本分析证据</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          {items.length} 条 · {sections.size} 个章节
        </p>
        <div className="relative mt-4">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="关键词检索"
            className="h-9 w-full rounded-md border border-border bg-background pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="mt-6 space-y-2 text-sm">
          {[...sections.entries()].map(([section, count]) => (
            <div key={section} className="flex justify-between px-2 py-1 text-muted-foreground">
              <span className="truncate">{section}</span>
              <span className="text-[11px] tabular">{count}</span>
            </div>
          ))}
        </div>
      </aside>

      <section className="overflow-y-auto p-6">
        <div className="space-y-3">
          {filteredItems.map((item) => (
            <button
              type="button"
              key={item.id}
              onClick={() => selectEvidence(item)}
              className={`w-full rounded-xl border bg-card p-4 text-left transition ${
                selected?.id === item.id
                  ? "border-primary ring-1 ring-primary/20"
                  : "border-border hover:border-primary/30"
              }`}
            >
              <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                <span className="tabular font-medium text-foreground">[{item.id}]</span>
                <span>·</span>
                <span>
                  {item.section} P.{item.page + 1}
                </span>
                <span className="ml-auto rounded-full bg-primary/8 px-2 py-0.5 font-medium text-primary tabular">
                  置信度 {item.conf.toFixed(2)}
                </span>
              </div>
              <p className="mt-3 text-sm leading-relaxed">{item.snippet}</p>
              <div className="mt-3 border-t border-border pt-3">
                <span className="rounded-full bg-[color:var(--risk-mid)]/15 px-2 py-0.5 text-[10px] text-[oklch(0.45_0.12_75)]">
                  {item.risk}
                </span>
              </div>
            </button>
          ))}
        </div>
      </section>

      <aside className="overflow-y-auto border-l border-border bg-card/40 p-5">
        <h3 className="flex items-center gap-2 font-serif text-base font-semibold">
          <FileText className="size-4 text-primary" /> 原文预览
        </h3>
        {selected ? (
          <>
            <div className="mt-1 text-[11px] text-muted-foreground">
              {selected.id} · {selected.section} · P.{activePage + 1}
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
            <div className="mt-4 overflow-hidden rounded-lg border border-border bg-paper">
              <PdfEvidenceViewer
                pdfUrl={selected.pdfUrl}
                pageIndex={activePage}
                bboxes={activeBboxes}
                coordinateSpace={selected.bboxCoordinateSpace}
              />
            </div>
          </>
        ) : (
          <div className="mt-4 text-xs text-muted-foreground">暂无证据</div>
        )}
      </aside>
    </div>
  );
}
