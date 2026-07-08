import { createFileRoute, getRouteApi, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  Loader2,
  Circle,
  Sparkles,
  Workflow,
  FileText,
  AlertCircle,
  ArrowRight,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { OrchestraFlow } from "@/components/orchestra-flow";
import {
  getAgents,
  streamAnalysisRun,
  type AnalysisRun,
  type AnalysisRunEvent,
  type OrchestraPlan,
  type OrchestraStageStatus,
} from "@/lib/data-service";
import { type Agent } from "@/mocks";

export const Route = createFileRoute("/analyses/$id/orchestra")({
  loader: async ({ params }) => {
    const [agentsList, run] = await Promise.all([getAgents(), fetchRun(params.id)]);
    return { agentsList, initialRun: run };
  },
  component: OrchestraPage,
  head: () => ({ meta: [{ title: "Orchestra 实时编排 · ProspectAlpha" }] }),
});

async function fetchRun(id: string): Promise<AnalysisRun | null> {
  try {
    const res = await fetch(
      `${import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"}/analysis-runs/${id}`,
    );
    if (!res.ok) return null;
    return (await res.json()) as AnalysisRun;
  } catch {
    return null;
  }
}

const TERMINAL = new Set(["completed", "failed"]);

function OrchestraPage() {
  const api = getRouteApi("/analyses/$id");
  const { id } = api.useParams();
  const navigate = useNavigate();
  const { agentsList: initialAgents, initialRun } = Route.useLoaderData() as {
    agentsList: Agent[];
    initialRun: AnalysisRun | null;
  };

  const [run, setRun] = useState<AnalysisRun | null>(initialRun);
  const [events, setEvents] = useState<AnalysisRunEvent[]>(initialRun?.events ?? []);
  const [agentsList] = useState<Agent[]>(initialAgents);
  const [streamError, setStreamError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const plan = (run?.orchestra_plan ?? null) as OrchestraPlan | null;

  // Derive stage status from run.events
  const stageStatus = deriveStageStatus(events, run?.current_stage);

  useEffect(() => {
    if (!run) return;
    if (TERMINAL.has(run.status)) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setStreamError(null);
    streamAnalysisRun(
      id,
      (event) => setEvents((cur) => [...cur, event]),
      (finalRun) => {
        setRun(finalRun);
        setEvents(finalRun.events);
      },
      { signal: controller.signal },
    ).catch((err: unknown) => {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setStreamError(err instanceof Error ? err.message : "stream failed");
    });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, run?.run_id]);

  // Poll for plan if not yet on the run (LLM might still be planning)
  useEffect(() => {
    if (!run) return;
    if (plan) return;
    if (TERMINAL.has(run.status)) return;
    const t = setInterval(() => {
      fetchRun(id).then((fresh) => {
        if (!fresh) return;
        if (fresh.orchestra_plan) {
          setRun(fresh);
        }
      });
    }, 1500);
    return () => clearInterval(t);
  }, [id, run, plan]);

  return (
    <div className="flex h-[calc(100vh-3.5rem)] min-h-[640px] flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-card/40 px-6 py-3">
        <div className="flex items-center gap-2">
          <Sparkles className="size-4 text-primary" />
          <div>
            <h1 className="font-serif text-base font-semibold">Orchestra 实时编排</h1>
            <p className="text-[11px] text-muted-foreground">
              {run?.company_name ?? "加载中"} · {run?.run_id}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          {streamError ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-destructive/10 px-2 py-1 text-destructive">
              <AlertCircle className="size-3" /> SSE 断开：{streamError}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1">
              <span className="size-1.5 animate-pulse rounded-full bg-primary" />
              {TERMINAL.has(run?.status ?? "") ? "已结束" : "实时同步中"}
            </span>
          )}
          <span className="rounded bg-muted px-1.5 py-0.5 tabular">{run?.status ?? "--"}</span>
          {run?.status === "completed" ? (
            <button
              onClick={() => navigate({ to: "/analyses/$id", params: { id } })}
              className="inline-flex items-center gap-1 rounded-full bg-primary px-3 py-1 text-primary-foreground hover:bg-primary/90"
            >
              查看报告 <ArrowRight className="size-3" />
            </button>
          ) : null}
        </div>
      </header>

      <div className="grid flex-1 grid-cols-[260px_1fr_320px] overflow-hidden">
        {/* Left: stage pipeline */}
        <aside className="overflow-y-auto border-r border-border bg-card/40 px-4 py-5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
            <Workflow className="size-3.5 text-primary" /> 分析编队阶段
          </div>
          <ol className="mt-3 space-y-1">
            {pipelineStages(plan, run).map((s, i) => (
              <li key={s.key} className="relative pl-6">
                <span className="absolute left-0 top-1.5">
                  {s.status === "done" && <CheckCircle2 className="size-4 text-emerald-600" />}
                  {s.status === "running" && (
                    <Loader2 className="size-4 animate-spin text-primary" />
                  )}
                  {(s.status === "queued" || s.status === "failed") && (
                    <Circle
                      className={`size-4 ${s.status === "failed" ? "text-destructive" : "text-muted-foreground/40"}`}
                    />
                  )}
                </span>
                <div className="rounded-md px-2 py-1 text-[11.5px]">
                  <div className="font-medium leading-snug">{s.label}</div>
                  <div className="text-[10px] text-muted-foreground">{s.owner}</div>
                </div>
                {i < (plan?.stages.length ?? 0) + 3 - 1 && (
                  <span className="absolute left-2 top-6 h-4 w-px bg-border" />
                )}
              </li>
            ))}
          </ol>
        </aside>

        {/* Middle: reactflow DAG */}
        <section className="relative overflow-hidden bg-background">
          <OrchestraFlow
            plan={plan}
            stageStatus={stageStatus}
            agentsList={agentsList}
            pipelineStage={run?.current_stage}
          />
          {!plan ? (
            <div className="absolute inset-x-0 bottom-4 flex justify-center">
              <motion.div
                animate={{ opacity: [0.5, 1, 0.5] }}
                transition={{ duration: 1.6, repeat: Infinity }}
                className="inline-flex items-center gap-1.5 rounded-full bg-card px-3 py-1.5 text-[11px] text-muted-foreground shadow ring-1 ring-border"
              >
                <Sparkles className="size-3 text-primary" /> 等待 Orchestra LLM 生成分析计划…
              </motion.div>
            </div>
          ) : null}
        </section>

        {/* Right: event stream */}
        <aside className="flex flex-col overflow-hidden border-l border-border bg-card/40">
          <div className="flex items-center justify-between border-b border-border px-4 py-3 text-[11px] font-medium">
            <span className="inline-flex items-center gap-1.5">
              <FileText className="size-3.5 text-primary" /> 实时事件流
            </span>
            <span className="text-muted-foreground tabular">{events.length}</span>
          </div>
          <div className="flex-1 overflow-y-auto px-4 py-3">
            <AnimatePresence initial={false}>
              {events.map((ev, i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="mb-2.5 rounded-lg border border-border bg-background px-3 py-2"
                >
                  <div className="flex items-center gap-1.5 text-[10.5px] text-muted-foreground">
                    <span
                      className={`rounded px-1.5 py-0.5 font-medium ${kindClass(ev.event_type)}`}
                    >
                      {kindLabel(ev.event_type)}
                    </span>
                    <span className="font-medium text-foreground/80">{ev.actor_name}</span>
                  </div>
                  <div className="mt-1 text-[12px] font-medium leading-snug">{ev.title}</div>
                  {ev.summary ? (
                    <div className="mt-0.5 text-[10.5px] leading-relaxed text-muted-foreground">
                      {ev.summary}
                    </div>
                  ) : null}
                </motion.div>
              ))}
              {!TERMINAL.has(run?.status ?? "") ? (
                <motion.div
                  animate={{ opacity: [0.4, 1, 0.4] }}
                  transition={{ duration: 1.4, repeat: Infinity }}
                  className="inline-flex items-center gap-1.5 text-[10.5px] text-muted-foreground"
                >
                  <span className="size-1.5 rounded-full bg-primary" /> Agent 仍在协同…
                </motion.div>
              ) : null}
            </AnimatePresence>
          </div>
        </aside>
      </div>
    </div>
  );
}

function pipelineStages(plan: OrchestraPlan | null, run: AnalysisRun | null) {
  const cur = run?.current_stage ?? "";
  const status = (key: string): OrchestraStageStatus => {
    if (cur === key) return "running";
    const order = [
      "orchestra_planning",
      "ready",
      "queued",
      "parsing",
      "indexing",
      "agent_running",
      "verifying",
      "reporting",
      "completed",
    ];
    const ci = order.indexOf(cur);
    const ki = order.indexOf(key);
    if (ci < 0 || ki < 0) return "queued";
    if (run?.status === "completed") return "done";
    if (run?.status === "failed" && cur === key) return "failed";
    return ci > ki ? "done" : "queued";
  };
  const stages: { key: string; label: string; owner: string; status: OrchestraStageStatus }[] = [
    {
      key: "orchestra_planning",
      label: "Orchestra 编排",
      owner: "orchestrator",
      status: status("orchestra_planning"),
    },
  ];
  if (plan) {
    for (const stage of plan.stages) {
      // map plan stage key → approximate pipeline signal: stages execute during "agent_running"
      const ss: OrchestraStageStatus =
        cur === "agent_running"
          ? "running"
          : status("agent_running") === "done"
            ? "done"
            : "queued";
      stages.push({
        key: stage.key,
        label: stage.label,
        owner: stage.agent,
        status: ss,
      });
    }
  }
  stages.push({
    key: "verifying",
    label: "证据门禁",
    owner: "verifier",
    status: status("verifying"),
  });
  stages.push({
    key: "reporting",
    label: "报告签发",
    owner: "reporter",
    status: status("reporting"),
  });
  return stages;
}

function deriveStageStatus(
  _events: AnalysisRunEvent[],
  currentStage: string | undefined,
): Record<string, OrchestraStageStatus> {
  // We rely primarily on run.current_stage; events are for the right-pane feed.
  // Return empty map; the OrchestraFlow component infers per-stage status from currentStage.
  void currentStage;
  return {};
}

function kindLabel(eventType: string): string {
  if (eventType.startsWith("agent_started") || eventType.startsWith("stage_started")) return "启动";
  if (eventType.startsWith("agent_completed") || eventType.startsWith("stage_completed"))
    return "完成";
  if (eventType.startsWith("verification")) return "裁定";
  if (eventType.startsWith("report")) return "报告";
  if (eventType.startsWith("orchestra")) return "编排";
  if (eventType.startsWith("stage_failed")) return "失败";
  if (eventType.startsWith("tool")) return "调用";
  return "事件";
}

function kindClass(eventType: string): string {
  if (eventType.startsWith("stage_failed")) return "bg-destructive/10 text-destructive";
  if (eventType.startsWith("orchestra")) return "bg-primary/10 text-primary";
  if (eventType.startsWith("verification")) return "bg-amber-500/15 text-amber-700";
  if (eventType.startsWith("report")) return "bg-emerald-500/15 text-emerald-700";
  if (eventType.startsWith("agent_started") || eventType.startsWith("stage_started"))
    return "bg-primary/10 text-primary";
  if (eventType.startsWith("agent_completed") || eventType.startsWith("stage_completed"))
    return "bg-emerald-500/10 text-emerald-700";
  return "bg-muted text-muted-foreground";
}
