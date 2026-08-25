import { createFileRoute, getRouteApi, Link } from "@tanstack/react-router";
import { Database, Network, Download, ExternalLink, FileBadge, ShieldAlert } from "lucide-react";
import { useCallback, useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getReportData } from "@/lib/data-service";
import { API_BASE_URL } from "@/lib/env";
import { type RiskLevel } from "@/lib/types";
import { RiskBadge } from "@/components/risk-badge";

export const Route = createFileRoute("/analyses/$id/report")({
  loader: async ({ params }) => {
    const report = await getReportData(params.id);
    return { report };
  },
  component: ReportPage,
  head: () => ({ meta: [{ title: "港股IPO风险穿透预警报告 · ProspectAlpha" }] }),
});

function ReportPage() {
  const api = getRouteApi("/analyses/$id");
  const { analysis } = api.useLoaderData();
  const { report } = Route.useLoaderData() as {
    report: {
      markdown: string;
      riskLevel: RiskLevel;
      reviewStatus: string;
      createdAt: string;
      toc: string[];
      exploratoryFindings: {
        finding_id?: string;
        title?: string;
        risk_type?: string;
        reasoning_summary?: string;
        causal_chain?: string;
        company_specificity?: string;
        industry_relevance?: string;
        evidence_grade?: string;
        confidence?: number;
        hypothesis_status?: string;
        evidence_ids?: string[];
        external_evidence_ids?: string[];
        counter_evidence_ids?: string[];
      }[];
      counterevidence: { hypothesis_id?: string; title?: string; status?: string; source_ids?: string[] }[];
      evidenceIndex: { evidence_id?: string; title?: string; url?: string; published_at?: string }[];
    } | null;
  };
  const sections = splitMarkdown(report?.markdown ?? "报告尚未生成。");
  const reportScrollerRef = useRef<HTMLElement>(null);
  const displayedRiskLevel = report?.riskLevel ?? analysis.riskLevel;
  const scrollToSection = useCallback((sectionId: string, behavior: ScrollBehavior) => {
    const scroller = reportScrollerRef.current;
    const target = document.getElementById(sectionId);
    if (!scroller || !target) return;
    const top =
      target.getBoundingClientRect().top -
      scroller.getBoundingClientRect().top +
      scroller.scrollTop;
    scroller.scrollTo({ top, behavior });
  }, []);

  useEffect(() => {
    const syncHashPosition = () => {
      const sectionId = window.location.hash.slice(1);
      if (!sectionId) {
        reportScrollerRef.current?.scrollTo({ top: 0, behavior: "auto" });
        return;
      }
      if (!/^section-\d+$/.test(sectionId)) return;
      window.requestAnimationFrame(() => scrollToSection(sectionId, "auto"));
    };

    syncHashPosition();
    window.addEventListener("hashchange", syncHashPosition);
    window.addEventListener("popstate", syncHashPosition);
    return () => {
      window.removeEventListener("hashchange", syncHashPosition);
      window.removeEventListener("popstate", syncHashPosition);
    };
  }, [scrollToSection, sections.length]);

  return (
    <div className="grid h-[calc(100vh-3.5rem-14.5rem)] min-h-[640px] grid-cols-[280px_minmax(0,1fr)]">
      <aside className="overflow-y-auto border-r border-border bg-card/40 px-4 py-6">
        <div className="px-2">
          <div className="font-serif text-base font-semibold">报告目录</div>
          {/* <p className="mt-1 text-[11px] text-muted-foreground">
            来自后端 ReportDraft，不含硬编码演示结论
          </p> */}
        </div>
        <nav className="mt-5 space-y-0.5">
          {sections.map((section, index) => (
            <a
              key={`${section.title}-${index}`}
              href={`#section-${index}`}
              onClick={(event) => {
                event.preventDefault();
                const sectionId = `section-${index}`;
                const hash = `#${sectionId}`;
                if (window.location.hash !== hash) {
                  window.history.pushState(null, "", hash);
                }
                scrollToSection(sectionId, "smooth");
              }}
              className="flex items-start gap-2 rounded-md px-3 py-2 text-left text-sm text-muted-foreground transition hover:bg-accent hover:text-foreground"
            >
              <span className="grid size-5 shrink-0 place-items-center rounded-full bg-muted text-[10px] font-semibold tabular">
                {index + 1}
              </span>
              <span className="leading-snug">{section.title}</span>
            </a>
          ))}
        </nav>
      </aside>

      <section ref={reportScrollerRef} className="overflow-y-auto bg-paper">
        <header className="border-b border-border bg-gradient-to-br from-[color:var(--gold-soft)]/60 to-background px-8 py-8">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <RiskBadge level={displayedRiskLevel} />
                <span className="rounded-full bg-card px-2 py-0.5 text-[11px] text-muted-foreground">
                  {report?.reviewStatus ?? "not_ready"}
                </span>
              </div>
              <h1 className="mt-3 font-serif text-3xl font-semibold">
                {analysis.name} · 港股IPO风险穿透预警报告
              </h1>
              <p className="mt-2 text-sm text-muted-foreground">
                {analysis.evidenceCount} 条证据 · {analysis.experts} 位专家 · 任务 {analysis.id}
              </p>
              <p className="mt-1 text-xs text-muted-foreground tabular">
                生成时间：{report?.createdAt ?? "等待生成"}
              </p>
            </div>
            <div className="flex shrink-0 gap-1.5">
              <ToolBtn icon={Network} label="Trace" to="/analyses/$id/trace" id={analysis.id} />
              <ToolBtn
                icon={Database}
                label="证据库"
                to="/analyses/$id/evidence"
                id={analysis.id}
              />
              <a
                href={report ? reportArtifactUrl(analysis.id) : undefined}
                target="_blank"
                rel="noreferrer"
                aria-disabled={!report}
                className="inline-flex h-9 items-center gap-1.5 rounded-full border border-border bg-card px-3 text-xs text-muted-foreground hover:bg-accent aria-disabled:pointer-events-none aria-disabled:opacity-50"
              >
                <ExternalLink className="size-3.5" /> 预览 HTML
              </a>
              <a
                href={report ? `${reportArtifactUrl(analysis.id)}?download=1` : undefined}
                onClick={(event) => {
                  event.preventDefault();
                  if (report) void downloadHtml(reportArtifactUrl(analysis.id), `${analysis.name}-港股IPO风险报告-${analysis.id}.html`);
                }}
                aria-disabled={!report}
                className="inline-flex h-9 items-center gap-1.5 rounded-full border border-border bg-card px-3 text-xs text-muted-foreground hover:bg-accent aria-disabled:pointer-events-none aria-disabled:opacity-50"
              >
                <Download className="size-3.5" /> 下载 HTML
              </a>
            </div>
          </div>
          <div className="mt-5 grid gap-3 sm:grid-cols-4">
            <HeroStat
              icon={ShieldAlert}
              label="风险评级"
              value={riskRatingLabel(displayedRiskLevel)}
            />
            <HeroStat icon={FileBadge} label="证据" value={analysis.evidenceCount} />
            <HeroStat icon={Network} label="专家" value={analysis.experts} />
            <HeroStat icon={Database} label="报告状态" value={report?.reviewStatus ?? "等待"} />
          </div>
        </header>

        <article className="mx-auto max-w-4xl px-8 py-8">
          {(report?.exploratoryFindings.length ?? 0) > 0 && (
            <section className="mb-8">
              <h2 className="font-serif text-xl font-semibold">探索性隐性风险</h2>
              <p className="mt-1 text-xs text-muted-foreground">低置信度和线索级证据仍展示；置信度只用于说明，不决定准入或评分。</p>
              <div className="mt-4 grid gap-3">
                {report!.exploratoryFindings.map((finding, index) => (
                  <div key={finding.finding_id ?? index} className="rounded-xl border border-border bg-card p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="font-medium">{finding.title ?? "未命名风险"}</h3>
                      <span className="rounded-full bg-muted px-2 py-0.5 text-[11px]">{finding.risk_type ?? "自由风险"}</span>
                      <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-800">{finding.evidence_grade ?? "lead"}</span>
                      <span className="ml-auto text-[11px] text-muted-foreground">置信度 {Number(finding.confidence ?? 0).toFixed(2)} · {finding.hypothesis_status ?? "reported"}</span>
                    </div>
                    <p className="mt-3 text-sm leading-6">{finding.reasoning_summary}</p>
                    <p className="mt-2 text-xs text-muted-foreground">因果链：{finding.causal_chain || "未展开"}</p>
                    <p className="mt-1 text-xs text-muted-foreground">公司特异性：{finding.company_specificity || "未说明"} · 行业关联：{finding.industry_relevance || "未说明"}</p>
                    <p className="mt-2 text-[11px] text-muted-foreground">招股书证据：{finding.evidence_ids?.join("、") || "无"} · 外部来源：{finding.external_evidence_ids?.join("、") || "未取得"} · 反证：{finding.counter_evidence_ids?.join("、") || "无"}</p>
                    {(finding.external_evidence_ids?.length ?? 0) > 0 && (
                      <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
                        {finding.external_evidence_ids!.map((sourceId) => {
                          const source = report!.evidenceIndex.find((item) => item.evidence_id === sourceId);
                          return source?.url ? <a key={sourceId} href={source.url} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-2">{source.title || sourceId} · {source.published_at || "日期不明"}</a> : <span key={sourceId}>{sourceId}</span>;
                        })}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}
          {(report?.counterevidence.length ?? 0) > 0 && (
            <section className="mb-8 rounded-xl border border-border bg-muted/30 p-4">
              <h2 className="font-serif text-lg font-semibold">反证与争议</h2>
              <ul className="mt-3 space-y-2 text-xs text-muted-foreground">
                {report!.counterevidence.map((item, index) => <li key={item.hypothesis_id ?? index}>{item.title ?? "风险假设"} · {item.status ?? "disputed"} · 来源 {item.source_ids?.join("、") || "无"}</li>)}
              </ul>
            </section>
          )}
          {sections.map((section, index) => (
            <section
              key={`${section.title}-${index}`}
              id={`section-${index}`}
              className="mb-8 scroll-mt-8"
            >
              <h2 className="font-serif text-xl font-semibold">{section.title}</h2>
              <ReportMarkdown markdown={section.lines.join("\n")} />
            </section>
          ))}
        </article>
      </section>
    </div>
  );
}

function riskRatingLabel(level: RiskLevel) {
  return {
    very_high: "极高风险",
    high: "高风险",
    medium: "中风险",
    low: "低风险",
    very_low: "极低风险",
    unrated: "未评级",
  }[level];
}

function reportArtifactUrl(runId: string) {
  return `${API_BASE_URL}/analysis-runs/${runId}/report/artifact`;
}

async function downloadHtml(url: string, filename: string) {
  const response = await fetch(`${url}?download=1`);
  if (!response.ok) throw new Error("报告下载失败");
  const blob = new Blob([await response.text()], { type: "text/html;charset=utf-8" });
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(objectUrl);
}

function splitMarkdown(markdown: string) {
  const lines = markdown.split("\n");
  const sections: { title: string; lines: string[] }[] = [];
  let current: { title: string; lines: string[] } | null = null;
  for (const line of lines) {
    if (line.startsWith("# ")) {
      current = { title: line.replace(/^#\s+/, ""), lines: [] };
      sections.push(current);
      continue;
    }
    if (line.startsWith("## ")) {
      current = { title: line.replace(/^##\s+/, ""), lines: [] };
      sections.push(current);
      continue;
    }
    if (!current) {
      current = { title: "报告", lines: [] };
      sections.push(current);
    }
    current.lines.push(line);
  }
  return sections.filter((section) => section.title || section.lines.some(Boolean));
}

function ReportMarkdown({ markdown }: { markdown: string }) {
  return (
    <div className="mt-4 text-sm leading-7 text-foreground/90">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h3: ({ children }) => (
            <h3 className="mb-3 mt-7 border-l-2 border-[color:var(--gold)] pl-3 font-serif text-base font-semibold text-foreground">
              {children}
            </h3>
          ),
          p: ({ children }) => <p className="my-3">{children}</p>,
          ul: ({ children }) => <ul className="my-3 list-disc space-y-1 pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="my-3 list-decimal space-y-1 pl-5">{children}</ol>,
          li: ({ children }) => (
            <li className="pl-1 marker:text-[color:var(--gold)]">{children}</li>
          ),
          blockquote: ({ children }) => (
            <blockquote className="my-4 border-l-2 border-[color:var(--gold)] bg-[color:var(--gold-soft)]/35 px-4 py-2 text-muted-foreground">
              {children}
            </blockquote>
          ),
          table: ({ children }) => (
            <div className="my-5 overflow-x-auto rounded-lg border border-border bg-card shadow-[0_1px_0_rgba(0,0,0,0.03)]">
              <table className="w-full min-w-[720px] border-collapse text-left text-xs leading-5">
                {children}
              </table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="bg-muted/80 font-semibold text-foreground">{children}</thead>
          ),
          tbody: ({ children }) => <tbody className="divide-y divide-border">{children}</tbody>,
          tr: ({ children }) => (
            <tr className="align-top transition-colors hover:bg-muted/35">{children}</tr>
          ),
          th: ({ children }) => (
            <th className="whitespace-nowrap border-r border-border px-3 py-2.5 last:border-r-0">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="max-w-[360px] border-r border-border px-3 py-2.5 text-muted-foreground last:border-r-0">
              {children}
            </td>
          ),
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="font-medium text-[color:var(--gold)] underline decoration-[color:var(--gold)]/35 underline-offset-4 hover:decoration-current"
            >
              {children}
            </a>
          ),
          hr: () => <hr className="my-8 border-border" />,
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}

function ToolBtn({
  icon: Icon,
  label,
  to,
  id,
}: {
  icon: typeof Network;
  label: string;
  to: "/analyses/$id/trace" | "/analyses/$id/evidence";
  id: string;
}) {
  return (
    <Link
      to={to}
      params={{ id }}
      className="inline-flex h-9 items-center gap-1.5 rounded-full border border-border bg-card px-3 text-xs text-muted-foreground hover:bg-accent"
    >
      <Icon className="size-3.5" /> {label}
    </Link>
  );
}

function HeroStat({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof ShieldAlert;
  label: string;
  value: string | number;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <Icon className="size-3.5 text-primary" /> {label}
      </div>
      <div className="mt-1 text-lg font-semibold tabular">{value}</div>
    </div>
  );
}
