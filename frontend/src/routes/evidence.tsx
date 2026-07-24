import { createFileRoute } from "@tanstack/react-router";
import { Search, FileText } from "lucide-react";
import { useState, useEffect, useMemo } from "react";
import { getKnowledgeData } from "@/lib/data-service";
import { PdfViewer } from "@/components/pdf-viewer";
import type { KnowledgeData, KnowledgeEvidence } from "@/lib/types";

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

const CONFIDENCE_BUCKETS = ["全部", "≥ 0.9 高", "0.7–0.9 中", "< 0.7 低", "未核验"] as const;

function matchConfidence(bucket: string, conf: number) {
  if (bucket === "≥ 0.9 高") return conf >= 0.9;
  if (bucket === "0.7–0.9 中") return conf >= 0.7 && conf < 0.9;
  if (bucket === "< 0.7 低") return conf < 0.7;
  return true;
}

function matchesConfidenceBucket(bucket: string, item: KnowledgeEvidence) {
  if (bucket === "未核验") return item.scoreKind !== "relevance";
  return item.scoreKind === "relevance" && matchConfidence(bucket, item.conf);
}

function EvidencePage() {
  const [data, setData] = useState<KnowledgeData | null>(null);
  const [keyword, setKeyword] = useState("");
  const [company, setCompany] = useState("全部");
  const [section, setSection] = useState("全部");
  const [risk, setRisk] = useState("全部");
  const [confidence, setConfidence] = useState("全部");
  const [selected, setSelected] = useState<KnowledgeEvidence | null>(null);

  useEffect(() => {
    let cancelled = false;
    getKnowledgeData().then((d) => {
      if (!cancelled) {
        setData(d);
        setSelected(d.evidence[0] ?? null);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    const items = filterEvidence(data?.evidence ?? [], {
      company,
      section,
      risk,
      confidence,
      keyword,
    });
    return items.sort((a, b) => b.conf - a.conf);
  }, [data, company, section, risk, confidence, keyword]);

  const facetGroups = useMemo(() => {
    const evidence = data?.evidence ?? [];
    const filters = { company, section, risk, confidence, keyword };
    const companyEvidence = filterEvidence(evidence, filters, "company");
    const sectionEvidence = filterEvidence(evidence, filters, "section");
    const riskEvidence = filterEvidence(evidence, filters, "risk");
    const confidenceEvidence = filterEvidence(evidence, filters, "confidence");
    return [
      {
        label: "公司",
        value: company,
        set: setCompany,
        counts: countByFor(companyEvidence, (item) => item.name),
        total: companyEvidence.length,
      },
      {
        label: "招股书章节",
        value: section,
        set: setSection,
        counts: countByFor(sectionEvidence, (item) => item.section),
        total: sectionEvidence.length,
      },
      {
        label: "风险类别",
        value: risk,
        set: setRisk,
        counts: countByFor(riskEvidence, (item) => item.risk),
        total: riskEvidence.length,
      },
      {
        label: "证据相关性",
        value: confidence,
        set: setConfidence,
        counts: new Map(
          CONFIDENCE_BUCKETS.filter((bucket) => bucket !== "全部").map((bucket) => [
            bucket,
            confidenceEvidence.filter((item) => matchesConfidenceBucket(bucket, item)).length,
          ]),
        ),
        total: confidenceEvidence.length,
      },
    ];
  }, [data, company, section, risk, confidence, keyword]);

  return (
    <div className="grid h-[calc(100vh-3.5rem)] grid-cols-[240px_minmax(360px,1fr)_minmax(460px,40vw)]">
      {/* facets */}
      <aside className="overflow-y-auto border-r border-border bg-card/40 p-5">
        <h2 className="font-serif text-lg font-semibold">证据知识库</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          {data?.stats.totalEvidence ?? 0} 条证据 · {data?.stats.totalCompanies ?? 0} 家公司
        </p>
        <div className="relative mt-4">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="关键词搜索"
            className="h-9 w-full rounded-lg border border-border bg-background pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="mt-6 space-y-5">
          {facetGroups.map((facet) => (
            <div key={facet.label}>
              <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                {facet.label}
              </div>
              <ul className="mt-2 space-y-1 text-sm">
                {["全部", ...facet.counts.keys()].map((item) => (
                  <li key={item}>
                    <button
                      onClick={() => facet.set(item)}
                      className={`flex w-full items-center justify-between rounded-md px-2 py-1 text-left ${
                        facet.value === item
                          ? "bg-primary/8 text-primary"
                          : "text-muted-foreground hover:bg-accent"
                      }`}
                    >
                      <span className="truncate">{item}</span>
                      <span className="text-[10px] tabular">
                        {item === "全部" ? facet.total : (facet.counts.get(item) ?? 0)}
                      </span>
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
              {filtered.length} / {data?.evidence.length ?? 0} 条 · 按证据相关性排序
            </p>
          </div>
        </div>
        <div className="mt-5 space-y-3">
          {filtered.map((e) => (
            <article
              key={e.id}
              onClick={() => setSelected(e)}
              className={`cursor-pointer rounded-xl border bg-card p-4 transition hover:border-primary/30 ${
                selected?.id === e.id ? "border-primary/50" : "border-border"
              }`}
            >
              <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                <span className="tabular font-medium text-foreground">{e.code}</span>
                <span>·</span>
                <span>{e.name}</span>
                <span>·</span>
                <span>
                  {e.section} P.{e.page}
                </span>
                <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-primary/8 px-2 py-0.5 font-medium text-primary tabular">
                  {e.scoreKind === "relevance"
                    ? `相关性 ${e.conf.toFixed(2)}`
                    : e.scoreKind === "retrieval"
                      ? e.conf > 0
                        ? `检索候选分 ${e.conf.toFixed(2)} · 未核验`
                        : "检索候选 · 历史任务未记录分数"
                      : "旧记录 · 请重新分析"}
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

      {/* preview */}
      <aside className="overflow-y-auto border-l border-border bg-card/40 p-5">
        <h3 className="flex items-center gap-2 font-serif text-base font-semibold">
          <FileText className="size-4 text-primary" /> 原文预览
        </h3>
        {selected ? (
          <>
            <div className="mt-1 text-[11px] text-muted-foreground">
              {selected.id} · {selected.code} · {selected.section} · P.{selected.page}
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
                title={`${selected.name} · ${selected.section}`}
              />
            ) : (
              <div className="mt-4 grid aspect-[3/4] place-items-center rounded-lg border border-border bg-paper text-center text-xs text-muted-foreground">
                该证据未关联 PDF 文档
              </div>
            )}
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

type EvidenceFilters = {
  company: string;
  section: string;
  risk: string;
  confidence: string;
  keyword: string;
};

function filterEvidence(
  evidence: KnowledgeEvidence[],
  filters: EvidenceFilters,
  skip?: keyof Omit<EvidenceFilters, "keyword">,
) {
  const query = filters.keyword.trim().toLowerCase();
  return evidence.filter((item) => {
    if (skip !== "company" && filters.company !== "全部" && item.name !== filters.company)
      return false;
    if (skip !== "section" && filters.section !== "全部" && item.section !== filters.section)
      return false;
    if (skip !== "risk" && filters.risk !== "全部" && item.risk !== filters.risk) return false;
    if (
      skip !== "confidence" &&
      filters.confidence !== "全部" &&
      !matchesConfidenceBucket(filters.confidence, item)
    ) {
      return false;
    }
    if (query) {
      const haystack = `${item.snippet} ${item.section} ${item.name} ${item.risk}`.toLowerCase();
      if (!haystack.includes(query)) return false;
    }
    return true;
  });
}

function countByFor(evidence: KnowledgeEvidence[], keyFor: (item: KnowledgeEvidence) => string) {
  const counts = new Map<string, number>();
  for (const item of evidence) {
    const key = keyFor(item);
    if (key) counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return counts;
}
