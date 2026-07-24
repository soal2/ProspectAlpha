import { useMemo } from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  type Edge,
  type Node,
  type NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";
import { AlertCircle, CheckCircle2, Circle, Loader2, PauseCircle, Sparkles } from "lucide-react";
import type {
  AgentTeamState,
  OrchestraStageStatus,
  StrategyAssignment,
  StrategyPlan,
} from "@/lib/data-service";
import type { Agent } from "@/lib/types";

const STATUS_RING: Record<OrchestraStageStatus, string> = {
  queued: "ring-muted-foreground/30 text-muted-foreground",
  running: "ring-primary text-primary",
  done: "ring-emerald-500 text-emerald-600",
  failed: "ring-destructive text-destructive",
  paused: "ring-amber-500 text-amber-700",
};

const STATUS_BORDER: Record<OrchestraStageStatus, string> = {
  queued: "border-border",
  running: "border-primary",
  done: "border-emerald-500/60",
  failed: "border-destructive/60",
  paused: "border-amber-500/60",
};

interface StageNodeData {
  label: string;
  agent: string;
  goal?: string;
  status: OrchestraStageStatus;
  agentMeta?: Agent;
  lifecycleStatus?: string;
  currentModule?: string | null;
  replacementRound?: number;
}

function StageNode({ data }: NodeProps<StageNodeData>) {
  const status = data.status ?? "queued";
  const isRunning = status === "running";
  const accent = data.agentMeta?.accent ?? "oklch(0.55 0.08 250)";
  const avatar = data.agentMeta?.avatar ?? data.label.slice(0, 1);
  return (
    <div
      className={`relative w-[190px] rounded-xl border bg-card px-3 py-3 shadow-sm ring-1 ${STATUS_RING[status]} ${STATUS_BORDER[status]}`}
    >
      <Handle type="target" position={Position.Left} className="!bg-primary/60" />
      <div className="flex items-center gap-2">
        <div
          className={`grid size-7 shrink-0 place-items-center rounded-full text-[11px] font-medium text-white ${isRunning ? "animate-pulse" : ""}`}
          style={{ background: accent }}
        >
          {avatar}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12px] font-semibold leading-tight">{data.label}</div>
          <div className="truncate text-[10px] tracking-wide text-muted-foreground">
            {data.agentMeta?.role ?? data.agent}
          </div>
        </div>
        <StatusIcon status={status} />
      </div>
      {data.goal ? (
        <div className="mt-2 line-clamp-2 border-t border-border/60 pt-1.5 text-[10.5px] leading-relaxed text-muted-foreground">
          {data.goal}
        </div>
      ) : null}
      {data.lifecycleStatus ? (
        <div className="mt-2 flex items-center justify-between gap-2 text-[9.5px] text-muted-foreground">
          <span className="truncate">{teamStatusLabel(data.lifecycleStatus)}</span>
          {data.replacementRound ? <span>替补 R{data.replacementRound}</span> : null}
        </div>
      ) : null}
      <Handle type="source" position={Position.Right} className="!bg-primary/60" />
    </div>
  );
}

function StatusIcon({ status }: { status: OrchestraStageStatus }) {
  if (status === "running") return <Loader2 className="size-3.5 animate-spin" />;
  if (status === "done") return <CheckCircle2 className="size-3.5 text-emerald-600" />;
  if (status === "failed") return <AlertCircle className="size-3.5 text-destructive" />;
  if (status === "paused") return <PauseCircle className="size-3.5 text-amber-700" />;
  return <Circle className="size-3.5 text-muted-foreground/50" />;
}

const nodeTypes = { stage: StageNode };

interface OrchestraFlowProps {
  plan: StrategyPlan | null;
  stageStatus: Record<string, OrchestraStageStatus>;
  teamState: AgentTeamState[];
  agentsList: Agent[];
  pipelineStage?: string;
  runStatus?: string;
  failedStage?: string | null;
}

export function OrchestraFlow({
  plan,
  stageStatus,
  teamState,
  agentsList,
  pipelineStage,
  runStatus,
  failedStage,
}: OrchestraFlowProps) {
  const { nodes, edges } = useMemo(() => {
    const agentByName = (name: string) =>
      agentsList.find(
        (agent) => agent.name === name || agent.id === name || agent.runtimeName === name,
      );
    const stateByAgent = new Map(teamState.map((state) => [state.agent_name, state]));
    const stateByModule = new Map(
      teamState
        .filter((state) => state.current_module)
        .map((state) => [state.current_module as string, state]),
    );
    const baseNodes: Node<StageNodeData>[] = [];
    const baseEdges: Edge[] = [];

    baseNodes.push({
      id: "strategist",
      type: "stage",
      position: { x: 0, y: 0 },
      data: {
        label: "战略层 Agent",
        agent: "orchestrator",
        goal: plan?.summary ?? "读取实时专家目录并生成任务编队",
        status:
          runStatus === "awaiting_expert_selection"
            ? "paused"
            : pipelineStage === "replanning" || pipelineStage === "orchestra_planning"
              ? "running"
              : plan
                ? "done"
                : "queued",
        agentMeta: agentByName("orchestrator") ?? agentByName("沈北辰"),
      },
    });

    const assignments = plan?.assignments ?? [];
    const rowGap = 142;
    assignments.forEach((assignment, index) => {
      const team =
        stateByModule.get(assignment.module_id) ?? stateByAgent.get(assignment.agent_name);
      const activeAgentName = team?.agent_name ?? assignment.agent_name;
      const nodeId = assignmentNodeId(assignment);
      baseNodes.push({
        id: nodeId,
        type: "stage",
        position: {
          x: 250,
          y: index * rowGap - ((assignments.length - 1) * rowGap) / 2,
        },
        data: {
          label: assignment.module,
          agent: activeAgentName,
          goal: assignment.goal,
          status:
            stageStatus[assignment.module_id] ??
            inferAssignmentStatus(team, pipelineStage, runStatus, failedStage),
          agentMeta: agentByName(activeAgentName),
          lifecycleStatus: team?.status,
          currentModule: team?.current_module,
          replacementRound: team?.replacement_round,
        },
      });
      baseEdges.push({
        id: `e-strategist-${nodeId}`,
        source: "strategist",
        target: nodeId,
        type: "smoothstep",
        className: "fill-none stroke-primary/60",
      });
    });

    const verifierStatus = systemStageStatus("verifying", pipelineStage, runStatus, failedStage);
    const synthesisStatus = systemStageStatus(
      "synthesizing",
      pipelineStage,
      runStatus,
      failedStage,
    );
    baseNodes.push({
      id: "verifier",
      type: "stage",
      position: { x: 500, y: -72 },
      data: {
        label: "证据门禁",
        agent: "verifier",
        goal: "校验模块结论与招股书原文证据的可追溯性。",
        status: verifierStatus,
        agentMeta: agentByName("verifier"),
      },
    });
    baseNodes.push({
      id: "synthesis",
      type: "stage",
      position: { x: 750, y: -72 },
      data: {
        label: "战略整合",
        agent: "orchestrator",
        goal: "融合全部已验证模块，形成完整风险分析报告。",
        status: synthesisStatus,
        agentMeta: agentByName("orchestrator") ?? agentByName("沈北辰"),
      },
    });

    for (const assignment of assignments) {
      baseEdges.push({
        id: `e-${assignmentNodeId(assignment)}-verifier`,
        source: assignmentNodeId(assignment),
        target: "verifier",
        type: "smoothstep",
        className: "fill-none stroke-muted-foreground/40",
      });
    }
    baseEdges.push({
      id: "e-verifier-synthesis",
      source: "verifier",
      target: "synthesis",
      type: "smoothstep",
      className: "fill-none stroke-primary/60",
    });

    return { nodes: baseNodes, edges: baseEdges };
  }, [agentsList, failedStage, pipelineStage, plan, runStatus, stageStatus, teamState]);

  if (!plan) {
    return (
      <div className="grid h-full w-full place-items-center rounded-xl border border-dashed border-border bg-card/30">
        <div className="flex flex-col items-center gap-2 text-xs text-muted-foreground">
          <Sparkles className="size-5 text-primary/60" />
          战略层正在读取专家能力卡并生成动态编队
        </div>
      </div>
    );
  }

  return (
    <div className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={18} color="oklch(0.92 0.005 250)" />
        <Controls
          showInteractive={false}
          className="!rounded-md !border !border-border !bg-card !shadow-sm"
        />
      </ReactFlow>
    </div>
  );
}

function assignmentNodeId(assignment: StrategyAssignment) {
  return `assignment-${assignment.module_id}`;
}

function inferAssignmentStatus(
  team: AgentTeamState | undefined,
  pipelineStage: string | undefined,
  runStatus: string | undefined,
  failedStage: string | null | undefined,
): OrchestraStageStatus {
  if (runStatus === "awaiting_expert_selection") return "paused";
  const status = team?.status.toLowerCase() ?? "";
  if (status.includes("fail") || status.includes("error")) return "failed";
  if (status.includes("complete") || status.includes("done") || status.includes("terminated"))
    return "done";
  if (
    status.includes("running") ||
    status.includes("working") ||
    status.includes("busy") ||
    Boolean(team?.current_module)
  )
    return "running";
  if (["verifying", "synthesizing", "reporting", "completed"].includes(pipelineStage ?? ""))
    return "done";
  return "queued";
}

function systemStageStatus(
  stage: "verifying" | "synthesizing",
  pipelineStage: string | undefined,
  runStatus: string | undefined,
  failedStage: string | null | undefined,
): OrchestraStageStatus {
  const stageAliases =
    stage === "verifying"
      ? new Set(["verifying", "cross_validation"])
      : new Set(["synthesizing", "reporting", "report_generation"]);
  if (runStatus === "failed" && stageAliases.has(failedStage ?? "")) return "failed";
  if (stageAliases.has(pipelineStage ?? "")) return "running";
  const order = [
    "task_understanding",
    "pdf_parsing",
    "expert_orchestration",
    "evidence_extraction",
    "risk_identification",
    "orchestra_planning",
    "spawning_experts",
    "agent_running",
    "replanning",
    "verifying",
    "cross_validation",
    "synthesizing",
    "reporting",
    "report_generation",
    "quality_review",
    "completed",
  ];
  const current = order.indexOf(pipelineStage ?? "");
  const target = order.indexOf(stage);
  if (runStatus === "completed" || (current >= 0 && current > target)) return "done";
  return "queued";
}

function teamStatusLabel(status: string) {
  const normalized = status.toLowerCase();
  if (normalized.includes("idle")) return "IDLE · 邮箱监听";
  if (normalized.includes("spawn") || normalized.includes("start")) return "线程启动中";
  if (normalized.includes("run") || normalized.includes("work")) return "WORK · 正在处理";
  if (normalized.includes("complete") || normalized.includes("done")) return "模块已回传";
  if (normalized.includes("fail") || normalized.includes("error")) return "执行失败";
  if (normalized.includes("terminat") || normalized.includes("shutdown")) return "线程已关闭";
  return status;
}
