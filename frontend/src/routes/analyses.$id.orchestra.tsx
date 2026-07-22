import { createFileRoute, getRouteApi, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Circle,
  ExternalLink,
  FileText,
  Loader2,
  PauseCircle,
  RefreshCw,
  Sparkles,
  Workflow,
} from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { OrchestraFlow } from "@/components/orchestra-flow";
import {
  getAgents,
  getAnalysisOptions,
  getAnalysisRun,
  selectAnalysisExperts,
  streamAnalysisRun,
  type AgentTeamState,
  type AnalysisOptions,
  type AnalysisRun,
  type AnalysisRunEvent,
  type OrchestraPlan,
  type OrchestraStageStatus,
  type StrategyPlan,
} from "@/lib/data-service";
import type { Agent } from "@/lib/types";
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

export const Route = createFileRoute("/analyses/$id/orchestra")({
  loader: async ({ params }) => {
    const [agentsList, options, run] = await Promise.all([
      getAgents(),
      getAnalysisOptions(),
      getAnalysisRun(params.id),
    ]);
    return { agentsList, options, initialRun: run };
  },
  component: OrchestraPage,
  head: () => ({ meta: [{ title: "动态 Agent 编排 · ProspectAlpha" }] }),
});

const TERMINAL = new Set(["completed", "failed"]);
const PAUSED = new Set(["awaiting_expert_selection"]);

function OrchestraPage() {
  const api = getRouteApi("/analyses/$id");
  const { id } = api.useParams();
  const navigate = useNavigate();
  const { agentsList, options, initialRun } = Route.useLoaderData() as {
    agentsList: Agent[];
    options: AnalysisOptions;
    initialRun: AnalysisRun;
  };

  const [run, setRun] = useState<AnalysisRun>(initialRun);
  const [events, setEvents] = useState<AnalysisRunEvent[]>(initialRun.events ?? []);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [streamEpoch, setStreamEpoch] = useState(0);
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  const [resuming, setResuming] = useState(false);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const [reportReadyOpen, setReportReadyOpen] = useState(false);
  const completionPromptShownRef = useRef(initialRun.status === "completed");

  const plan = useMemo(() => strategyPlanForRun(run), [run]);
  const teamState = run.team_state ?? [];
  const stageStatus = deriveStageStatus(events, run.current_stage, run.status);
  const paused = PAUSED.has(run.status);
  const completedModuleIds = new Set(
    (run.module_results ?? []).map((item) => String(item.module_id ?? "")).filter(Boolean),
  );
  const pendingModuleCount = plan
    ? plan.assignments.filter((assignment) => !completedModuleIds.has(assignment.module_id)).length
    : undefined;
  const reportEvents = useMemo(
    () => events.filter((event) => event.event_type === "agent_reported"),
    [events],
  );

  useEffect(() => {
    if (TERMINAL.has(run.status) || PAUSED.has(run.status)) return;
    const controller = new AbortController();
    setStreamError(null);
    streamAnalysisRun(
      id,
      (event) => setEvents((current) => appendUniqueEvent(current, event)),
      (latestRun) => {
        setRun(latestRun);
        setEvents(latestRun.events ?? []);
      },
      { signal: controller.signal },
    ).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setStreamError(error instanceof Error ? error.message : "stream failed");
    });
    return () => controller.abort();
    // Status updates arrive inside the stream; reconnect only after an explicit resume.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, run.run_id, streamEpoch]);

  useEffect(() => {
    if (TERMINAL.has(run.status)) return;
    const timer = setInterval(() => {
      getAnalysisRun(id)
        .then((fresh) => {
          if (PAUSED.has(run.status) && !PAUSED.has(fresh.status)) {
            setStreamEpoch((value) => value + 1);
          }
          setRun(fresh);
          setEvents((current) =>
            (fresh.events?.length ?? 0) >= current.length ? (fresh.events ?? []) : current,
          );
        })
        .catch(() => undefined);
    }, 1500);
    return () => clearInterval(timer);
  }, [id, run.status]);

  useEffect(() => {
    if (run.status !== "completed" || completionPromptShownRef.current) return;
    completionPromptShownRef.current = true;
    setReportReadyOpen(true);
  }, [run.status]);

  async function resumeWithExperts() {
    if (!selectedAgents.length) return;
    setResuming(true);
    setResumeError(null);
    try {
      const resumedRun = await selectAnalysisExperts(id, selectedAgents);
      setRun(resumedRun);
      setEvents(resumedRun.events ?? []);
      setSelectedAgents([]);
      setStreamEpoch((value) => value + 1);
    } catch (error) {
      setResumeError(error instanceof Error ? error.message : "恢复动态编排失败");
    } finally {
      setResuming(false);
    }
  }

  return (
    <div className="flex h-[calc(100vh-3.5rem)] min-h-[640px] flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-card/40 px-6 py-3">
        <div className="flex items-center gap-2">
          <Sparkles className="size-4 text-primary" />
          <div>
            <h1 className="font-serif text-base font-semibold">动态 Agent 编排</h1>
            <p className="text-[11px] text-muted-foreground">
              {run.company_name} · {run.run_id}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          {streamError ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-destructive/10 px-2 py-1 text-destructive">
              <AlertCircle className="size-3" /> SSE 断开：{streamError}
            </span>
          ) : paused ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-1 text-amber-700">
              <PauseCircle className="size-3" /> 等待人工选择专家
            </span>
          ) : (
            <span className="inline-flex items-center gap-1">
              <span
                className={`size-1.5 rounded-full bg-primary ${TERMINAL.has(run.status) ? "" : "animate-pulse"}`}
              />
              {TERMINAL.has(run.status) ? "已结束" : "分析进行中"}
            </span>
          )}
          <span className="rounded bg-muted px-1.5 py-0.5 tabular">
            {runStatusLabel(run.status)}
          </span>
          {run.status === "completed" ? (
            <button
              onClick={() => navigate({ to: "/analyses/$id/report", params: { id } })}
              className="inline-flex items-center gap-1 rounded-full bg-primary px-3 py-1 text-primary-foreground hover:bg-primary/90"
            >
              查看报告 <ArrowRight className="size-3" />
            </button>
          ) : null}
        </div>
      </header>

      {paused ? (
        <ExpertSelectionPanel
          options={options}
          selectedAgents={selectedAgents}
          interruptReason={run.interrupt_reason}
          error={resumeError}
          resuming={resuming}
          requiredCount={pendingModuleCount}
          onToggle={(agentName) =>
            setSelectedAgents((current) =>
              current.includes(agentName)
                ? current.filter((name) => name !== agentName)
                : pendingModuleCount && current.length >= pendingModuleCount
                  ? current
                  : [...current, agentName],
            )
          }
          onResume={() => void resumeWithExperts()}
        />
      ) : null}

      <div className="grid min-h-0 flex-1 grid-cols-[240px_minmax(0,1fr)_300px] overflow-hidden max-xl:grid-cols-[minmax(0,1fr)_270px]">
        <aside className="overflow-y-auto border-r border-border bg-card/40 px-4 py-5 max-xl:hidden">
          <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
            <Workflow className="size-3.5 text-primary" /> 战略任务编队
          </div>
          {plan ? (
            <>
              <p className="mt-3 text-xs leading-relaxed text-foreground/80">{plan.summary}</p>
              {plan.focus_areas?.length ? (
                <div className="mt-3 flex flex-wrap gap-1">
                  {plan.focus_areas.map((focus) => (
                    <span
                      key={focus}
                      className="rounded-full bg-primary/8 px-2 py-0.5 text-[10px] text-primary"
                    >
                      {focus}
                    </span>
                  ))}
                </div>
              ) : null}
              <ol className="mt-4 space-y-2">
                {plan.assignments.map((assignment) => {
                  const member = teamState.find(
                    (item) =>
                      item.current_module === assignment.module_id ||
                      item.agent_name === assignment.agent_name,
                  );
                  const status =
                    stageStatus[assignment.module_id] ?? teamStageStatus(member, run.current_stage);
                  return (
                    <li
                      key={assignment.module_id}
                      className="rounded-lg border border-border bg-background px-3 py-2.5"
                    >
                      <div className="flex items-start gap-2">
                        <StageStatusIcon status={status} />
                        <div className="min-w-0 flex-1">
                          <div className="text-xs font-medium leading-snug">
                            {assignment.module}
                          </div>
                          <div className="mt-0.5 truncate text-[10px] text-muted-foreground">
                            {expertTitle(options, member?.agent_name ?? assignment.agent_name)} ·{" "}
                            {member?.status ?? "queued"}
                          </div>
                        </div>
                        {member?.replacement_round ? (
                          <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[9px] text-amber-700">
                            R{member.replacement_round}
                          </span>
                        ) : null}
                      </div>
                    </li>
                  );
                })}
              </ol>
            </>
          ) : (
            <div className="mt-4 rounded-lg border border-dashed border-border p-3 text-xs leading-relaxed text-muted-foreground">
              战略层正在加载专家能力卡并规划模块任务。
            </div>
          )}

          {run.replacement_history?.length ? (
            <div className="mt-6 border-t border-border pt-4">
              <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
                <RefreshCw className="size-3.5 text-amber-700" /> 动态替补记录
              </div>
              <div className="mt-2 space-y-2">
                {run.replacement_history.map((record, index) => (
                  <div
                    key={`${replacementValue(record, "module_id")}-${index}`}
                    className="rounded-md bg-amber-500/8 px-2.5 py-2 text-[10.5px] leading-relaxed"
                  >
                    <div className="font-medium">
                      {replacementValue(record, "module_id") || "分析模块"} · 第
                      {replacementValue(record, "round") || index + 1} 轮
                    </div>
                    <div className="mt-0.5 text-muted-foreground">{replacementSummary(record)}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </aside>

        <section className="relative overflow-hidden bg-background">
          <OrchestraFlow
            plan={plan}
            stageStatus={stageStatus}
            teamState={teamState}
            agentsList={agentsList}
            pipelineStage={run.current_stage}
            runStatus={run.status}
            failedStage={run.failed_stage}
          />
        </section>

        <aside className="flex flex-col overflow-hidden border-l border-border bg-card/40">
          <div className="flex items-center justify-between border-b border-border px-4 py-3 text-[11px] font-medium">
            <span className="inline-flex items-center gap-1.5">
              <FileText className="size-3.5 text-primary" /> 专家分析汇报
            </span>
            <span className="text-muted-foreground tabular">{reportEvents.length}</span>
          </div>
          <div className="flex-1 overflow-y-auto px-4 py-3">
            <AnimatePresence initial={false}>
              {reportEvents.map((event, index) => (
                <motion.div
                  key={`${event.created_at ?? index}-${event.event_type}-${event.actor_name}`}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="mb-2.5 rounded-lg border border-border bg-background px-3 py-2"
                >
                  <div className="flex items-center gap-1.5 text-[10.5px] text-muted-foreground">
                    <span
                      className={`rounded px-1.5 py-0.5 font-medium ${kindClass(event.event_type)}`}
                    >
                      {kindLabel(event.event_type)}
                    </span>
                    <span className="truncate font-medium text-foreground/80">
                      {displayNameForAgent(options, event.actor_name)}
                    </span>
                    {event.created_at ? (
                      <span className="ml-auto shrink-0 tabular">
                        {formatClock(event.created_at)}
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-1 text-[12px] font-medium leading-snug">{event.title}</div>
                  {event.summary ? (
                    <div className="mt-0.5 text-[10.5px] leading-relaxed text-muted-foreground">
                      {event.summary}
                    </div>
                  ) : null}
                </motion.div>
              ))}
              {!reportEvents.length && !TERMINAL.has(run.status) && !paused ? (
                <motion.div
                  animate={{ opacity: [0.4, 1, 0.4] }}
                  transition={{ duration: 1.4, repeat: Infinity }}
                  className="inline-flex items-center gap-1.5 text-[10.5px] text-muted-foreground"
                >
                  <span className="size-1.5 rounded-full bg-primary" /> 等待专家提交首份分析汇报
                </motion.div>
              ) : null}
            </AnimatePresence>
          </div>
        </aside>
      </div>
      <AlertDialog open={reportReadyOpen} onOpenChange={setReportReadyOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>分析报告已生成</AlertDialogTitle>
            <AlertDialogDescription>
              {run.company_name} 的专家分析与证据校验已完成，是否现在进入报告查看页面？
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

function ExpertSelectionPanel({
  options,
  selectedAgents,
  interruptReason,
  error,
  resuming,
  requiredCount,
  onToggle,
  onResume,
}: {
  options: AnalysisOptions;
  selectedAgents: string[];
  interruptReason?: string | null;
  error: string | null;
  resuming: boolean;
  requiredCount?: number;
  onToggle: (agentName: string) => void;
  onResume: () => void;
}) {
  const layerGroups = useMemo(() => groupAgentsByLayer(options.agents), [options.agents]);
  return (
    <section className="border-b border-amber-500/30 bg-amber-500/8 px-6 py-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-2xl">
          <div className="flex items-center gap-2 text-sm font-semibold text-amber-800">
            <PauseCircle className="size-4" /> 需要人工选择可用专家
          </div>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {interruptReason ?? "战略层返回的专家名单未通过校验，当前运行已安全暂停。"}
            {requiredCount
              ? ` 当前有 ${requiredCount} 个未完成模块，请选择 ${requiredCount} 位专家。`
              : " 请选择至少一位专家。"}
            选择后将从 LangGraph checkpoint 恢复；后续任务和替补仍由战略层动态规划。
          </p>
        </div>
        <button
          type="button"
          onClick={onResume}
          disabled={
            !selectedAgents.length ||
            resuming ||
            (requiredCount !== undefined && selectedAgents.length !== requiredCount)
          }
          className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {resuming ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <ArrowRight className="size-3.5" />
          )}
          恢复动态编排
        </button>
      </div>
      <div className="mt-3 max-h-[280px] space-y-3 overflow-y-auto pr-1">
        {layerGroups.map((group) => (
          <div key={group.layer} className="rounded-lg border border-border/60 bg-card/30 p-2.5">
            <div className="mb-2 flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">
              <span
                className="rounded px-1.5 py-0.5 text-[9.5px]"
                style={{
                  backgroundColor: `${layerAccent(group.layer)}1a`,
                  color: layerAccent(group.layer),
                }}
              >
                {group.layer}
              </span>
              <span>{layerLabel(group.layer)}</span>
              <span className="text-muted-foreground/60 tabular">· {group.agents.length} 位</span>
            </div>
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
              {group.agents.map((agent) => {
                const selected = selectedAgents.includes(agent.agent_name);
                const accent = agent.accent || "#3b6fa0";
                return (
                  <div
                    key={agent.agent_name}
                    className={`rounded-lg border bg-card px-3 py-2.5 transition ${
                      selected ? "border-primary ring-1 ring-primary/30" : "border-border"
                    }`}
                  >
                    <label className="flex cursor-pointer items-start gap-2">
                      <input
                        type="checkbox"
                        checked={selected}
                        disabled={
                          !selected &&
                          requiredCount !== undefined &&
                          selectedAgents.length >= requiredCount
                        }
                        onChange={() => onToggle(agent.agent_name)}
                        className="mt-0.5"
                      />
                      <span
                        className="flex size-6 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold text-white"
                        style={{ backgroundColor: accent }}
                      >
                        {agent.avatar ?? agent.display_name?.slice(0, 1) ?? agent.title.slice(0, 1)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs font-medium">
                          {agent.display_name ?? agent.title}
                        </span>
                        {agent.role_zh ? (
                          <span className="mt-0.5 block truncate text-[10px] text-muted-foreground">
                            {agent.role_zh}
                          </span>
                        ) : null}
                        <span className="mt-0.5 line-clamp-2 block text-[10px] leading-relaxed text-muted-foreground">
                          {agent.description ||
                            agent.capabilities?.join("、") ||
                            "可参与招股书风险分析"}
                        </span>
                      </span>
                    </label>
                    <div className="mt-2 flex items-center justify-between gap-2">
                      <span className="truncate text-[9.5px] text-muted-foreground">
                        {agent.expertise?.slice(0, 2).join(" · ") ||
                          agent.capabilities?.slice(0, 2).join(" · ") ||
                          "通用分析"}
                      </span>
                      {agent.agent_card_url ? (
                        <a
                          href={agent.agent_card_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex shrink-0 items-center gap-0.5 text-[9.5px] text-primary hover:underline"
                        >
                          Agent Card <ExternalLink className="size-2.5" />
                        </a>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      {error ? (
        <div className="mt-2 inline-flex items-center gap-1 text-xs text-destructive">
          <AlertCircle className="size-3.5" /> {error}
        </div>
      ) : null}
    </section>
  );
}

function groupAgentsByLayer(
  agents: AnalysisOptions["agents"],
): { layer: string; agents: AnalysisOptions["agents"] }[] {
  const groups = new Map<string, AnalysisOptions["agents"]>();
  for (const agent of agents) {
    const layer = (agent.layer || "L1").toUpperCase();
    const bucket = groups.get(layer) ?? [];
    bucket.push(agent);
    groups.set(layer, bucket);
  }
  const order = ["L3", "L2", "L1"];
  return [...groups.entries()]
    .sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]))
    .map(([layer, list]) => ({
      layer,
      agents: list.sort((a, b) =>
        String(a.persona_id ?? a.agent_name).localeCompare(String(b.persona_id ?? b.agent_name)),
      ),
    }));
}

function layerLabel(layer: string): string {
  if (layer === "L3") return "战略层 · 总监 / 首席 / 红队";
  if (layer === "L2") return "高级专家 · 策略 / 定价 / 路演";
  return "执行专家 · 财务 / 法务 / 市场 / 业务 / 技术 / 检索";
}

function layerAccent(layer: string): string {
  if (layer === "L3") return "#0b243d";
  if (layer === "L2") return "#1e3a5f";
  return "#3b6fa0";
}

function strategyPlanForRun(run: AnalysisRun): StrategyPlan | null {
  if (run.strategy_plan && Array.isArray(run.strategy_plan.assignments)) {
    return run.strategy_plan;
  }
  const legacy = run.orchestra_plan as OrchestraPlan | StrategyPlan | undefined;
  if (!legacy) return null;
  if ("assignments" in legacy && Array.isArray(legacy.assignments)) return legacy;
  if (!("stages" in legacy) || !Array.isArray(legacy.stages)) return null;
  return {
    summary: legacy.summary,
    focus_areas: legacy.focus_areas,
    assignments: legacy.stages.map((stage) => ({
      module_id: stage.key,
      agent_name: stage.agent,
      module: stage.label,
      goal: stage.goal,
      expected_outputs: stage.expected_outputs,
    })),
  };
}

function deriveStageStatus(
  events: AnalysisRunEvent[],
  currentStage: string | undefined,
  runStatus: string,
): Record<string, OrchestraStageStatus> {
  const statuses: Record<string, OrchestraStageStatus> = {};
  for (const event of events) {
    const rawModuleId = String(
      event.payload?.module_id ?? event.payload?.task_id ?? event.stage_key ?? "",
    );
    const moduleId = rawModuleId.replace(/__replacement_\d+$/, "");
    if (!moduleId) continue;
    const type = event.event_type.toLowerCase();
    if (type.includes("failed") || type.includes("error")) statuses[moduleId] = "failed";
    else if (type.includes("completed") || type.includes("result") || type.includes("artifact"))
      statuses[moduleId] = "done";
    else if (type.includes("started") || type.includes("assigned") || type.includes("working"))
      statuses[moduleId] = "running";
  }
  if (runStatus === "awaiting_expert_selection") {
    for (const key of Object.keys(statuses)) {
      if (statuses[key] === "running") statuses[key] = "paused";
    }
  }
  return statuses;
}

function teamStageStatus(
  member: AgentTeamState | undefined,
  currentStage: string,
): OrchestraStageStatus {
  if (!member) return "queued";
  const status = member.status.toLowerCase();
  if (status.includes("fail") || status.includes("error")) return "failed";
  if (status.includes("complete") || status.includes("done") || status.includes("terminat"))
    return "done";
  if (status.includes("run") || status.includes("work") || member.current_module) return "running";
  if (["verifying", "synthesizing", "reporting", "completed"].includes(currentStage)) return "done";
  return "queued";
}

function StageStatusIcon({ status }: { status: OrchestraStageStatus }) {
  if (status === "done") return <CheckCircle2 className="mt-0.5 size-3.5 text-emerald-600" />;
  if (status === "running")
    return <Loader2 className="mt-0.5 size-3.5 animate-spin text-primary" />;
  if (status === "failed") return <AlertCircle className="mt-0.5 size-3.5 text-destructive" />;
  if (status === "paused") return <PauseCircle className="mt-0.5 size-3.5 text-amber-700" />;
  return <Circle className="mt-0.5 size-3.5 text-muted-foreground/40" />;
}

function expertTitle(options: AnalysisOptions, agentName: string) {
  return displayNameForAgent(options, agentName);
}

function displayNameForAgent(options: AnalysisOptions, agentName: string) {
  return (
    options.agents.find((agent) => agent.agent_name === agentName)?.display_name ??
    ({ orchestrator: "沈北辰", strategic_agent: "沈北辰" }[agentName] || agentName)
  );
}

function kindLabel(eventType: string): string {
  const type = eventType.toLowerCase();
  if (type === "agent_reported") return "汇报";
  if (type.includes("mailbox") || type.includes("message") || type.includes("a2a")) return "A2A";
  if (type.includes("idle") || type.includes("thread") || type.includes("shutdown"))
    return "生命周期";
  if (type.includes("replacement") || type.includes("replan")) return "替补";
  if (type.includes("interrupt") || type.includes("expert_selection")) return "接管";
  if (type.includes("arbiter") || type.includes("arbitration")) return "仲裁";
  if (type.includes("critique")) return "质证";
  if (type.includes("revised") || type.includes("revision")) return "修订";
  if (type.includes("debate")) return "辩论";
  if (type.includes("verification")) return "裁定";
  if (type.includes("report") || type.includes("synth")) return "整合";
  if (type.includes("strategy") || type.includes("orchestra")) return "战略";
  if (type.includes("failed") || type.includes("error")) return "失败";
  if (type.includes("completed") || type.includes("result")) return "完成";
  if (type.includes("started") || type.includes("assigned")) return "启动";
  return "事件";
}

function kindClass(eventType: string): string {
  const label = kindLabel(eventType);
  if (label === "失败") return "bg-destructive/10 text-destructive";
  if (label === "接管" || label === "替补") return "bg-amber-500/15 text-amber-700";
  if (label === "裁定" || label === "仲裁")
    return "bg-[color:var(--risk-high)]/15 text-[color:var(--risk-high)]";
  if (label === "质证") return "bg-[color:var(--gold)]/20 text-[color:var(--navy)]";
  if (label === "修订") return "bg-sky-500/15 text-sky-700";
  if (label === "辩论") return "bg-[color:var(--gold)]/15 text-[color:var(--navy)]";
  if (label === "完成" || label === "整合" || label === "汇报")
    return "bg-emerald-500/15 text-emerald-700";
  if (label === "A2A") return "bg-sky-500/12 text-sky-700";
  if (label === "生命周期") return "bg-slate-500/12 text-slate-700";
  if (label === "战略" || label === "启动") return "bg-primary/10 text-primary";
  return "bg-muted text-muted-foreground";
}

function runStatusLabel(status: string) {
  const labels: Record<string, string> = {
    task_understanding: "任务理解",
    pdf_parsing: "材料解析",
    expert_orchestration: "动态专家编排",
    evidence_extraction: "证据抽取",
    risk_identification: "风险识别",
    expert_debate: "专家辩论",
    cross_validation: "证据校验",
    report_generation: "战略整合",
    quality_review: "质检审裁",
    orchestra_planning: "战略规划",
    spawning_experts: "启动专家线程",
    agent_running: "专家协作",
    replanning: "动态替补",
    awaiting_expert_selection: "等待人工接管",
    verifying: "证据校验",
    synthesizing: "战略整合",
    completed: "已完成",
    failed: "失败",
  };
  return labels[status] ?? status;
}

function replacementValue(record: Record<string, unknown>, key: string) {
  const value = record[key];
  return value === undefined || value === null ? "" : String(value);
}

function replacementSummary(record: Record<string, unknown>) {
  const failed = replacementValue(record, "failed_agent") || replacementValue(record, "agent_name");
  const replacement = replacementValue(record, "replacement_agent");
  const reason = replacementValue(record, "reason");
  if (failed && replacement) return `${failed} → ${replacement}${reason ? ` · ${reason}` : ""}`;
  return reason || replacementValue(record, "status") || "战略层已重新规划该模块。";
}

function appendUniqueEvent(current: AnalysisRunEvent[], event: AnalysisRunEvent) {
  const duplicate = current.some(
    (item) =>
      item.created_at === event.created_at &&
      item.event_type === event.event_type &&
      item.actor_name === event.actor_name &&
      item.title === event.title,
  );
  return duplicate ? current : [...current, event];
}

function formatClock(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}
