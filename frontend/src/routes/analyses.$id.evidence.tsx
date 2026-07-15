import { createFileRoute } from "@tanstack/react-router";
import { Search, FileText } from "lucide-react";
import { useMemo, useState } from "react";
import { getEvidenceDetailItems } from "@/lib/data-service";
import { PdfViewer } from "@/components/pdf-viewer";
import type { EvidenceDetailItem } from "@/lib/types";

export const Route = createFileRoute("/analyses/$id/evidence")({
  loader: async ({ params }) => {
    const items = await getEvidenceDetailItems(params.id);
    return { items };
  },
  component: AnalysisEvidence,
  head: () => ({ meta: [{ title: "证据库 · IPO 风险分析" }] }),
});

function AnalysisEvidence() {
  const { items } = Route.useLoaderData() as { items: EvidenceDetailItem[] };
  const [keyword, setKeyword] = useState("");
  const [section, setSection] = useState("全部");
  const [selected, setSelected] = useState<EvidenceDetailItem | null>(items[0] ?? null);

  const sectionCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of items) {
      const key = item.section || "未标注章节";
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return counts;
  }, [items]);

  const filtered = useMemo(() => {
    return items.filter((item) => {
      if (section !== "全部" && (item.section || "未标注章节") !== section) return false;
      if (keyword.trim()) {
        const query = keyword.trim().toLowerCase();
        const haystack = `${item.snippet} ${item.section} ${item.risk}`.toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      return true;
    });
  }, [items, section, keyword]);

  return (
    <div className="grid h-[calc(100vh-3.5rem-14.5rem)] min-h-[640px] grid-cols-[240px_minmax(360px,1fr)_minmax(460px,40vw)]">
      <aside className="overflow-y-auto border-r border-border bg-card/40 p-5">
        <h3 className="font-serif text-base font-semibold">本分析证据</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          {items.length} 条 · {sectionCounts.size} 个章节
        </p>
        <div className="relative mt-4">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="关键词检索"
            className="h-9 w-full rounded-md border border-border bg-background pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>

        <div className="mt-6 space-y-1 text-sm">
          {["全部", ...sectionCounts.keys()].map((name) => (
            <button
              key={name}
              onClick={() => setSection(name)}
              className={`flex w-full justify-between rounded-md px-2 py-1 text-left ${
                section === name
                  ? "bg-primary/8 text-primary"
                  : "text-muted-foreground hover:bg-accent"
              }`}
            >
              <span className="truncate">{name}</span>
              <span className="text-[11px] tabular">
                {name === "全部" ? items.length : (sectionCounts.get(name) ?? 0)}
              </span>
            </button>
          ))}
        </div>
      </aside>

      <section className="overflow-y-auto p-6">
        <div className="space-y-3">
          {filtered.map((e) => (
            <article
              key={e.id}
              onClick={() => setSelected(e)}
              className={`cursor-pointer rounded-xl border bg-card p-4 transition hover:border-primary/30 ${
                selected?.id === e.id ? "border-primary/50" : "border-border"
              }`}
            >
              <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                <span className="tabular font-medium text-foreground">[{e.id}]</span>
                <span>·</span>
                <span>
                  {e.section} P.{e.page}
                </span>
                <span className="ml-auto inline-flex items-center rounded-full bg-primary/8 px-2 py-0.5 font-medium text-primary tabular">
                  {e.scoreKind === "relevance"
                    ? `证据相关性 ${e.conf.toFixed(2)}`
                    : "旧记录 · 请重新分析"}
                </span>
              </div>
              <p className="mt-3 text-sm leading-relaxed">{e.snippet}</p>
              <div className="mt-3 flex items-center justify-between border-t border-border pt-3">
                <span className="rounded-full bg-[color:var(--risk-mid)]/15 px-2 py-0.5 text-[10px] text-[oklch(0.45_0.12_75)]">
                  {e.risk}
                </span>
                <span className="text-xs text-primary">
                  {e.documentId ? "右侧原文已定位" : "未关联 PDF"}
                </span>
              </div>
            </article>
          ))}
          {!filtered.length ? (
            <div className="mt-16 text-center text-xs text-muted-foreground">
              <FileText className="mx-auto mb-2 size-8 opacity-40" />
              没有符合当前筛选条件的证据
            </div>
          ) : null}
        </div>
      </section>

      <aside className="overflow-y-auto border-l border-border bg-card/40 p-5">
        <h3 className="flex items-center gap-2 font-serif text-base font-semibold">
          <FileText className="size-4 text-primary" /> 原文预览
        </h3>
        {selected ? (
          <>
            <div className="mt-1 text-[11px] text-muted-foreground">
              [{selected.id}] · {selected.section} · P.{selected.page}
            </div>
            {selected.documentId ? (
              <PdfViewer
                inline
                open
                onOpenChange={() => undefined}
                documentId={selected.documentId}
                page={selected.page}
                highlight={
                  selected.bbox?.length === 4
                    ? {
                        page: selected.page,
                        bbox: selected.bbox,
                        coordinateSpace: selected.bboxCoordinateSpace,
                        label: selected.section,
                      }
                    : null
                }
                title={`证据 ${selected.id} · ${selected.section}`}
              />
            ) : (
              <div className="mt-4 grid aspect-[3/4] place-items-center rounded-lg border border-border bg-paper text-center text-xs text-muted-foreground">
                该证据未关联 PDF 文档
              </div>
            )}
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
