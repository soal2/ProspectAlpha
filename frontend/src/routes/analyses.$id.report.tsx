import { createFileRoute, getRouteApi, Link } from "@tanstack/react-router";
import { Database, Network, Download, FileBadge, ShieldAlert } from "lucide-react";
import { getReportData } from "@/lib/data-service";
import { RiskBadge } from "@/components/risk-badge";

export const Route = createFileRoute("/analyses/$id/report")({
  loader: async ({ params }) => {
    const report = await getReportData(params.id);
    return { report };
  },
  component: ReportPage,
  head: () => ({ meta: [{ title: "IPO 风险报告 · ProspectAlpha" }] }),
});

function ReportPage() {
  const api = getRouteApi("/analyses/$id");
  const { analysis } = api.useLoaderData();
  const { report } = Route.useLoaderData() as {
    report: {
      markdown: string;
      reviewStatus: string;
      createdAt: string;
      toc: string[];
    } | null;
  };
  const sections = splitMarkdown(report?.markdown ?? "报告尚未生成。");

  return (
    <div className="grid h-[calc(100vh-3.5rem-14.5rem)] min-h-[640px] grid-cols-[280px_minmax(0,1fr)]">
      <aside className="overflow-y-auto border-r border-border bg-card/40 px-4 py-6">
        <div className="px-2">
          <div className="font-serif text-base font-semibold">真实报告目录</div>
          <p className="mt-1 text-[11px] text-muted-foreground">
            来自后端 ReportDraft，不含硬编码演示结论
          </p>
        </div>
        <nav className="mt-5 space-y-0.5">
          {sections.map((section, index) => (
            <a
              key={`${section.title}-${index}`}
              href={`#section-${index}`}
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

      <section className="overflow-y-auto bg-paper">
        <header className="border-b border-border bg-gradient-to-br from-[color:var(--gold-soft)]/60 to-background px-8 py-8">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <RiskBadge level={analysis.riskLevel} />
                <span className="rounded-full bg-card px-2 py-0.5 text-[11px] text-muted-foreground">
                  {report?.reviewStatus ?? "not_ready"}
                </span>
              </div>
              <h1 className="mt-3 font-serif text-3xl font-semibold">
                {analysis.name} · IPO 风险分析报告
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
              <button className="inline-flex h-9 items-center gap-1.5 rounded-full border border-border bg-card px-3 text-xs text-muted-foreground">
                <Download className="size-3.5" /> 导出
              </button>
            </div>
          </div>
          <div className="mt-5 grid gap-3 sm:grid-cols-4">
            <HeroStat icon={ShieldAlert} label="风险评分" value={analysis.riskScore} />
            <HeroStat icon={FileBadge} label="证据" value={analysis.evidenceCount} />
            <HeroStat icon={Network} label="专家" value={analysis.experts} />
            <HeroStat icon={Database} label="报告状态" value={report?.reviewStatus ?? "等待"} />
          </div>
        </header>

        <article className="mx-auto max-w-4xl px-8 py-8">
          {sections.map((section, index) => (
            <section
              key={`${section.title}-${index}`}
              id={`section-${index}`}
              className="mb-8 scroll-mt-8"
            >
              <h2 className="font-serif text-xl font-semibold">{section.title}</h2>
              <div className="mt-3 space-y-2 text-sm leading-relaxed">
                {section.lines.map((line, lineIndex) => renderMarkdownLine(line, lineIndex))}
              </div>
            </section>
          ))}
        </article>
      </section>
    </div>
  );
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

function renderMarkdownLine(line: string, index: number) {
  if (!line.trim()) return <div key={index} className="h-2" />;
  if (line.startsWith("### ")) {
    return (
      <h3 key={index} className="pt-3 text-base font-semibold">
        {line.replace(/^###\s+/, "")}
      </h3>
    );
  }
  if (line.startsWith("- ")) {
    return (
      <p key={index} className="rounded-md border border-border bg-card px-3 py-2">
        {line.replace(/^-\s+/, "")}
      </p>
    );
  }
  return <p key={index}>{line}</p>;
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
