import { useMemo } from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  type Node,
  type Edge,
  type NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";
import { Sparkles, CheckCircle2, Loader2, Circle, AlertCircle } from "lucide-react";
import type { OrchestraPlan, OrchestraStageStatus } from "@/lib/data-service";
import type { Agent } from "@/lib/types";

const STATUS_RING: Record<OrchestraStageStatus, string> = {
  queued: "ring-muted-foreground/30 text-muted-foreground",
  running: "ring-primary text-primary",
  done: "ring-emerald-500 text-emerald-600",
  failed: "ring-destructive text-destructive",
};

const STATUS_BORDER: Record<OrchestraStageStatus, string> = {
  queued: "border-border",
  running: "border-primary",
  done: "border-emerald-500/60",
  failed: "border-destructive/60",
};

const AGENT_LABEL: Record<string, string> = {
  financial_agent: "财务",
  legal_agent: "法律",
  business_agent: "业务",
  market_agent: "市场",
  orchestrator: "指挥官",
  verifier: "证据门禁",
  reporter: "报告",
};

interface StageNodeData {
  label: string;
  agent: string;
  goal?: string;
  status: OrchestraStageStatus;
  agentMeta?: Agent;
  kind?: "orchestrator" | "agent" | "verifier" | "reporter";
}

function StageNode({ data }: NodeProps<StageNodeData>) {
  const status = data.status ?? "queued";
  const isRunning = status === "running";
  const accent = data.agentMeta?.accent ?? "oklch(0.55 0.08 250)";
  const avatar = data.agentMeta?.avatar ?? data.label.slice(0, 1);
  return (
    <div
      className={`relative w-[210px] rounded-xl bg-card px-3 py-3 shadow-sm ring-1 ${STATUS_RING[status]} ${STATUS_BORDER[status]} border`}
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
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {AGENT_LABEL[data.agent] ?? data.agent}
          </div>
        </div>
        <StatusIcon status={status} />
      </div>
      {data.goal ? (
        <div className="mt-2 line-clamp-2 border-t border-border/60 pt-1.5 text-[10.5px] leading-relaxed text-muted-foreground">
          {data.goal}
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
  return <Circle className="size-3.5 text-muted-foreground/50" />;
}

const nodeTypes = { stage: StageNode };

interface OrchestraFlowProps {
  plan: OrchestraPlan | null;
  stageStatus: Record<string, OrchestraStageStatus>;
  agentsList: Agent[];
  pipelineStage?: string;
}

export function OrchestraFlow({
  plan,
  stageStatus,
  agentsList,
  pipelineStage,
}: OrchestraFlowProps) {
  const { nodes, edges } = useMemo(() => {
    const agentByName = (name: string) => agentsList.find((a) => a.name === name || a.id === name);
    const baseNodes: Node<StageNodeData>[] = [];
    const baseEdges: Edge[] = [];

    // orchestrator root
    baseNodes.push({
      id: "orchestrator",
      type: "stage",
      position: { x: 0, y: 0 },
      data: {
        label: "Orchestra 指挥官",
        agent: "orchestrator",
        goal: plan?.summary ?? "生成多阶段分析计划",
        status: stageStatus["orchestra_planning"] ?? (pipelineStage ? "done" : "queued"),
        agentMeta: agentByName("沈北辰"),
        kind: "orchestrator",
      },
    });

    const stages = plan?.stages ?? [];
    // simple layered layout: each stage goes to layer = max(layer of deps) + 1
    const layerOf: Record<string, number> = {};
    const sortedStages = [...stages].sort((a, b) => a.depends_on.length - b.depends_on.length);
    let maxLayer = 0;
    for (const stage of sortedStages) {
      let layer = 0;
      for (const dep of stage.depends_on) {
        layer = Math.max(layer, (layerOf[dep] ?? 0) + 1);
      }
      layerOf[stage.key] = layer;
      maxLayer = Math.max(maxLayer, layer);
    }
    // group by layer
    const byLayer: Record<number, typeof stages> = {};
    for (const stage of stages) {
      const layer = layerOf[stage.key] ?? 0;
      (byLayer[layer] ??= []).push(stage);
    }

    const NODE_W = 240;
    const NODE_H = 110;
    const LAYER_GAP = 320;

    for (let layer = 0; layer <= maxLayer; layer++) {
      const items = byLayer[layer] ?? [];
      items.forEach((stage, idx) => {
        const agentMeta = agentsList.find((a) => a.name === stage.agent || a.id === stage.agent);
        baseNodes.push({
          id: stage.key,
          type: "stage",
          position: {
            x: 320 + layer * LAYER_GAP,
            y: idx * NODE_H * 1.6 - (items.length - 1) * NODE_H * 0.8,
          },
          data: {
            label: stage.label,
            agent: stage.agent,
            goal: stage.goal,
            status:
              stageStatus[stage.key] ?? inferStageStatus(stage.key, stage.agent, pipelineStage),
            agentMeta,
            kind: "agent",
          },
        });
        // edge from orchestrator (if first layer) or from each dependency
        if (stage.depends_on.length === 0) {
          baseEdges.push({
            id: `e-orch-${stage.key}`,
            source: "orchestrator",
            target: stage.key,
            type: "smoothstep",
            className: "fill-none stroke-primary/60",
          });
        } else {
          for (const dep of stage.depends_on) {
            baseEdges.push({
              id: `e-${dep}-${stage.key}`,
              source: dep,
              target: stage.key,
              type: "smoothstep",
              className: "fill-none stroke-primary/40",
            });
          }
        }
      });
    }

    // verifier + reporter tails (only when pipeline reaches those stages)
    const reachedVerifier =
      pipelineStage === "verifying" ||
      pipelineStage === "reporting" ||
      pipelineStage === "completed";
    const reachedReporter = pipelineStage === "reporting" || pipelineStage === "completed";

    const tailX = 320 + (maxLayer + 1) * LAYER_GAP;
    if (reachedVerifier) {
      baseNodes.push({
        id: "verifier",
        type: "stage",
        position: { x: tailX, y: -60 },
        data: {
          label: "证据门禁",
          agent: "verifier",
          goal: "校验每条 finding 的原文证据强度。",
          status: pipelineStage === "verifying" ? "running" : "done",
          kind: "verifier",
        },
      });
      for (const stage of stages) {
        baseEdges.push({
          id: `e-${stage.key}-verifier`,
          source: stage.key,
          target: "verifier",
          type: "smoothstep",
          className: "fill-none stroke-muted-foreground/40",
        });
      }
    }
    if (reachedReporter) {
      baseNodes.push({
        id: "reporter",
        type: "stage",
        position: { x: tailX + LAYER_GAP, y: -60 },
        data: {
          label: "报告签发",
          agent: "reporter",
          goal: "仅采用已验证 finding 撰写最终报告。",
          status: pipelineStage === "reporting" ? "running" : "done",
          kind: "reporter",
        },
      });
      baseEdges.push({
        id: "e-verifier-reporter",
        source: "verifier",
        target: "reporter",
        type: "smoothstep",
        className: "fill-none stroke-primary/60",
      });
    }

    return { nodes: baseNodes, edges: baseEdges };
  }, [plan, stageStatus, agentsList, pipelineStage]);

  if (!plan) {
    return (
      <div className="grid h-full w-full place-items-center rounded-xl border border-dashed border-border bg-card/30">
        <div className="flex flex-col items-center gap-2 text-xs text-muted-foreground">
          <Sparkles className="size-5 text-primary/60" />
          等待 Orchestra 生成分析编队
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
        fitViewOptions={{ padding: 0.18 }}
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

function inferStageStatus(
  stageKey: string,
  agent: string,
  pipelineStage: string | undefined,
): OrchestraStageStatus {
  if (!pipelineStage) return "queued";
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
  const cur = order.indexOf(pipelineStage);
  if (cur < 0) return "queued";
  // agent_running is where rule agents execute; we treat them all as done after that stage.
  if (pipelineStage === "agent_running") {
    // while agents running, mark all as running (best-effort; we don't have per-agent timing).
    return "running";
  }
  if (cur >= order.indexOf("verifying")) return "done";
  return "queued";
}
