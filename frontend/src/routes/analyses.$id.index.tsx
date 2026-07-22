import { createFileRoute, getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  CheckCircle2,
  Loader2,
  Circle,
  FileText,
  ExternalLink,
  Search,
  Wand2,
  AlertCircle,
  ArrowLeft,
  Database,
  PauseCircle,
  Users,
} from "lucide-react";
import {
  getAgents,
  getAnalysisProgress,
  getAnalysisRun,
  getTraceData,
  getEvidenceFeed,
  startAnalysisRun,
  streamAnalysisRun,
  type AnalysisRun,
  type AnalysisRunEvent,
  type AnalysisProgress,
} from "@/lib/data-service";
import { type Agent } from "@/lib/types";
import { PdfViewer } from "@/components/pdf-viewer";
import { motion, AnimatePresence } from "framer-motion";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

export const Route = createFileRoute("/analyses/$id/")({
  loader: async ({ params }) => {
    const [agentsList, run, trace, feed] = await Promise.all([
      getAgents(),
      getAnalysisRun(params.id),
      getTraceData(params.id),
      getEvidenceFeed(params.id),
    ]);
    return { agents: agentsList, run, trace, evidenceFeed: feed };
  },
  component: RealtimeStream,
  head: () => ({ meta: [{ title: "实时协作思维流 · IPO RiskAgent" }] }),
});

type TraceView = {
  stages: { key: string; label: string; owner: string; status: string; cost?: string | null }[];
  seed: { who: string; kind: string; text: string; t: string }[];
  flow: { who: string; kind: string; text: string; t: string }[];
  evidenceFeed: { id: string; source: string; title: string; conf: number; time: string }[];
} | null;

type EvidenceFeedItem = {
  id: string;
  source: string;
  title: string;
  conf: number;
  time: string;
  page?: number | null;
  snippet?: string | null;
  document_id?: string | null;
  bbox?: number[];
};
type Bubble = { who: string; kind: string; text: string; t: string };

const kindLabel: Record<string, string> = {
  execute: "执行",
  discover: "发现",
  challenge: "质疑",
  verdict: "裁定",
  assign: "指派",
};
const kindClass: Record<string, string> = {
  execute: "bg-primary/10 text-primary",
  discover: "bg-[color:var(--risk-low)]/15 text-[color:var(--risk-low)]",
  challenge: "bg-[color:var(--risk-mid)]/15 text-[oklch(0.45_0.12_75)]",
  verdict: "bg-[color:var(--risk-high)]/15 text-[color:var(--risk-high)]",
  assign: "bg-[color:var(--gold)]/15 text-[color:var(--navy)]",
};

const terminalStatuses = new Set(["completed", "failed"]);
const pausedStatuses = new Set(["awaiting_expert_selection"]);

function RealtimeStream() {
  const api = getRouteApi("/analyses/$id");
  const { analysis } = api.useLoaderData();
  const { id } = api.useParams();
  const navigate = useNavigate();
  const {
    agents: agentsList,
    run: initialRun,
    trace: initialTrace,
    evidenceFeed: initialEvidenceFeed,
  } = Route.useLoaderData() as {
    agents: Agent[];
    run: AnalysisRun;
    trace: TraceView;
    evidenceFeed: EvidenceFeedItem[];
  };

  const [run, setRun] = useState<AnalysisRun>(initialRun);
  const [trace, setTrace] = useState<TraceView>(initialTrace);
  const [evidenceFeed, setEvidenceFeed] = useState<EvidenceFeedItem[]>(initialEvidenceFeed);
  const [liveEvents, setLiveEvents] = useState<AnalysisRunEvent[]>(initialRun.events ?? []);
  const [progressSnapshot, setProgressSnapshot] = useState<AnalysisProgress | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [filter, setFilter] = useState("");
  const [viewerEvidence, setViewerEvidence] = useState<EvidenceFeedItem | null>(null);
  const [reportReadyOpen, setReportReadyOpen] = useState(false);
  const startedRunRef = useRef<string | null>(null);
  const liveEventsRef = useRef<AnalysisRunEvent[]>(initialRun.events ?? []);
  const durableCursorRef = useRef(initialRun.events?.length ?? 0);
  const pendingEventsRef = useRef<AnalysisRunEvent[]>([]);
  const animationFrameRef = useRef<number | null>(null);
  const completionPromptShownRef = useRef(initialRun.status === "completed");

  const queueLiveEvent = useCallback((event: AnalysisRunEvent) => {
    pendingEventsRef.current.push(event);
    if (animationFrameRef.current !== null) return;
    animationFrameRef.current = window.requestAnimationFrame(() => {
      const pending = pendingEventsRef.current.splice(0);
      animationFrameRef.current = null;
      setLiveEvents((current) => {
        const next = pending.reduce(mergeLiveEvent, current);
        liveEventsRef.current = next;
        return next;
      });
    });
  }, []);

  const refreshTrace = useCallback(async () => {
    const [freshRun, freshTrace, freshEvidence] = await Promise.all([
      getAnalysisRun(id),
      getTraceData(id),
      getEvidenceFeed(id),
    ]);
    setRun(freshRun);
    setTrace(freshTrace);
    setEvidenceFeed(freshEvidence);
    setLiveEvents(freshRun.events ?? []);
    liveEventsRef.current = freshRun.events ?? [];
    durableCursorRef.current = freshRun.events?.length ?? 0;
  }, [id]);

  useEffect(
    () => () => {
      if (animationFrameRef.current !== null) {
        window.cancelAnimationFrame(animationFrameRef.current);
      }
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;
    let startPoll: ReturnType<typeof setInterval> | undefined;
    async function boot() {
      await Promise.resolve();
      if (cancelled || startedRunRef.current === id) return;
      startedRunRef.current = id;
      let activeRun = run;
      if (run.status === "ready") {
        setStarting(true);
        startPoll = setInterval(() => {
          if (!cancelled) void refreshTrace().catch(() => undefined);
        }, 2000);
        try {
          activeRun = await startAnalysisRun(id);
          if (cancelled) return;
          setRun(activeRun);
          setLiveEvents(activeRun.events ?? []);
        } catch (err) {
          if (!cancelled) setStreamError(err instanceof Error ? err.message : "启动分析失败");
          return;
        } finally {
          if (startPoll) clearInterval(startPoll);
          if (!cancelled) setStarting(false);
        }
      }

      if (terminalStatuses.has(activeRun.status) || pausedStatuses.has(activeRun.status)) {
        await refreshTrace().catch(() => undefined);
        return;
      }

      const controller = new AbortController();
      let retryTimer: number | undefined;
      const connect = () => {
        streamAnalysisRun(
          id,
          (event) => {
            if (cancelled) return;
            if (
              event.event_id &&
              !liveEventsRef.current.some((item) => item.event_id === event.event_id)
            ) {
              durableCursorRef.current += 1;
            }
            queueLiveEvent(event);
          },
          (finalRun) => {
            if (cancelled) return;
            setRun(finalRun);
            setLiveEvents(finalRun.events ?? []);
            liveEventsRef.current = finalRun.events ?? [];
            durableCursorRef.current = finalRun.events?.length ?? 0;
            void refreshTrace();
          },
          { signal: controller.signal, after: durableCursorRef.current },
        ).catch((err: unknown) => {
          if (err instanceof DOMException && err.name === "AbortError") return;
          if (cancelled) return;
          setStreamError(err instanceof Error ? err.message : "实时流连接失败，正在重连");
          retryTimer = window.setTimeout(connect, 1000);
        });
      };
      connect();
      return () => {
        controller.abort();
        if (retryTimer) window.clearTimeout(retryTimer);
      };
    }

    let cleanup: (() => void) | undefined;
    boot().then((result) => {
      cleanup = result;
    });
    return () => {
      cancelled = true;
      if (startPoll) clearInterval(startPoll);
      cleanup?.();
    };
    // run.status is intentionally not a dependency; the page should boot once per run id.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, queueLiveEvent, refreshTrace]);

  useEffect(() => {
    let cancelled = false;
    const refreshProgress = async () => {
      try {
        const next = await getAnalysisProgress(id);
        if (!cancelled) {
          setProgressSnapshot(next);
          setRun((current) => ({
            ...current,
            status: next.status,
            current_stage: next.current_stage,
            error_summary: next.error_summary ?? current.error_summary,
          }));
        }
      } catch (err) {
        if (!cancelled) setStreamError(err instanceof Error ? err.message : "进度同步失败");
      }
    };
    void refreshProgress();
    if (terminalStatuses.has(run.status) || pausedStatuses.has(run.status)) {
      return () => {
        cancelled = true;
      };
    }
    const interval = window.setInterval(() => void refreshProgress(), 1000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [id, run.status]);

  useEffect(() => {
    if (run.status !== "completed" || completionPromptShownRef.current) return;
    completionPromptShownRef.current = true;
    setReportReadyOpen(true);
  }, [run.status]);

  const stages = trace?.stages?.length ? trace.stages : fallbackStages(run);
  const eventBubbles = useMemo(() => {
    const reportEvents = liveEvents.filter(isExecutionReportEvent);
    const eventFlow = reportEvents.map((event, index) =>
      eventToBubble(event, run.created_at, index),
    );
    const legacyReports = reportEvents.some((event) => event.event_type === "agent_reported")
      ? []
      : moduleResultBubbles(run);
    return mergeBubbles(legacyReports, eventFlow);
  }, [liveEvents, run]);
  const liveEvidenceFeed = useMemo(
    () => mergeEvidenceFeed(evidenceFeed, liveEvents),
    [evidenceFeed, liveEvents],
  );
  const formalEvidenceCount = liveEvidenceFeed.filter(
    (item) => item.scoreKind === "relevance",
  ).length;
  const retrievalCandidateCount = liveEvidenceFeed.filter(
    (item) => item.scoreKind === "retrieval",
  ).length;
  const filteredLiveEvidence = liveEvidenceFeed.filter((item) =>
    `${displayAgentName(agentsList, item.source)} ${item.title} ${item.id} ${item.snippet ?? ""}`
      .toLowerCase()
      .includes(filter.toLowerCase()),
  );
  const completedStages = stages.filter((stage) => stage.status === "done").length;
  const progress = stages.length ? Math.round((completedStages / stages.length) * 100) : 0;
  const selectedAgentNames = new Set([
    ...(run.strategy_plan?.assignments?.map((assignment) => assignment.agent_name) ?? []),
    ...(run.team_state?.map((member) => member.agent_name) ?? []),
    ...(run.analysis_config.selected_agents?.map((agent) => agent.agent_name) ?? []),
  ]);
  const selectedAgentMeta = agentsList.filter(
    (agent) =>
      selectedAgentNames.has(agent.id) ||
      selectedAgentNames.has(agent.name) ||
      Boolean(agent.runtimeName && selectedAgentNames.has(agent.runtimeName)),
  );
  const visibleAgents = selectedAgentMeta.length
    ? selectedAgentMeta
    : agentsList.slice(0, Math.max(analysis.experts, 6));
  const activeAgentCount =
    new Set([
      ...selectedAgentNames,
      ...liveEvents
        .filter((event) => ["agent_selected", "rules_assigned"].includes(event.event_type))
        .map((event) => event.actor_name)
        .filter(Boolean),
    ]).size || visibleAgents.length;
  const pdfProgress = progressSnapshot?.pdf;
  const indexProgress = progressSnapshot?.index;
  const collaborationAgents = progressSnapshot?.agents ?? [];
  const pdfPercent = pdfProgress?.total_pages
    ? Math.round((pdfProgress.parsed_pages / pdfProgress.total_pages) * 100)
    : 0;
  const indexPercent = indexProgress?.total_blocks
    ? Math.round((indexProgress.indexed_blocks / indexProgress.total_blocks) * 100)
    : 0;

  return (
    <div className="flex h-[calc(100vh-3.5rem)] min-h-[680px] flex-col bg-background">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-card/80 px-5 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <a
            href="/"
            className="grid size-8 shrink-0 place-items-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="返回工作台"
          >
            <ArrowLeft className="size-4" />
          </a>
          <div className="grid size-10 shrink-0 place-items-center rounded-sm bg-[color:var(--gold)]/15 text-[color:var(--navy)]">
            <Wand2 className="size-5" />
          </div>
          <div className="min-w-0">
            <h1 className="truncate text-sm font-semibold text-[color:var(--navy)]">
              {run.company_name || analysis.name} · IPO 上市风险审核
            </h1>
            <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
              任务 {run.run_id} · {run.status} · 数据截止 {run.data_cutoff_date}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
          {streamError ? (
            <span className="inline-flex items-center gap-1 rounded-sm bg-destructive/10 px-2 py-1 text-destructive">
              <AlertCircle className="size-3" /> 实时流断开：{streamError}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1">
              <span className="size-1.5 animate-pulse rounded-full bg-[color:var(--gold)]" />
              {starting
                ? "正在启动 Agent 编队"
                : pausedStatuses.has(run.status)
                  ? "等待人工选择专家"
                  : terminalStatuses.has(run.status)
                    ? "分析已结束"
                    : "实时同步中"}
            </span>
          )}
          <span className="inline-flex items-center gap-1">
            <FileText className="size-3.5" /> {formalEvidenceCount} 条正式证据 ·{" "}
            {retrievalCandidateCount} 条检索候选
          </span>
          <span className="inline-flex items-center gap-1">
            <Users className="size-3.5" /> {activeAgentCount || visibleAgents.length} 位专家
          </span>
          <Link
            to="/analyses/$id/orchestra"
            params={{ id }}
            className="inline-flex items-center gap-1 rounded-sm border border-border bg-background px-2 py-1 text-foreground hover:bg-muted"
          >
            <Activity className="size-3.5 text-primary" /> 动态编排
          </Link>
          <div className="flex items-center gap-2">
            <div className="h-1.5 w-28 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-[color:var(--gold)]"
                style={{ width: `${progress}%` }}
              />
            </div>
            <span className="tabular">{progress}%</span>
          </div>
        </div>
      </header>

      {pausedStatuses.has(run.status) ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-amber-500/30 bg-amber-500/8 px-5 py-3 text-xs">
          <span className="inline-flex items-center gap-2 text-amber-800">
            <PauseCircle className="size-4" />
            {run.interrupt_reason ?? "战略层专家名单需要人工确认，实时流已安全暂停。"}
          </span>
          <Link
            to="/analyses/$id/orchestra"
            params={{ id }}
            className="rounded-full bg-primary px-3 py-1.5 font-medium text-primary-foreground hover:bg-primary/90"
          >
            选择专家并恢复
          </Link>
        </div>
      ) : null}

      <div className="grid min-h-0 flex-1 grid-cols-[260px_minmax(0,1fr)_360px] overflow-hidden max-xl:grid-cols-[220px_minmax(0,1fr)_320px] max-lg:grid-cols-1">
        <aside className="overflow-y-auto border-r border-border bg-card/50 px-5 py-6 max-lg:hidden">
          <div className="flex items-center gap-2 text-xs font-medium">
            <Activity className="size-3.5 text-primary" /> 任务流水线
          </div>
          <ol className="mt-4 space-y-1">
            {stages.map((stage, index) => (
              <li key={stage.key} className="relative pl-6">
                <span className="absolute left-0 top-1.5">
                  {stage.status === "done" && (
                    <CheckCircle2 className="size-4 text-[color:var(--risk-low)]" />
                  )}
                  {stage.status === "running" && (
                    <Loader2 className="size-4 animate-spin text-primary" />
                  )}
                  {stage.status === "queued" && (
                    <Circle className="size-4 text-muted-foreground/40" />
                  )}
                </span>
                <div className="rounded-md px-2 py-1 text-xs">
                  <div className="font-medium leading-snug">{stage.label}</div>
                  <div className="text-[11px] text-muted-foreground">
                    {displayAgentName(agentsList, stage.owner)}
                  </div>
                </div>
                {index < stages.length - 1 && (
                  <span className="absolute left-2 top-7 h-6 w-px bg-border" />
                )}
              </li>
            ))}
          </ol>

          <div className="mt-8 rounded-lg border border-border bg-background p-3">
            <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
              <FileText className="size-3.5" /> PDF 材料处理
            </div>
            <ProgressLine
              label="原文解析"
              value={pdfPercent}
              detail={
                pdfProgress
                  ? `第 ${pdfProgress.parsed_pages} / ${pdfProgress.total_pages} 页 · ${pdfProgress.parsed_blocks} 个原文块`
                  : "等待解析任务启动"
              }
              failed={pdfProgress?.status === "failed"}
            />
            <ProgressLine
              label="材料索引"
              value={indexPercent}
              detail={
                indexProgress
                  ? `${indexProgress.indexed_blocks} / ${indexProgress.total_blocks} 个原文块`
                  : "等待原文解析完成"
              }
            />
            {pdfProgress?.error ? (
              <p className="mt-2 text-[11px] leading-relaxed text-destructive">
                {pdfProgress.error}
              </p>
            ) : null}
          </div>

          <div className="mt-4 rounded-lg border border-border bg-background p-3">
            <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
              <Users className="size-3.5" /> 专家协作（{collaborationAgents.length}）
            </div>
            <div className="mt-3 space-y-2 text-[11px]">
              {collaborationAgents.length ? (
                collaborationAgents.map((agent) => (
                  <AgentProgressCard key={agent.agent_name} agent={agent} agents={agentsList} />
                ))
              ) : (
                <p className="text-muted-foreground">战略层尚未完成专家编排。</p>
              )}
            </div>
          </div>
        </aside>

        <section className="flex min-w-0 flex-col overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-card/40 px-6 py-3 text-xs">
            <span className="inline-flex items-center gap-1.5 font-medium">
              <Wand2 className="size-3.5 text-primary" /> 专家分析汇报
            </span>
            <span className="text-muted-foreground">
              已收到 <span className="tabular text-foreground">{eventBubbles.length}</span>{" "}
              份专家汇报
            </span>
          </div>
          <div className="flex-1 overflow-y-auto px-6 py-5">
            <AnimatePresence initial={false}>
              {eventBubbles.map((bubble, index) => {
                const agent = agentForBubble(agentsList, bubble);
                const kind = kindLabel[bubble.kind] ? bubble.kind : "assign";
                return (
                  <motion.div
                    key={`${bubble.t}-${index}-${bubble.text}`}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="mb-4 flex gap-3"
                  >
                    <div
                      style={{ background: agent?.accent ?? "#0b243d" }}
                      className="grid size-8 shrink-0 place-items-center rounded-full text-xs font-medium text-white"
                    >
                      {agent?.avatar ?? bubble.who[0] ?? "A"}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2 text-[11px]">
                        <span
                          className={`rounded-full px-1.5 py-0.5 font-medium ${kindClass[kind]}`}
                        >
                          {kindLabel[kind]}
                        </span>
                        <span className="font-medium">
                          {agent?.name ?? displayAgentName(agentsList, bubble.who)}
                        </span>
                        <span className="text-muted-foreground">{agent?.role ?? "Agent 专家"}</span>
                        <span className="ml-auto text-muted-foreground tabular">+{bubble.t}</span>
                      </div>
                      <div className="mt-1 rounded-xl rounded-tl-sm border border-border bg-card px-3 py-2 text-sm leading-relaxed shadow-sm">
                        {displayAgentText(agentsList, bubble.text)}
                      </div>
                    </div>
                  </motion.div>
                );
              })}
              {!terminalStatuses.has(run.status) && !pausedStatuses.has(run.status) ? (
                <motion.div
                  animate={{ opacity: [0.4, 1, 0.4] }}
                  transition={{ duration: 1.4, repeat: Infinity }}
                  className="inline-flex items-center gap-2 text-xs text-muted-foreground"
                >
                  <span className="size-1.5 rounded-full bg-primary" />
                  {collaborationStatusText(progressSnapshot, liveEvents, agentsList)}
                </motion.div>
              ) : null}
              {!eventBubbles.length && terminalStatuses.has(run.status) ? (
                <div className="mt-12 text-center text-xs text-muted-foreground">
                  本次执行未保存专家模块汇报。
                </div>
              ) : null}
            </AnimatePresence>
          </div>
        </section>

        <aside className="overflow-y-auto border-l border-border bg-card/50 px-5 py-6 max-lg:hidden">
          <div className="flex items-center justify-between text-xs font-medium">
            <span className="flex items-center gap-2">
              <Database className="size-3.5 text-primary" /> 原文与证据（实时）
            </span>
            <span className="tabular text-muted-foreground">{filteredLiveEvidence.length}</span>
          </div>
          <div className="relative mt-3">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="过滤证据"
              className="h-8 w-full rounded-md border border-border bg-background pl-8 pr-3 text-xs focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div className="mt-4 space-y-2.5">
            {filteredLiveEvidence.map((item) => (
              <div key={item.id} className="rounded-lg border border-border bg-background p-3">
                <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                  <span
                    className={`rounded px-1.5 py-0.5 font-medium ${
                      item.source === "官方"
                        ? "bg-primary/10 text-primary"
                        : "bg-muted text-muted-foreground"
                    }`}
                  >
                    {displayAgentName(agentsList, item.source)}
                  </span>
                  <span className="tabular">{item.id}</span>
                  <span>{item.time}</span>
                  <span className="ml-auto tabular">
                    {item.scoreKind === "retrieval" ? "检索分" : "相关性"}{" "}
                    {Number.isFinite(item.conf) ? Math.round(item.conf * 100) : "待计算"}
                  </span>
                </div>
                <div className="mt-1.5 text-xs font-medium leading-snug">{item.title}</div>
                {item.snippet ? (
                  <p className="mt-1.5 line-clamp-3 text-[11px] leading-relaxed text-muted-foreground">
                    {item.snippet}
                  </p>
                ) : null}
                <button
                  onClick={() => setViewerEvidence(item)}
                  disabled={!item.document_id}
                  className="mt-2 inline-flex items-center gap-1 text-[11px] text-primary hover:underline disabled:cursor-not-allowed disabled:text-muted-foreground disabled:no-underline"
                >
                  查看原文 <ExternalLink className="size-3" />
                </button>
              </div>
            ))}
            {!filteredLiveEvidence.length ? (
              <div className="mt-16 text-center text-xs text-muted-foreground">
                <FileText className="mx-auto mb-2 size-8 opacity-40" />
                {filter
                  ? "没有匹配当前筛选条件的证据。"
                  : terminalStatuses.has(run.status)
                    ? "本次分析未形成通过证据门禁的证据，请查看报告局限与检索事件。"
                    : "证据采集中，稍候片刻……"}
              </div>
            ) : null}
          </div>
        </aside>
      </div>
      {viewerEvidence?.document_id ? (
        <PdfViewer
          open={Boolean(viewerEvidence)}
          onOpenChange={(next) => {
            if (!next) setViewerEvidence(null);
          }}
          documentId={viewerEvidence.document_id}
          page={viewerEvidence.page ?? 1}
          highlight={
            viewerEvidence.bbox?.length === 4 && viewerEvidence.page
              ? {
                  page: viewerEvidence.page,
                  bbox: viewerEvidence.bbox,
                  label: viewerEvidence.title,
                }
              : null
          }
          title={`证据 ${viewerEvidence.id} · ${viewerEvidence.title}`}
        />
      ) : null}
      <AlertDialog open={reportReadyOpen} onOpenChange={setReportReadyOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>分析报告已生成</AlertDialogTitle>
            <AlertDialogDescription>
              {run.company_name || analysis.name}{" "}
              的专家分析与证据校验已完成，是否现在进入报告查看页面？
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>留在当前页</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => navigate({ to: "/analyses/$id/report", params: { id } })}
            >
              查看报告
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function ProgressLine({
  label,
  value,
  detail,
  failed = false,
}: {
  label: string;
  value: number;
  detail: string;
  failed?: boolean;
}) {
  return (
    <div className="mt-3">
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span>{label}</span>
        <span className={failed ? "text-destructive" : "tabular text-muted-foreground"}>
          {value}%
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className={failed ? "h-full bg-destructive" : "h-full bg-[color:var(--gold)]"}
          style={{ width: `${value}%` }}
        />
      </div>
      <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">{detail}</p>
    </div>
  );
}

function AgentProgressCard({
  agent,
  agents,
}: {
  agent: AnalysisProgress["agents"][number];
  agents: Agent[];
}) {
  const profile = agents.find(
    (item) =>
      item.id === agent.agent_name ||
      item.name === agent.agent_name ||
      item.runtimeName === agent.agent_name,
  );
  return (
    <div className="rounded-md border border-border/80 p-2">
      <div className="flex items-center gap-2">
        <span
          style={{ background: profile?.accent ?? "#0b243d" }}
          className="grid size-5 shrink-0 place-items-center rounded-full text-[9px] font-medium text-white"
        >
          {profile?.avatar ?? agent.agent_name[0]}
        </span>
        <span className="min-w-0 flex-1 truncate font-medium">
          {profile?.name ?? agent.agent_name}
        </span>
        <span className={`rounded px-1 py-0.5 ${agentStatusClass(agent.status)}`}>
          {agentStatusLabel(agent.status)}
        </span>
      </div>
      <p className="mt-1 truncate text-muted-foreground">{agent.module || agent.latest_action}</p>
      <p className="mt-0.5 line-clamp-2 text-[10px] leading-relaxed text-muted-foreground">
        {displayAgentText(agents, agent.latest_summary)}
      </p>
    </div>
  );
}

function agentStatusLabel(status: string) {
  return (
    {
      queued: "待编排",
      initializing: "初始化",
      retrieving_evidence: "检索证据",
      analyzing: "分析中",
      debating: "辩论/修订",
      completed: "已完成",
      failed: "失败",
    }[status] ?? status
  );
}

function agentStatusClass(status: string) {
  if (status === "completed") return "bg-[color:var(--risk-low)]/15 text-[color:var(--risk-low)]";
  if (status === "failed") return "bg-destructive/10 text-destructive";
  if (status === "queued") return "bg-muted text-muted-foreground";
  return "bg-primary/10 text-primary";
}

function collaborationStatusText(
  snapshot: AnalysisProgress | null,
  events: AnalysisRunEvent[],
  agents: Agent[],
) {
  const active = snapshot?.agents.find(
    (agent) => !["completed", "failed", "queued"].includes(agent.status),
  );
  if (active) return `${displayAgentName(agents, active.agent_name)}正在分析，等待提交汇报`;
  const latestReport = [...events].reverse().find(isExecutionReportEvent);
  return latestReport
    ? `${displayAgentName(agents, latestReport.actor_name)}已提交最新汇报`
    : "等待首位专家提交分析汇报";
}

function fallbackStages(run: AnalysisRun) {
  const lifecycle = [
    ["task_understanding", "任务理解", "战略层 Agent"],
    ["parsing", "PDF 解析", "解析服务"],
    ["indexing", "检索索引", "检索服务"],
    ["orchestra_planning", "动态规划", "战略层 Agent"],
  ];
  const tail = [
    ["verifying", "证据门禁", "Verifier"],
    ["synthesizing", "战略整合", "战略层 Agent"],
    ["completed", "交付完成", "analysis_run"],
  ];
  const dynamicAssignments = (run.strategy_plan?.assignments ?? []).map((assignment) => [
    assignment.module_id,
    assignment.module,
    assignment.agent_name,
  ]);
  const order = [...lifecycle, ...dynamicAssignments, ...tail];
  const pipelineOrder = [
    "task_understanding",
    "parsing",
    "indexing",
    "orchestra_planning",
    "spawning_experts",
    "agent_running",
    "verifying",
    "synthesizing",
    "completed",
  ];
  const currentPipelineIndex = pipelineOrder.indexOf(run.current_stage);
  return order.map(([key, label, owner]) => {
    const assignment = run.strategy_plan?.assignments?.find((item) => item.module_id === key);
    if (assignment) {
      const member = run.team_state?.find((item) => item.agent_name === assignment.agent_name);
      const state = member?.status.toLowerCase() ?? "";
      const done =
        state.includes("complete") ||
        state.includes("done") ||
        ["verifying", "synthesizing", "completed"].includes(run.current_stage);
      const running =
        state.includes("run") || state.includes("work") || Boolean(member?.current_module);
      return { key, label, owner, status: done ? "done" : running ? "running" : "queued" };
    }
    const stageIndex = pipelineOrder.indexOf(key);
    return {
      key,
      label,
      owner,
      status:
        run.status === "completed" ||
        (currentPipelineIndex >= 0 && stageIndex >= 0 && stageIndex < currentPipelineIndex)
          ? "done"
          : run.current_stage === key
            ? "running"
            : "queued",
    };
  });
}

function mergeEvidenceFeed(
  seed: EvidenceFeedItem[],
  events: AnalysisRunEvent[],
): EvidenceFeedItem[] {
  const admittedFromEvents = events
    .filter((event) => event.event_type === "evidence_found")
    .map((event, index) => {
      const payload = event.payload ?? {};
      return {
        id: String(payload.evidence_id ?? `live-${index}`),
        source: event.actor_name,
        title: event.title.replace("发现证据：", ""),
        conf: Number(payload.relevance_score ?? payload.retrieval_score ?? Number.NaN),
        time: event.created_at ? formatClock(event.created_at) : "近实时",
        page: Number(payload.page_number ?? 0),
        snippet: event.summary,
        document_id: payload.document_id ? String(payload.document_id) : null,
        bbox: Array.isArray(payload.bbox) ? (payload.bbox as number[]) : undefined,
        scoreKind: "relevance" as const,
      };
    });
  const candidatesFromEvents = events.flatMap((event) => {
    if (event.event_type !== "retrieval_query_completed") return [];
    const chunks = Array.isArray(event.payload?.chunks) ? event.payload.chunks : [];
    return chunks.flatMap((rawChunk) => {
      if (!rawChunk || typeof rawChunk !== "object") return [];
      const chunk = rawChunk as Record<string, unknown>;
      const chunkId = String(chunk.chunk_id ?? "");
      if (!chunkId) return [];
      return [
        {
          id: `candidate-${chunkId}`,
          source: event.actor_name,
          title: `候选原文：${String(chunk.section_path ?? "招股书")}`,
          conf: Number(chunk.retrieval_score ?? Number.NaN),
          time: event.created_at ? formatClock(event.created_at) : "近实时",
          page: Number(chunk.page_number ?? 0),
          snippet: String(chunk.text ?? ""),
          document_id: chunk.document_id ? String(chunk.document_id) : null,
          bbox: Array.isArray(chunk.bbox) ? (chunk.bbox as number[]) : undefined,
          scoreKind: "retrieval" as const,
        },
      ];
    });
  });
  const byId = new Map<string, EvidenceFeedItem>();
  [...candidatesFromEvents, ...seed, ...admittedFromEvents].forEach((item) => {
    const locationKey = [item.document_id ?? "", item.page ?? "", item.bbox?.join(",") ?? ""].join(
      ":",
    );
    byId.set(locationKey || item.id, {
      ...item,
      scoreKind: item.scoreKind ?? "relevance",
    });
  });
  return [...byId.values()];
}

function formatClock(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value.match(/T(\d{2}:\d{2}:\d{2})/)?.[1] ?? value;
  }
  const hkt = new Date(date.getTime() + 8 * 60 * 60 * 1000);
  return hkt.toISOString().slice(11, 19);
}

function eventToBubble(event: AnalysisRunEvent, runCreatedAt: string, index: number): Bubble {
  const modelMeta = event.model
    ? `（${event.model}${event.duration_ms ? ` · ${event.duration_ms}ms` : ""}）`
    : "";
  return {
    who: event.actor_name || "analysis_run",
    kind: eventKind(event.event_type),
    text: event.summary
      ? `${event.title}${modelMeta}：${event.raw_response || event.summary}`
      : event.title,
    t: relativeTime(runCreatedAt, event.created_at, index),
  };
}

function isExecutionReportEvent(event: AnalysisRunEvent) {
  return event.event_type === "agent_reported";
}

function moduleResultBubbles(run: AnalysisRun): Bubble[] {
  return (run.module_results ?? []).flatMap((module, index) => {
    const summary = String(module.summary ?? "").trim();
    if (!summary) return [];
    return [
      {
        who: String(module.agent_name ?? "Agent 专家"),
        kind: "discover",
        text: `${String(module.module ?? "分析模块")}汇报：${summary}`,
        t: relativeTime(run.created_at, run.completed_at ?? undefined, index),
      },
    ];
  });
}

function mergeLiveEvent(current: AnalysisRunEvent[], event: AnalysisRunEvent): AnalysisRunEvent[] {
  if (event.event_id && current.some((item) => item.event_id === event.event_id)) return current;
  if (event.call_id) {
    const index = current.findIndex((item) => item.call_id === event.call_id);
    if (event.event_type === "llm_response_delta") {
      if (index >= 0) {
        const next = [...current];
        next[index] = { ...next[index], summary: `${next[index].summary}${event.summary}` };
        return next;
      }
      return [
        ...current,
        { ...event, event_type: "llm_response_streaming", title: "正在等待模型回复" },
      ];
    }
    if (event.event_type === "llm_response_completed" && index >= 0) {
      const next = [...current];
      next[index] = { ...event, summary: event.raw_response || event.summary };
      return next;
    }
  }
  return [...current, event];
}

function eventKind(eventType: string) {
  if (eventType.includes("failed")) return "challenge";
  if (eventType.includes("verification")) return "verdict";
  if (eventType.includes("completed") || eventType.includes("generated")) return "discover";
  if (eventType.includes("started") || eventType.includes("tool")) return "execute";
  return "assign";
}

function relativeTime(start: string, current: string | undefined, index: number) {
  if (!current) return `00:${String(index * 3).padStart(2, "0")}`;
  const startTime = Date.parse(start);
  const currentTime = Date.parse(current);
  if (Number.isNaN(startTime) || Number.isNaN(currentTime))
    return `00:${String(index * 3).padStart(2, "0")}`;
  const seconds = Math.max(0, Math.round((currentTime - startTime) / 1000));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function mergeBubbles(traceFlow: Bubble[], eventFlow: Bubble[]) {
  const seen = new Set<string>();
  return [...traceFlow, ...eventFlow].filter((item) => {
    const key = `${item.who}|${item.kind}|${item.text}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function agentForBubble(agents: Agent[], bubble: Bubble) {
  return (
    agents.find((agent) => agent.name === bubble.who) ??
    agents.find((agent) => agent.id === bubble.who) ??
    agents.find((agent) => agent.runtimeName === bubble.who) ??
    agents.find((agent) => bubble.who.includes(agent.name)) ??
    agents.find((agent) => agent.role.includes(bubble.who))
  );
}

function displayAgentName(agents: Agent[], actorName: string) {
  const profile =
    agents.find((agent) => agent.name === actorName) ??
    agents.find((agent) => agent.id === actorName) ??
    agents.find((agent) => agent.runtimeName === actorName);
  if (profile) return profile.name;
  return (
    {
      orchestrator: "沈北辰",
      strategic_agent: "沈北辰",
      verifier: "萧则明",
      quality_reviewer: "萧则明",
      analysis_run: "分析任务",
    }[actorName] ?? actorName
  );
}

function displayAgentText(agents: Agent[], text: string) {
  return agents.reduce((result, agent) => {
    return [agent.runtimeName, agent.id]
      .filter((name): name is string => Boolean(name && name !== agent.name))
      .reduce((current, name) => current.split(name).join(agent.name), result);
  }, text);
}
