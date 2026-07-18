import { useEffect, useMemo, useState } from "react";
import { createFileRoute, getRouteApi } from "@tanstack/react-router";
import {
  BookOpen,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  Database,
  Download,
  ExternalLink,
  FlaskConical,
  MessageSquarePlus,
  PanelLeft,
  PanelRight,
  SearchCheck,
  Sparkles,
} from "lucide-react";
import {
  addV2ReportAnnotation,
  getLatestV2Report,
  getV2Report,
  getV2ReportExportUrl,
  getV2ReportVersions,
  getReportData,
  requestV2ResearchSupplement,
  resolveV2ReportRisk,
  type ReportModuleKind,
  type V2ReportBundle,
  type V2ReportComponent,
  type V2ReportModule,
  type V2ReportSection,
} from "@/lib/data-service";
import { RiskBadge } from "@/components/risk-badge";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/analyses/$id/report")({
  loader: async ({ params }) => {
    const [v2Report, legacyReport] = await Promise.all([
      getLatestV2Report(params.id),
      getReportData(params.id),
    ]);
    return { v2Report, legacyReport };
  },
  component: ReportPage,
  head: () => ({ meta: [{ title: "IPO 风险报告 · ProspectAlpha" }] }),
});

type FocusTarget = {
  kind: "hypothesis" | "finding" | "evidence" | "section";
  id: string;
  title: string;
  supporting?: SourceCard[];
  counter?: SourceCard[];
};

type SourceCard = {
  evidence_id: string;
  url: string;
  title?: string;
  site_name?: string;
  tier?: string;
  accessible?: boolean;
};

function ReportPage() {
  const { v2Report, legacyReport } = Route.useLoaderData();
  if (!v2Report) return <LegacyReportPage report={legacyReport} />;
  return <ResearchWorkbench initialBundle={v2Report} />;
}

function ResearchWorkbench({ initialBundle }: { initialBundle: V2ReportBundle }) {
  const api = getRouteApi("/analyses/$id");
  const { analysis } = api.useLoaderData();
  const [bundle, setBundle] = useState(initialBundle);
  const [versions, setVersions] = useState<V2ReportBundle[]>([initialBundle]);
  const [moduleKind, setModuleKind] = useState<ReportModuleKind>("decision_brief");
  const [riskFilter, setRiskFilter] = useState<"all" | "verified" | "unresolved">("all");
  const [focus, setFocus] = useState<FocusTarget | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [annotation, setAnnotation] = useState("");
  const [supplement, setSupplement] = useState("");
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    void getV2ReportVersions(analysis.id).then(setVersions).catch(() => undefined);
  }, [analysis.id]);

  const module = moduleKind === "decision_brief" ? bundle.decision_brief : bundle.deep_research;
  const sources = useMemo(() => collectSources(module.components), [module.components]);
  const selectedSources = useMemo(() => {
    if (!focus) return sources;
    const related = [...(focus.supporting ?? []), ...(focus.counter ?? [])];
    return related.length ? related : sources.filter((source) => source.evidence_id === focus.id);
  }, [focus, sources]);
  const sections = useMemo(
    () => filterSections(module, bundle, riskFilter),
    [module, bundle, riskFilter],
  );

  async function changeVersion(version: number) {
    if (version === bundle.report_version) return;
    setNotice(null);
    try {
      setBundle(await getV2Report(analysis.id, version));
      setFocus(null);
    } catch {
      setNotice("无法载入该报告版本，请稍后重试。");
    }
  }

  async function saveAnnotation() {
    if (!focus || !annotation.trim()) return;
    setIsSaving(true);
    try {
      await addV2ReportAnnotation(analysis.id, bundle.report_version, {
        target_kind: focus.kind,
        target_id: focus.id,
        body: annotation.trim(),
      });
      setAnnotation("");
      setNotice("批注已记录在该报告版本的审阅轨迹中。");
    } catch {
      setNotice("批注未能保存，请检查网络后重试。");
    } finally {
      setIsSaving(false);
    }
  }

  async function resolveRisk(action: "accept" | "downgrade" | "dismiss") {
    if (!focus || (focus.kind !== "hypothesis" && focus.kind !== "finding")) return;
    setIsSaving(true);
    try {
      await resolveV2ReportRisk(analysis.id, bundle.report_version, {
        target_kind: focus.kind,
        target_id: focus.id,
        action,
        reason: `在报告工作台中标记为${action}`,
      });
      setNotice("处置意见已登记；原始报告版本保持不变。");
    } catch {
      setNotice("处置意见未能保存，请稍后重试。");
    } finally {
      setIsSaving(false);
    }
  }

  async function createSupplement() {
    if (!supplement.trim()) return;
    setIsSaving(true);
    try {
      await requestV2ResearchSupplement(analysis.id, {
        query: supplement.trim(),
        focus_hypothesis_id: focus?.kind === "hypothesis" ? focus.id : undefined,
      });
      setSupplement("");
      setNotice("补充调研任务已创建，等待研究流程接续处理。");
    } catch {
      setNotice("补充调研任务创建失败，请稍后重试。");
    } finally {
      setIsSaving(false);
    }
  }

  const toc = (
    <ReportNavigator
      module={module}
      bundle={bundle}
      filter={riskFilter}
      onFilter={setRiskFilter}
      onSection={(id) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" })}
      version={bundle.report_version}
      versions={versions}
      onVersion={changeVersion}
    />
  );
  const evidence = (
    <EvidenceRail
      focus={focus}
      sources={selectedSources}
      annotation={annotation}
      supplement={supplement}
      busy={isSaving}
      onAnnotation={setAnnotation}
      onSupplement={setSupplement}
      onSaveAnnotation={saveAnnotation}
      onCreateSupplement={createSupplement}
      onResolve={resolveRisk}
    />
  );

  return (
    <div className="flex min-h-[calc(100vh-14.5rem)] flex-col bg-[#f7f8fa]">
      <header className="border-b border-border bg-paper px-4 py-3 lg:px-6">
        <div className="mx-auto flex max-w-[1800px] items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="eyebrow text-primary/70">Research dossier · frozen artifact v{bundle.research_artifact_version}</div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <span>版本化研究产物</span>
              {bundle.review_state === "review_required" && (
                <span className="rounded-full bg-risk-mid/10 px-2 py-0.5 text-[11px] font-medium text-risk-mid">需人工复核</span>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <Sheet>
              <SheetTrigger asChild>
                <button className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border bg-paper px-3 text-xs xl:hidden"><PanelLeft className="size-3.5" /> 目录</button>
              </SheetTrigger>
              <SheetContent side="left" className="w-[88vw] overflow-y-auto p-0"><SheetTitle className="sr-only">报告目录</SheetTitle>{toc}</SheetContent>
            </Sheet>
            <a href={getV2ReportExportUrl(analysis.id, bundle.report_version)} className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border bg-paper px-3 text-xs text-muted-foreground hover:text-foreground"><Download className="size-3.5" /> 导出 Markdown</a>
            <Sheet>
              <SheetTrigger asChild>
                <button className="inline-flex h-9 items-center gap-1.5 rounded-md bg-primary px-3 text-xs text-primary-foreground xl:hidden"><PanelRight className="size-3.5" /> 证据</button>
              </SheetTrigger>
              <SheetContent side="right" className="w-[92vw] overflow-y-auto p-0"><SheetTitle className="sr-only">证据与审阅</SheetTitle>{evidence}</SheetContent>
            </Sheet>
          </div>
        </div>
      </header>

      {notice && <div className="border-b border-gold/30 bg-gold-soft/40 px-6 py-2 text-center text-xs text-primary">{notice}</div>}

      <div className="mx-auto hidden w-full max-w-[1800px] flex-1 xl:block"><ResizablePanelGroup orientation="horizontal" className="h-full">
        <ResizablePanel defaultSize="18%" minSize="220px" maxSize="30%" className="border-r border-border bg-paper">{toc}</ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel defaultSize="58%" minSize="480px">
          <main className="h-full min-w-0 overflow-y-auto">
            <article className="mx-auto max-w-5xl px-4 py-6 sm:px-8 lg:px-10">
              <ReportHero analysis={analysis} bundle={bundle} module={module} moduleKind={moduleKind} onModule={setModuleKind} />
              {sections.map(({ section, components }) => (
                <ReportSection key={section.section_id} section={section} components={components} onFocus={setFocus} />
              ))}
            </article>
          </main>
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel defaultSize="24%" minSize="300px" maxSize="34%" className="border-l border-border bg-paper">{evidence}</ResizablePanel>
      </ResizablePanelGroup></div>
      <main className="min-w-0 flex-1 xl:hidden">
        <article className="mx-auto max-w-5xl px-4 py-6 sm:px-8">
          <ReportHero analysis={analysis} bundle={bundle} module={module} moduleKind={moduleKind} onModule={setModuleKind} />
          {sections.map(({ section, components }) => (
            <ReportSection key={section.section_id} section={section} components={components} onFocus={setFocus} />
          ))}
        </article>
      </main>
    </div>
  );
}

function ReportHero({ analysis, bundle, module, moduleKind, onModule }: { analysis: { name: string; evidenceCount: number; riskScore: number; riskLevel: "high" | "mid" | "low" | "unrated" }; bundle: V2ReportBundle; module: V2ReportModule; moduleKind: ReportModuleKind; onModule: (value: ReportModuleKind) => void }) {
  const counts = [
    ["已验证", bundle.selection.verified_hypothesis_ids.length, "text-risk-high"],
    ["重大待核实", bundle.selection.material_unresolved_hypothesis_ids.length, "text-risk-mid"],
    ["已纳入证据", analysis.evidenceCount, "text-primary"],
  ];
  return <section className="mb-8 overflow-hidden rounded-xl border border-border bg-paper soft-ring">
    <div className="border-b border-border bg-[linear-gradient(120deg,#071e34_0%,#123d62_62%,#204c73_100%)] px-6 py-7 text-white sm:px-8">
      <div className="flex flex-wrap items-start justify-between gap-5">
        <div><div className="eyebrow text-gold-soft">Version {bundle.report_version} · {bundle.industry_profile.detected === "biotech" ? "Biotech profile" : "General profile"}</div><h1 className="mt-3 font-serif text-3xl font-semibold">{moduleKind === "decision_brief" ? "投资决策简报" : "专项深度研究报告"}</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-slate-200">基于冻结研究产物生成。正式结论与待核实事项分层呈现；所有可用外部来源均保留原始链接。</p></div>
        <div className="rounded-lg border border-white/15 bg-white/10 px-4 py-3 text-right"><div className="text-[10px] uppercase tracking-[0.18em] text-slate-300">风险画像</div><div className="mt-1 text-2xl font-semibold tabular">{analysis.riskLevel === "unrated" ? "—" : analysis.riskScore}</div></div>
      </div>
      <div className="mt-6 inline-flex rounded-lg border border-white/15 bg-black/10 p-1">
        {(["decision_brief", "deep_research"] as const).map((kind) => <button key={kind} onClick={() => onModule(kind)} className={cn("rounded-md px-3 py-1.5 text-xs transition", moduleKind === kind ? "bg-white text-primary shadow-sm" : "text-slate-200 hover:bg-white/10")}>{kind === "decision_brief" ? "决策简报" : "深度研究"}</button>)}
      </div>
    </div>
    <div className="grid grid-cols-3 divide-x divide-border">{counts.map(([label, value, color]) => <div key={label} className="px-4 py-4 sm:px-6"><div className="text-[11px] text-muted-foreground">{label}</div><div className={cn("mt-1 text-2xl font-semibold tabular", color)}>{value}</div></div>)}</div>
  </section>;
}

function ReportNavigator({ module, bundle, filter, onFilter, onSection, version, versions, onVersion }: { module: V2ReportModule; bundle: V2ReportBundle; filter: "all" | "verified" | "unresolved"; onFilter: (value: "all" | "verified" | "unresolved") => void; onSection: (id: string) => void; version: number; versions: V2ReportBundle[]; onVersion: (value: number) => void }) {
  const filters: Array<["all" | "verified" | "unresolved", string, number]> = [["all", "全部", module.sections.length], ["verified", "已验证", bundle.selection.verified_hypothesis_ids.length], ["unresolved", "待核实", bundle.selection.material_unresolved_hypothesis_ids.length]];
  return <div className="h-full p-4"><div className="flex items-center gap-2"><BookOpen className="size-4 text-primary"/><div className="font-serif font-semibold">报告目录</div></div><div className="mt-5"><div className="text-[11px] font-medium text-muted-foreground">风险范围</div><div className="mt-2 flex flex-wrap gap-1">{filters.map(([key, label, count]) => <button key={key} onClick={() => onFilter(key)} className={cn("rounded-full px-2 py-1 text-[11px]", filter === key ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:text-foreground")}>{label} {count}</button>)}</div></div><nav className="mt-6 space-y-1">{module.sections.map((section, index) => <button key={section.section_id} onClick={() => onSection(`section-${section.section_id}`)} className="flex w-full items-start gap-2 rounded-md px-2 py-2 text-left text-sm text-muted-foreground hover:bg-muted hover:text-foreground"><span className="grid size-5 shrink-0 place-items-center rounded-full bg-muted text-[10px] tabular">{index + 1}</span><span className="leading-snug">{section.title_zh}</span>{section.non_formal && <span className="ml-auto mt-0.5 text-[9px] text-risk-mid">附录</span>}</button>)}</nav><div className="mt-8 border-t border-border pt-4"><label className="text-[11px] font-medium text-muted-foreground">报告版本</label><select aria-label="报告版本" value={version} onChange={(event) => onVersion(Number(event.target.value))} className="field-input mt-2 py-2 text-xs">{versions.map((item) => <option key={item.report_version} value={item.report_version}>v{item.report_version} · 产物 v{item.research_artifact_version}</option>)}</select></div></div>;
}

function ReportSection({ section, components, onFocus }: { section: V2ReportSection; components: V2ReportComponent[]; onFocus: (target: FocusTarget) => void }) {
  return <section id={`section-${section.section_id}`} className="scroll-mt-6 border-b border-border py-8 last:border-b-0"><div className="mb-4 flex items-start justify-between gap-4"><div><div className="eyebrow text-primary/55">Section</div><h2 className="mt-1 font-serif text-2xl font-semibold">{section.title_zh}</h2></div>{section.non_formal && <span className="rounded-full bg-risk-mid/10 px-2.5 py-1 text-[11px] font-medium text-risk-mid">非正式结论</span>}</div>{section.non_formal && <div className="mb-4 rounded-md border-l-2 border-risk-mid bg-risk-mid/5 px-3 py-2 text-xs leading-5 text-risk-mid">本章节保留调查轨迹与弱信号，不构成正式结论。</div>}<MarkdownBody markdown={section.markdown} suppressFirstHeading />{components.map((component, index) => <ReportComponent key={`${component.kind}-${index}`} component={component} onFocus={onFocus} />)}</section>;
}

function MarkdownBody({ markdown, suppressFirstHeading = false }: { markdown: string; suppressFirstHeading?: boolean }) {
  let skippedHeading = false;
  const lines = markdown.split("\n").filter((line) => {
    if (suppressFirstHeading && !skippedHeading && /^#{1,6}\s+/.test(line)) {
      skippedHeading = true;
      return false;
    }
    return !line.startsWith("```") && !line.startsWith("<!--") && !line.includes("\"kind\":") && !line.includes("\"cells\":") && !line.includes("\"items\":");
  });
  return <div className="space-y-2 text-sm leading-7 text-foreground/85">{lines.map((line, index) => { const text = line.replace(/^#{1,6}\s+/, "").replace(/\*\*/g, ""); if (!text.trim()) return <div key={index} className="h-2" />; if (line.startsWith(">")) return <p key={index} className="border-l-2 border-gold pl-3 text-muted-foreground">{text.replace(/^>\s*/, "")}</p>; if (line.startsWith("- ")) return <p key={index} className="flex gap-2"><span className="mt-3 size-1 shrink-0 rounded-full bg-gold" />{text.replace(/^-\s*/, "")}</p>; return <p key={index}>{text}</p>; })}</div>;
}

function ReportComponent({ component, onFocus }: { component: V2ReportComponent; onFocus: (target: FocusTarget) => void }) {
  const payload = component.payload;
  if (payload.available === false) return <div className="mt-5 rounded-lg border border-dashed border-border bg-muted/35 px-4 py-3 text-xs text-muted-foreground">该结构化组件在本次研究中没有可用数据；未以推测性内容填充。</div>;
  if (component.kind === "risk_profile_heatmap") return <Heatmap payload={payload} />;
  if (component.kind === "risk_card") return <RiskCards payload={payload} onFocus={onFocus} />;
  if (component.kind === "risk_matrix") return <RiskMatrix payload={payload} />;
  if (component.kind === "hypothesis_matrix") return <HypothesisMatrix payload={payload} onFocus={onFocus} />;
  if (component.kind === "source_quality") return <SourceQuality payload={payload} />;
  if (component.kind === "event_timeline") return <Timeline payload={payload} onFocus={onFocus} />;
  if (component.kind === "risk_causal_graph") return <CausalGraph payload={payload} onFocus={onFocus} />;
  if (component.kind === "financial_trend" || component.kind === "peer_comparison" || component.kind === "pipeline_chart") return <DataStrip payload={payload} kind={component.kind} />;
  return null;
}

function Heatmap({ payload }: { payload: Record<string, unknown> }) { const cells = list(payload.cells); return <div className="mt-5 grid grid-cols-3 gap-2 sm:grid-cols-5">{cells.map((cell, index) => { const item = record(cell); const score = num(item.verified_count) * 2 + num(item.material_unresolved_count) * 2 + num(item.contested_count); return <div key={index} className={cn("rounded-lg border p-3", score >= 5 ? "border-risk-high/30 bg-risk-high/5" : score >= 2 ? "border-risk-mid/30 bg-risk-mid/5" : "border-border bg-muted/30")}><div className="text-xs font-medium">{str(item.dimension)}</div><div className="mt-2 flex items-end justify-between"><span className="text-[10px] text-muted-foreground">已验证 / 待核实</span><span className="font-semibold tabular">{num(item.verified_count)} / {num(item.material_unresolved_count)}</span></div></div> })}</div>; }

function RiskCards({ payload, onFocus }: { payload: Record<string, unknown>; onFocus: (target: FocusTarget) => void }) { const items = list(payload.items); if (!items.length) return <Empty label="本节没有达到展示门槛的风险事项。" />; return <div className="mt-5 space-y-3">{items.map((item, index) => { const row = record(item); const supporting = sourceList(row.supporting); const counter = sourceList(row.counter); return <button key={str(row.hypothesis_id) || index} onClick={() => onFocus({ kind: "hypothesis", id: str(row.hypothesis_id), title: str(row.title), supporting, counter })} className="block w-full rounded-lg border border-border bg-paper p-4 text-left transition hover:border-primary/35 hover:shadow-sm"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex flex-wrap gap-2 text-[10px]"><Tag text={str(row.dimension)} /><Tag text={importanceLabel(str(row.importance))} tone={str(row.importance)} /><Tag text={statusLabel(str(row.status))} tone={str(row.status)} /></div><h3 className="mt-2 text-sm font-semibold leading-6">{str(row.title)}</h3></div><ChevronRight className="mt-1 size-4 text-muted-foreground" /></div><div className="mt-3 grid gap-2 text-xs sm:grid-cols-2"><EvidenceCount icon={CheckCircle2} label="支持证据" count={supporting.length} /><EvidenceCount icon={SearchCheck} label="反证检索" count={counter.length} /></div></button> })}</div>; }

function RiskMatrix({ payload }: { payload: Record<string, unknown> }) { const rows = list(payload.rows); return <div className="mt-5 overflow-x-auto rounded-lg border border-border"><table className="w-full min-w-[560px] text-left text-xs"><thead className="bg-muted/60 text-muted-foreground"><tr><th className="px-3 py-2 font-medium">维度</th><th className="px-3 py-2 font-medium">重要性</th><th className="px-3 py-2 font-medium">置信度</th><th className="px-3 py-2 font-medium">支持 / 反证</th></tr></thead><tbody>{rows.map((item, index) => { const row = record(item); return <tr key={index} className="border-t border-border"><td className="px-3 py-2">{str(row.dimension)}</td><td className="px-3 py-2">{importanceLabel(str(row.importance))}</td><td className="px-3 py-2">{confidenceLabel(str(row.confidence_level))}</td><td className="px-3 py-2 tabular">{num(row.supporting_count)} / {num(row.counter_count)}</td></tr> })}</tbody></table></div>; }

function HypothesisMatrix({ payload, onFocus }: { payload: Record<string, unknown>; onFocus: (target: FocusTarget) => void }) { const rows = list(payload.rows); return <div className="mt-5 overflow-x-auto rounded-lg border border-risk-mid/20"><table className="w-full min-w-[660px] text-left text-xs"><thead className="bg-risk-mid/5 text-muted-foreground"><tr><th className="px-3 py-2">假设</th><th className="px-3 py-2">维度</th><th className="px-3 py-2">状态</th><th className="px-3 py-2">反证检索</th></tr></thead><tbody>{rows.map((item, index) => { const row = record(item); return <tr key={index} className="cursor-pointer border-t border-border hover:bg-muted/40" onClick={() => onFocus({ kind: "hypothesis", id: str(row.hypothesis_id), title: str(row.title) })}><td className="px-3 py-2 font-medium">{str(row.title)}</td><td className="px-3 py-2">{str(row.dimension)}</td><td className="px-3 py-2">{statusLabel(str(row.status))}</td><td className="px-3 py-2">{row.refutation_search_done ? "已完成" : "未完成"}</td></tr> })}</tbody></table></div>; }

function SourceQuality({ payload }: { payload: Record<string, unknown> }) { const summary = record(payload.summary); return <div className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-4"><Metric label="一级来源" value={num(summary.tier1_count)} /><Metric label="二级来源" value={num(summary.tier2_count)} /><Metric label="三级线索" value={num(summary.tier3_count)} /><Metric label="不可访问" value={num(summary.inaccessible_count)} /></div>; }

function Timeline({ payload, onFocus }: { payload: Record<string, unknown>; onFocus: (target: FocusTarget) => void }) { const items = list(payload.items); if (!items.length) return <Empty label="当前研究产物未提供可排序的事件。" />; return <div className="mt-5 border-l border-gold/60 pl-5">{items.map((item, index) => { const row = record(item); return <button key={index} onClick={() => row.evidence_id && onFocus({ kind: "evidence", id: str(row.evidence_id), title: str(row.title) })} className="relative mb-5 block text-left last:mb-0"><span className="absolute -left-[25px] top-1 size-2 rounded-full border-2 border-paper bg-gold"/><div className="text-[11px] tabular text-muted-foreground">{str(row.event_date) || "日期未披露"}</div><div className="mt-1 text-sm font-medium">{str(row.title)}</div></button> })}</div>; }

function CausalGraph({ payload, onFocus }: { payload: Record<string, unknown>; onFocus: (target: FocusTarget) => void }) { const nodes = list(payload.nodes); if (!nodes.length) return <Empty label="当前范围内未形成可解释的共同证据关联。" />; return <div className="mt-5 rounded-lg border border-border bg-muted/20 p-4"><div className="flex flex-wrap gap-2">{nodes.map((node, index) => { const row = record(node); return <button key={index} onClick={() => onFocus({ kind: "hypothesis", id: str(row.hypothesis_id), title: str(row.title) })} className="rounded-md border border-primary/20 bg-paper px-3 py-2 text-left text-xs hover:border-primary/50"><div className="font-medium">{str(row.title)}</div><div className="mt-1 text-[10px] text-muted-foreground">{str(row.dimension)} · {confidenceLabel(str(row.confidence_level))}</div></button> })}</div></div>; }

function DataStrip({ payload, kind }: { payload: Record<string, unknown>; kind: string }) { const rows = list(payload.points).length ? list(payload.points) : list(payload.rows).length ? list(payload.rows) : list(payload.stages); if (!rows.length) return <Empty label="本次运行没有对应的结构化数据。" />; return <div className="mt-5 grid gap-2 sm:grid-cols-3">{rows.slice(0, 9).map((item, index) => { const row = record(item); return <div key={index} className="rounded-md border border-border bg-muted/25 px-3 py-2 text-xs"><div className="text-muted-foreground">{str(row.period) || str(row.peer) || str(row.name) || kind}</div><div className="mt-1 font-medium">{str(row.metric) || str(row.stage) || "—"} <span className="tabular">{str(row.value)}</span></div></div> })}</div>; }

function EvidenceRail({ focus, sources, annotation, supplement, busy, onAnnotation, onSupplement, onSaveAnnotation, onCreateSupplement, onResolve }: { focus: FocusTarget | null; sources: SourceCard[]; annotation: string; supplement: string; busy: boolean; onAnnotation: (value: string) => void; onSupplement: (value: string) => void; onSaveAnnotation: () => void; onCreateSupplement: () => void; onResolve: (action: "accept" | "downgrade" | "dismiss") => void }) { return <div className="h-full p-4"><div className="flex items-center gap-2"><Database className="size-4 text-primary"/><div><div className="font-serif font-semibold">证据与审阅</div><div className="text-[10px] text-muted-foreground">原始 URL · 支持与反证分开呈现</div></div></div>{focus && <div className="mt-4 rounded-lg border border-primary/15 bg-primary/[0.03] p-3"><div className="text-[10px] text-muted-foreground">当前对象</div><div className="mt-1 text-sm font-medium leading-5">{focus.title}</div>{(focus.kind === "hypothesis" || focus.kind === "finding") && <div className="mt-3 flex gap-1"><button disabled={busy} onClick={() => onResolve("accept")} className="rounded bg-risk-low/10 px-2 py-1 text-[10px] text-risk-low">采纳</button><button disabled={busy} onClick={() => onResolve("downgrade")} className="rounded bg-risk-mid/10 px-2 py-1 text-[10px] text-risk-mid">降级</button><button disabled={busy} onClick={() => onResolve("dismiss")} className="rounded bg-muted px-2 py-1 text-[10px] text-muted-foreground">排除</button></div>}</div>}<div className="mt-5"><div className="flex items-center justify-between"><div className="text-xs font-semibold">{focus ? "关联来源" : "来源卡片"}</div><span className="text-[10px] tabular text-muted-foreground">{sources.length}</span></div><div className="mt-2 space-y-2">{sources.slice(0, 12).map((source) => <SourceLink key={source.evidence_id} source={source} />)}{!sources.length && <Empty label="当前对象尚无可公开链接的来源卡片。" />}</div></div><div className="mt-6 border-t border-border pt-4"><div className="flex items-center gap-1.5 text-xs font-semibold"><MessageSquarePlus className="size-3.5 text-primary"/>批注</div><textarea value={annotation} onChange={(event) => onAnnotation(event.target.value)} disabled={!focus || busy} placeholder={focus ? "记录需复核的问题或判断依据" : "请选择报告中的风险或证据"} className="field-input mt-2 min-h-20 resize-y text-xs"/><button disabled={!focus || !annotation.trim() || busy} onClick={onSaveAnnotation} className="mt-2 inline-flex h-8 items-center gap-1 rounded-md bg-primary px-3 text-xs text-primary-foreground disabled:opacity-40"><ClipboardCheck className="size-3.5"/>保存批注</button></div><div className="mt-5 border-t border-border pt-4"><div className="flex items-center gap-1.5 text-xs font-semibold"><FlaskConical className="size-3.5 text-primary"/>请求补充调研</div><textarea value={supplement} onChange={(event) => onSupplement(event.target.value)} disabled={busy} placeholder="说明希望核实的事实、时间范围或来源类型" className="field-input mt-2 min-h-20 resize-y text-xs"/><button disabled={!supplement.trim() || busy} onClick={onCreateSupplement} className="mt-2 inline-flex h-8 items-center gap-1 rounded-md border border-primary/25 px-3 text-xs text-primary disabled:opacity-40"><Sparkles className="size-3.5"/>创建任务</button></div></div>; }

function SourceLink({ source }: { source: SourceCard }) { return <a href={source.url} target="_blank" rel="noreferrer" className="block rounded-lg border border-border p-3 transition hover:border-primary/30 hover:bg-muted/30"><div className="flex items-start justify-between gap-2"><div className="min-w-0"><div className="flex gap-1"><Tag text={tierLabel(source.tier)} tone={source.tier} />{source.accessible === false && <Tag text="不可访问" tone="inaccessible" />}</div><div className="mt-1 line-clamp-2 text-xs font-medium leading-5">{source.title || source.site_name || "未命名来源"}</div><div className="mt-1 truncate text-[10px] text-muted-foreground">{source.site_name || domainOf(source.url)}</div></div><ExternalLink className="mt-1 size-3.5 shrink-0 text-muted-foreground"/></div></a>; }

function filterSections(module: V2ReportModule, bundle: V2ReportBundle, filter: "all" | "verified" | "unresolved") { const sectionComponents = pairSections(module); if (filter === "all") return sectionComponents; const targets = new Set(filter === "verified" ? bundle.selection.verified_hypothesis_ids : bundle.selection.material_unresolved_hypothesis_ids); return sectionComponents.filter(({ section, components }) => { const ids = [...section.referenced_hypothesis_ids, ...components.flatMap((component) => component.lineage)]; return !ids.length || ids.some((id) => targets.has(id)); }); }
function pairSections(module: V2ReportModule) { let cursor = 0; return module.sections.map((section) => { const count = section.component_anchors.length; const components = module.components.slice(cursor, cursor + count); cursor += count; return { section, components }; }); }
function collectSources(components: V2ReportComponent[]) { const found = new Map<string, SourceCard>(); const visit = (value: unknown) => { if (Array.isArray(value)) { value.forEach(visit); return; } if (!value || typeof value !== "object") return; const row = value as Record<string, unknown>; if (typeof row.evidence_id === "string" && typeof row.url === "string") found.set(row.evidence_id, { evidence_id: row.evidence_id, url: row.url, title: typeof row.title === "string" ? row.title : undefined, site_name: typeof row.site_name === "string" ? row.site_name : undefined, tier: typeof row.tier === "string" ? row.tier : undefined, accessible: typeof row.accessible === "boolean" ? row.accessible : undefined }); Object.values(row).forEach(visit); }; components.forEach((component) => visit(component.payload)); return [...found.values()]; }
function sourceList(value: unknown) { return list(value).flatMap((item) => { const row = record(item); return typeof row.evidence_id === "string" && typeof row.url === "string" ? [{ evidence_id: row.evidence_id, url: row.url, title: typeof row.title === "string" ? row.title : undefined, site_name: typeof row.site_name === "string" ? row.site_name : undefined, tier: typeof row.tier === "string" ? row.tier : undefined, accessible: typeof row.accessible === "boolean" ? row.accessible : undefined }] : []; }); }
function list(value: unknown) { return Array.isArray(value) ? value : []; }
function record(value: unknown) { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function str(value: unknown) { return typeof value === "string" ? value : value == null ? "" : String(value); }
function num(value: unknown) { return typeof value === "number" ? value : 0; }
function importanceLabel(value: string) { return (({ high: "高重要性", medium: "中重要性", low: "低重要性" } as Record<string, string>)[value] ?? value) || "未标注"; }
function confidenceLabel(value: string) { return (({ high: "高置信", medium: "中置信", low: "低置信" } as Record<string, string>)[value] ?? value) || "未标注"; }
function statusLabel(value: string) { return (({ verified: "已验证", material_unresolved: "重大待核实", contested: "存在争议", partially_supported: "部分支持", refuted: "已驳回", inconclusive: "尚无定论" } as Record<string, string>)[value] ?? value) || "待核实"; }
function tierLabel(value?: string) { return ({ tier1: "一级来源", tier2: "二级来源", tier3: "三级线索", unknown: "来源待分级" } as Record<string, string>)[value ?? ""] ?? "来源待分级"; }
function domainOf(url: string) { try { return new URL(url).hostname; } catch { return url; } }
function Tag({ text, tone }: { text: string; tone?: string }) { return <span className={cn("rounded-full bg-muted px-1.5 py-0.5 text-[9px] text-muted-foreground", tone === "high" || tone === "verified" ? "bg-risk-high/10 text-risk-high" : tone === "material_unresolved" || tone === "medium" ? "bg-risk-mid/10 text-risk-mid" : tone === "tier1" ? "bg-risk-low/10 text-risk-low" : "")}>{text}</span>; }
function EvidenceCount({ icon: Icon, label, count }: { icon: typeof CheckCircle2; label: string; count: number }) { return <div className="flex items-center gap-1.5 rounded bg-muted/50 px-2 py-1.5 text-muted-foreground"><Icon className="size-3.5 text-primary"/>{label}<span className="ml-auto tabular text-foreground">{count}</span></div>; }
function Metric({ label, value }: { label: string; value: number }) { return <div className="rounded-md border border-border bg-muted/30 p-3"><div className="text-[10px] text-muted-foreground">{label}</div><div className="mt-1 text-lg font-semibold tabular">{value}</div></div>; }
function Empty({ label }: { label: string }) { return <div className="mt-3 rounded-md border border-dashed border-border bg-muted/25 px-3 py-3 text-xs text-muted-foreground">{label}</div>; }

function LegacyReportPage({ report }: { report: Awaited<ReturnType<typeof getReportData>> }) {
  const api = getRouteApi("/analyses/$id");
  const { analysis } = api.useLoaderData();
  const sections = splitMarkdown(report?.markdown ?? "报告尚未生成。");
  const [focusedEvidenceId, setFocusedEvidenceId] = useState<string | null>(null);
  const evidence = extractLegacyEvidence(report?.markdown ?? "");
  const summary = sections.find((section) => section.title.includes("执行摘要"))?.lines.join(" ") ?? "历史报告保留原始风险结论与证据索引，尚未生成可编辑的 V2 研究产物。";
  const navigator = <LegacyNavigator sections={sections} onSection={(index) => document.getElementById(`legacy-${index}`)?.scrollIntoView({ behavior: "smooth", block: "start" })} />;
  const rail = <LegacyEvidenceRail evidence={evidence} focusedEvidenceId={focusedEvidenceId} onFocus={setFocusedEvidenceId} />;

  return <div className="flex min-h-[calc(100vh-14.5rem)] flex-col bg-[#f7f8fa]">
    <header className="border-b border-border bg-paper px-4 py-3 lg:px-6">
      <div className="mx-auto flex max-w-[1800px] items-center justify-between gap-3">
        <div className="min-w-0"><div className="eyebrow text-primary/70">Historical report · compatibility view</div><div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground"><span>历史报告阅读器</span><span className="rounded-full bg-gold-soft px-2 py-0.5 text-[11px] font-medium text-primary">仅供阅读</span></div></div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Sheet><SheetTrigger asChild><button className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border bg-paper px-3 text-xs xl:hidden"><PanelLeft className="size-3.5" /> 目录</button></SheetTrigger><SheetContent side="left" className="w-[88vw] overflow-y-auto p-0"><SheetTitle className="sr-only">报告目录</SheetTitle>{navigator}</SheetContent></Sheet>
          <Sheet><SheetTrigger asChild><button className="inline-flex h-9 items-center gap-1.5 rounded-md bg-primary px-3 text-xs text-primary-foreground xl:hidden"><PanelRight className="size-3.5" /> 溯源</button></SheetTrigger><SheetContent side="right" className="w-[92vw] overflow-y-auto p-0"><SheetTitle className="sr-only">证据与溯源</SheetTitle>{rail}</SheetContent></Sheet>
        </div>
      </div>
    </header>
    <div className="mx-auto hidden w-full max-w-[1800px] flex-1 xl:block"><ResizablePanelGroup orientation="horizontal" className="h-full">
      <ResizablePanel defaultSize="18%" minSize="220px" maxSize="30%" className="border-r border-border bg-paper">{navigator}</ResizablePanel>
      <ResizableHandle withHandle />
      <ResizablePanel defaultSize="58%" minSize="480px"><main className="h-full min-w-0 overflow-y-auto"><article className="mx-auto max-w-5xl px-4 py-6 sm:px-8 lg:px-10"><LegacyReportHero analysis={analysis} report={report} summary={summary} evidenceCount={evidence.length} />{sections.map((section, index) => <LegacyReportSection key={`${section.title}-${index}`} section={section} index={index} focusedEvidenceId={focusedEvidenceId} onFocusEvidence={setFocusedEvidenceId} />)}</article></main></ResizablePanel>
      <ResizableHandle withHandle />
      <ResizablePanel defaultSize="24%" minSize="300px" maxSize="34%" className="border-l border-border bg-paper">{rail}</ResizablePanel>
    </ResizablePanelGroup></div>
    <main className="min-w-0 flex-1 xl:hidden"><article className="mx-auto max-w-5xl px-4 py-6 sm:px-8"><LegacyReportHero analysis={analysis} report={report} summary={summary} evidenceCount={evidence.length} />{sections.map((section, index) => <LegacyReportSection key={`${section.title}-${index}`} section={section} index={index} focusedEvidenceId={focusedEvidenceId} onFocusEvidence={setFocusedEvidenceId} />)}</article></main>
  </div>;
}

type LegacySection = { title: string; lines: string[] };
type LegacyEvidence = { evidence_id: string; location: string; snippet: string };

function LegacyNavigator({ sections, onSection }: { sections: LegacySection[]; onSection: (index: number) => void }) { return <div className="h-full p-4"><div className="flex items-center gap-2"><BookOpen className="size-4 text-primary" /><div className="font-serif font-semibold">报告目录</div></div><p className="mt-2 text-xs leading-5 text-muted-foreground">兼容阅读视图 · 以原始报告为准</p><nav className="mt-5 space-y-1">{sections.map((section, index) => <button key={`${section.title}-${index}`} onClick={() => onSection(index)} className="flex w-full items-start gap-2 rounded-md px-2 py-2 text-left text-sm text-muted-foreground hover:bg-muted hover:text-foreground"><span className="grid size-5 shrink-0 place-items-center rounded-full bg-muted text-[10px] tabular">{index + 1}</span><span className="leading-snug">{section.title}</span></button>)}</nav></div>; }

function LegacyReportHero({ analysis, report, summary, evidenceCount }: { analysis: { name: string; riskScore: number; riskLevel: "high" | "mid" | "low" | "unrated" }; report: Awaited<ReturnType<typeof getReportData>>; summary: string; evidenceCount: number }) {
  const radar = report?.radar ?? [];
  return <section className="mb-8 overflow-hidden rounded-xl border border-border bg-paper soft-ring"><div className="border-b border-border bg-[linear-gradient(120deg,rgba(247,241,225,.9),rgba(255,255,255,.2))] px-5 py-5 sm:px-7"><div className="flex flex-wrap items-start justify-between gap-5"><div className="max-w-2xl"><div className="eyebrow text-primary/60">Legacy analysis dossier</div><h1 className="mt-2 font-serif text-2xl font-semibold sm:text-3xl">{analysis.name} · IPO 风险分析报告</h1><p className="mt-3 text-sm leading-6 text-muted-foreground">该任务尚未生成 V2 研究产物。以下内容保留历史结论，同时提供目录导航、风险画像与证据溯源。</p></div><RiskBadge level={analysis.riskLevel} /></div></div><div className="grid gap-5 p-5 sm:p-7 lg:grid-cols-[minmax(0,1fr)_240px]"><div><div className="text-sm font-semibold">执行摘要</div><p className="mt-2 line-clamp-4 text-sm leading-6 text-muted-foreground">{summary}</p><div className="mt-5 grid grid-cols-3 gap-2"><Metric label="风险评分" value={analysis.riskScore} /><Metric label="证据索引" value={evidenceCount} /><Metric label="报告状态" value={report?.reviewStatus === "approved" ? 1 : 0} /></div></div><div className="rounded-lg border border-border bg-muted/20 p-4"><div className="flex items-center gap-2 text-sm font-semibold"><Database className="size-4 text-primary" />风险画像</div><div className="mt-4 space-y-3">{radar.map((item) => <div key={item.dim}><div className="mb-1 flex justify-between text-[10px] text-muted-foreground"><span>{item.dim}</span><span className="tabular">{item.score}</span></div><div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary/75" style={{ width: `${item.score}%` }} /></div></div>)}</div></div></div></section>;
}

function LegacyReportSection({ section, index, focusedEvidenceId, onFocusEvidence }: { section: LegacySection; index: number; focusedEvidenceId: string | null; onFocusEvidence: (id: string) => void }) { const sectionEvidence = extractEvidenceIds(section.lines.join("\n")); return <section id={`legacy-${index}`} className="scroll-mt-6 border-b border-border py-8 last:border-b-0"><div className="mb-4 flex items-start justify-between gap-4"><div><div className="eyebrow text-primary/55">Section {String(index + 1).padStart(2, "0")}</div><h2 className="mt-1 font-serif text-2xl font-semibold">{section.title}</h2></div>{sectionEvidence.length > 0 && <span className="rounded-full bg-primary/8 px-2.5 py-1 text-[11px] text-primary">{sectionEvidence.length} 条引用</span>}</div><MarkdownBody markdown={section.lines.join("\n")} />{sectionEvidence.length > 0 && <div className="mt-5 flex flex-wrap gap-2 border-t border-border pt-4">{sectionEvidence.map((id) => <button key={id} onClick={() => onFocusEvidence(id)} className={cn("rounded-full border px-2.5 py-1 text-[11px] transition", focusedEvidenceId === id ? "border-primary bg-primary text-primary-foreground" : "border-border bg-paper text-muted-foreground hover:border-primary/50 hover:text-primary")}>证据 {id}</button>)}</div>}</section>; }

function LegacyEvidenceRail({ evidence, focusedEvidenceId, onFocus }: { evidence: LegacyEvidence[]; focusedEvidenceId: string | null; onFocus: (id: string) => void }) { return <aside className="h-full p-4"><div className="flex items-center gap-2"><SearchCheck className="size-4 text-primary" /><div className="font-serif font-semibold">证据与溯源</div></div><p className="mt-2 text-xs leading-5 text-muted-foreground">历史报告引用 · 点击报告中的证据标签定位</p><div className="mt-5 space-y-3">{evidence.length ? evidence.map((item) => <button key={item.evidence_id} onClick={() => onFocus(item.evidence_id)} className={cn("w-full rounded-lg border p-3 text-left transition", focusedEvidenceId === item.evidence_id ? "border-primary bg-primary/5" : "border-border bg-paper hover:border-primary/40")}><div className="flex items-center justify-between gap-2"><span className="font-mono text-[11px] text-primary">{item.evidence_id}</span><Tag text="PDF 原文" tone="tier1" /></div><div className="mt-2 text-xs font-medium text-foreground">{item.location}</div><p className="mt-1 line-clamp-4 text-xs leading-5 text-muted-foreground">{item.snippet}</p></button>) : <Empty label="历史报告未包含可结构化的证据索引。" />}</div><div className="mt-6 rounded-lg border border-dashed border-border bg-muted/20 p-3 text-xs leading-5 text-muted-foreground">V2 报告生成后，将在此处显示原始 URL、来源分级、支持与反证关系及审阅批注。</div></aside>; }

function extractEvidenceIds(value: string) { return [...new Set([...value.matchAll(/\bev_[a-zA-Z0-9_]+\b/g)].map((match) => match[0]))]; }
function extractLegacyEvidence(markdown: string): LegacyEvidence[] { return markdown.split("\n").flatMap((line) => { const match = line.match(/^\s*-\s*(ev_[a-zA-Z0-9_]+)：\s*([^/]+)\/\s*([^/]+)\/[^/]*\/\s*(.*)$/); return match ? [{ evidence_id: match[1], location: `${match[2].trim()} · ${match[3].trim()}`, snippet: match[4].trim() }] : []; }); }

function splitMarkdown(markdown: string): LegacySection[] { const sections: LegacySection[] = []; let current: LegacySection | null = null; markdown.split("\n").forEach((line) => { if (line.startsWith("# ") || line.startsWith("## ")) { current = { title: line.replace(/^#{1,2}\s+/, ""), lines: [] }; sections.push(current); } else { if (!current) { current = { title: "报告", lines: [] }; sections.push(current); } current.lines.push(line); } }); return sections; }
