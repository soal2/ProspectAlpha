import { createFileRoute, getRouteApi, Link } from "@tanstack/react-router";
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
  getAnalysisRun,
  getTraceData,
  getEvidenceFeed,
  startAnalysisRun,
  streamAnalysisRun,
  type AnalysisRun,
  type AnalysisRunEvent,
} from "@/lib/data-service";
import { type Agent } from "@/lib/types";
import { motion, AnimatePresence } from "framer-motion";

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
  snippet?: string | null;
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
  const [streamError, setStreamError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [filter, setFilter] = useState("");
  const startedRunRef = useRef<string | null>(null);

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
  }, [id]);

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
      streamAnalysisRun(
        id,
        (event) => {
          if (cancelled) return;
          setLiveEvents((current) => [...current, event]);
        },
        (finalRun) => {
          if (cancelled) return;
          setRun(finalRun);
          setLiveEvents(finalRun.events ?? []);
          void refreshTrace();
        },
        { signal: controller.signal },
      ).catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (cancelled) return;
        setStreamError(err instanceof Error ? err.message : "实时流连接失败");
        void refreshTrace();
      });
      return () => controller.abort();
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
  }, [id, refreshTrace]);

  const stages = trace?.stages?.length ? trace.stages : fallbackStages(run);
  const eventBubbles = useMemo(() => {
    const traceFlow = trace?.flow ?? [];
    const eventFlow = liveEvents.map((event, index) => eventToBubble(event, run.created_at, index));
    return mergeBubbles(traceFlow, eventFlow);
  }, [liveEvents, run.created_at, trace]);
  const liveEvidenceFeed = useMemo(
    () =>
      terminalStatuses.has(run.status) ? evidenceFeed : mergeEvidenceFeed(evidenceFeed, liveEvents),
    [evidenceFeed, liveEvents, run.status],
  );
  const filteredLiveEvidence = liveEvidenceFeed.filter((item) =>
    `${item.source} ${item.title} ${item.id} ${item.snippet ?? ""}`
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
    (agent) => selectedAgentNames.has(agent.id) || selectedAgentNames.has(agent.name),
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
            <FileText className="size-3.5" /> {liveEvidenceFeed.length} 条证据
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
                  <div className="text-[11px] text-muted-foreground">{stage.owner}</div>
                </div>
                {index < stages.length - 1 && (
                  <span className="absolute left-2 top-7 h-6 w-px bg-border" />
                )}
              </li>
            ))}
          </ol>

          <div className="mt-8 rounded-lg border border-border bg-background p-3">
            <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
              <Users className="size-3.5" /> 专家团（{visibleAgents.length}）
            </div>
            <div className="mt-3 space-y-2 text-[11px]">
              {(["L3", "L2", "L1"] as const).map((layer) => (
                <div key={layer}>
                  <div className="text-muted-foreground">
                    {layer === "L3" ? "决策层" : layer === "L2" ? "策略层" : "执行层"} {layer}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {visibleAgents
                      .filter((agent) => agent.layer === layer)
                      .map((agent) => (
                        <div
                          key={agent.id}
                          title={`${agent.name} · ${agent.role}`}
                          style={{ background: agent.accent }}
                          className="grid size-7 place-items-center rounded-full text-[10px] font-medium text-white ring-2 ring-background"
                        >
                          {agent.avatar}
                        </div>
                      ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </aside>

        <section className="flex min-w-0 flex-col overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-card/40 px-6 py-3 text-xs">
            <span className="inline-flex items-center gap-1.5 font-medium">
              <Wand2 className="size-3.5 text-primary" /> 实时协作思维流
            </span>
            <span className="text-muted-foreground">
              累计事件 <span className="tabular text-foreground">{liveEvents.length}</span> · Agent
              调用流
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
                        <span className="font-medium">{agent?.name ?? bubble.who}</span>
                        <span className="text-muted-foreground">{agent?.role ?? "Agent 专家"}</span>
                        <span className="ml-auto text-muted-foreground tabular">+{bubble.t}</span>
                      </div>
                      <div className="mt-1 rounded-xl rounded-tl-sm border border-border bg-card px-3 py-2 text-sm leading-relaxed shadow-sm">
                        {bubble.text}
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
                  <span className="size-1.5 rounded-full bg-primary" /> Agent 仍在协作中…
                </motion.div>
              ) : null}
            </AnimatePresence>
          </div>
        </section>

        <aside className="overflow-y-auto border-l border-border bg-card/50 px-5 py-6 max-lg:hidden">
          <div className="flex items-center justify-between text-xs font-medium">
            <span className="flex items-center gap-2">
              <Database className="size-3.5 text-primary" /> 证据库（实时）
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
                    {item.source}
                  </span>
                  <span className="tabular">{item.id}</span>
                  <span>{item.time}</span>
                  <span className="ml-auto tabular">可信度 {Math.round(item.conf * 100)}</span>
                </div>
                <div className="mt-1.5 text-xs font-medium leading-snug">{item.title}</div>
                {item.snippet ? (
                  <p className="mt-1.5 line-clamp-3 text-[11px] leading-relaxed text-muted-foreground">
                    {item.snippet}
                  </p>
                ) : null}
                <button className="mt-2 inline-flex items-center gap-1 text-[11px] text-primary hover:underline">
                  查看原文 <ExternalLink className="size-3" />
                </button>
              </div>
            ))}
            {!filteredLiveEvidence.length ? (
              <div className="mt-16 text-center text-xs text-muted-foreground">
                <FileText className="mx-auto mb-2 size-8 opacity-40" />
                证据采集中，稍候片刻……
              </div>
            ) : null}
          </div>
        </aside>
      </div>
      <FloatingTracePanel
        run={run}
        stages={stages}
        events={liveEvents}
        activeAgents={activeAgentCount}
      />
    </div>
  );
}

function fallbackStages(run: AnalysisRun) {
  const lifecycle = [
    ["task_understanding", "任务理解", "战略层 Agent"],
    ["parsing", "PDF 解析", "解析服务"],
    ["indexing", "检索索引", "检索服务"],
    ["orchestra_planning", "动态规划", "战略层 Agent"],
    ["spawning_experts", "线程与邮箱初始化", "A2A Runtime"],
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
  const fromEvents = events
    .filter((event) => event.event_type === "evidence_found")
    .map((event, index) => {
      const payload = event.payload ?? {};
      return {
        id: String(payload.evidence_id ?? `live-${index}`),
        source: event.actor_name,
        title: event.title.replace("发现证据：", ""),
        conf: Number(payload.confidence ?? 0),
        time: event.created_at ? formatClock(event.created_at) : "近实时",
        page: Number(payload.page_number ?? 0),
        snippet: event.summary,
      };
    });
  const byId = new Map<string, EvidenceFeedItem>();
  [...seed, ...fromEvents].forEach((item) => byId.set(item.id, item));
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

function FloatingTracePanel({
  run,
  stages,
  events,
  activeAgents,
}: {
  run: AnalysisRun;
  stages: { key: string; label: string; owner: string; status: string; cost?: string }[];
  events: AnalysisRunEvent[];
  activeAgents: number;
}) {
  const [open, setOpen] = useState(true);
  const [visible, setVisible] = useState(true);
  const [pos, setPos] = useState({ x: 24, y: 84 });
  const dragging = useRef<{ dx: number; dy: number } | null>(null);
  if (!visible) return null;
  const currentStage = stages.find((stage) => stage.status === "running") ?? stages.at(-1);
  const toolCalls = events.filter((event) => event.event_type.startsWith("tool_")).length;
  const recent = events.slice(-4).reverse();
  return (
    <div
      className="fixed z-40 w-80 rounded-lg border border-border bg-card/95 shadow-xl backdrop-blur"
      style={{ right: pos.x, top: pos.y }}
    >
      <div
        className="flex cursor-move items-center justify-between border-b border-border px-3 py-2 text-xs"
        onMouseDown={(event) => {
          dragging.current = { dx: event.clientX + pos.x, dy: event.clientY - pos.y };
          const move = (moveEvent: MouseEvent) => {
            if (!dragging.current) return;
            setPos({
              x: Math.max(12, dragging.current.dx - moveEvent.clientX),
              y: Math.max(56, moveEvent.clientY - dragging.current.dy),
            });
          };
          const up = () => {
            dragging.current = null;
            window.removeEventListener("mousemove", move);
            window.removeEventListener("mouseup", up);
          };
          window.addEventListener("mousemove", move);
          window.addEventListener("mouseup", up);
        }}
      >
        <span className="font-medium">Agent Trace</span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setOpen((v) => !v)}
            className="rounded px-1 text-muted-foreground hover:bg-muted"
          >
            {open ? "收起" : "展开"}
          </button>
          <button
            onClick={() => setVisible(false)}
            className="rounded px-1 text-muted-foreground hover:bg-muted"
          >
            关闭
          </button>
        </div>
      </div>
      {open ? (
        <div className="space-y-3 p-3 text-[11px]">
          <div className="grid grid-cols-3 gap-2 text-center">
            <div className="rounded bg-muted/60 p-2">
              <div className="text-muted-foreground">工具调用</div>
              <div className="tabular text-sm font-semibold">{toolCalls}</div>
            </div>
            <div className="rounded bg-muted/60 p-2">
              <div className="text-muted-foreground">活跃专家</div>
              <div className="tabular text-sm font-semibold">{activeAgents}</div>
            </div>
            <div className="rounded bg-muted/60 p-2">
              <div className="text-muted-foreground">Token</div>
              <div className="tabular text-sm font-semibold">未上报</div>
            </div>
          </div>
          <div>
            <div className="text-muted-foreground">当前执行步骤</div>
            <div className="mt-1 rounded border border-border bg-background px-2 py-1.5">
              {currentStage?.label ?? run.current_stage} · {run.status}
            </div>
          </div>
          <div>
            <div className="text-muted-foreground">最近事件</div>
            <div className="mt-1 space-y-1">
              {recent.map((event, index) => (
                <div
                  key={`${event.created_at}-${index}`}
                  className="rounded bg-background px-2 py-1"
                >
                  <span className="font-medium">{event.action_label ?? event.event_type}</span>
                  <span className="ml-1 text-muted-foreground">{event.title}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function eventToBubble(event: AnalysisRunEvent, runCreatedAt: string, index: number): Bubble {
  return {
    who: event.actor_name || "analysis_run",
    kind: eventKind(event.event_type),
    text: event.summary ? `${event.title}：${event.summary}` : event.title,
    t: relativeTime(runCreatedAt, event.created_at, index),
  };
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
    agents.find((agent) => bubble.who.includes(agent.name)) ??
    agents.find((agent) => agent.role.includes(bubble.who))
  );
}
