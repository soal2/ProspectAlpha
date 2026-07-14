import { createFileRoute, getRouteApi } from "@tanstack/react-router";
import { CheckCircle2, Circle, Loader2, Workflow, Zap, FileText, Search } from "lucide-react";
import { getAgents, getTraceData } from "@/lib/data-service";
import type { Agent, TraceData } from "@/lib/types";

export const Route = createFileRoute("/analyses/$id/trace")({
  loader: async ({ params }) => {
    const [trace, agents] = await Promise.all([getTraceData(params.id), getAgents()]);
    return { trace, agents };
  },
  component: TracePage,
  head: () => ({ meta: [{ title: "Agent Trace · ProspectAlpha" }] }),
});

const kindMeta: Record<string, { label: string; cls: string }> = {
  execute: { label: "执行", cls: "bg-primary/10 text-primary" },
  discover: { label: "发现", cls: "bg-[color:var(--risk-low)]/15 text-[color:var(--risk-low)]" },
  challenge: { label: "质检", cls: "bg-[color:var(--risk-mid)]/15 text-[oklch(0.45_0.12_75)]" },
  verdict: { label: "裁定", cls: "bg-[color:var(--risk-high)]/15 text-[color:var(--risk-high)]" },
  assign: { label: "指派", cls: "bg-[color:var(--gold)]/15 text-[color:var(--navy)]" },
  critique: { label: "质证", cls: "bg-[color:var(--gold)]/20 text-[color:var(--navy)]" },
  revision: { label: "修订", cls: "bg-sky-500/15 text-sky-700" },
  arbitration: { label: "仲裁", cls: "bg-[color:var(--risk-high)]/20 text-[color:var(--risk-high)]" },
};

function TracePage() {
  const api = getRouteApi("/analyses/$id");
  const { analysis } = api.useLoaderData();
  const { trace, agents } = Route.useLoaderData() as {
    trace: TraceData | null;
    agents: Agent[];
  };
  const stages = trace?.stages ?? [];
  const flow = trace?.flow ?? [];
  const evidence = trace?.evidenceFeed ?? [];
  const toolCalls = flow.filter((event) => event.kind === "execute").length;
  const activeAgents = new Set(flow.map((event) => event.who).filter(Boolean)).size;

  return (
    <div className="grid h-[calc(100vh-3.5rem-14.5rem)] min-h-[640px] grid-cols-[280px_minmax(0,1fr)_360px]">
      <aside className="overflow-y-auto border-r border-border bg-card/40 px-5 py-6">
        <div className="flex items-center gap-2 text-xs font-medium">
          <Workflow className="size-3.5 text-primary" /> 九阶段流水线
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
              {index < stages.length - 1 ? (
                <span className="absolute left-2 top-7 h-5 w-px bg-border" />
              ) : null}
            </li>
          ))}
        </ol>
      </aside>

      <section className="flex min-w-0 flex-col overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-6 py-3 text-xs">
          <span className="inline-flex items-center gap-1.5 font-medium">
            <Zap className="size-3.5 text-primary" /> 结构化执行 Trace
          </span>
          <span className="text-muted-foreground">
            {analysis.id} · {toolCalls} 次执行事件 · {activeAgents} 个参与者 · token 未上报
          </span>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">
          <div className="space-y-3">
            {flow.map((event, index) => {
              const agent = agentForTrace(agents, event.who);
              const meta = kindMeta[event.kind] ?? kindMeta.assign;
              return (
                <article key={`${event.t}-${index}-${event.text}`} className="flex gap-3">
                  <div
                    style={{ background: agent?.accent ?? "#0b243d" }}
                    className="grid size-8 shrink-0 place-items-center rounded-full text-xs font-medium text-white"
                  >
                    {agent?.avatar ?? event.who[0] ?? "A"}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 text-[11px]">
                      <span className={`rounded-full px-1.5 py-0.5 font-medium ${meta.cls}`}>
                        {meta.label}
                      </span>
                      <span className="font-medium">{agent?.name ?? event.who}</span>
                      <span className="text-muted-foreground">{agent?.role ?? "系统事件"}</span>
                      <span className="ml-auto tabular text-muted-foreground">+{event.t}</span>
                    </div>
                    <div className="mt-1 rounded-xl border border-border bg-card px-3 py-2 text-sm leading-relaxed">
                      {event.text}
                    </div>
                  </div>
                </article>
              );
            })}
            {!flow.length ? (
              <div className="mt-16 text-center text-xs text-muted-foreground">
                <Search className="mx-auto mb-2 size-6 text-primary/50" />
                Trace 尚未生成，请先启动分析任务。
              </div>
            ) : null}
          </div>
        </div>
      </section>

      <aside className="overflow-y-auto border-l border-border bg-card/40 px-5 py-6">
        <h3 className="flex items-center gap-2 font-serif text-base font-semibold">
          <FileText className="size-4 text-primary" /> 证据引用
        </h3>
        <div className="mt-4 space-y-2">
          {evidence.map((item) => (
            <div
              key={item.id}
              className="rounded-lg border border-border bg-background p-3 text-xs"
            >
              <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
                <span className="tabular">{item.id}</span>
                <span>可信度 {Math.round(item.conf * 100)}</span>
              </div>
              <div className="mt-1 font-medium">{item.title}</div>
              {item.snippet ? (
                <p className="mt-1.5 line-clamp-4 leading-relaxed text-muted-foreground">
                  {item.snippet}
                </p>
              ) : null}
            </div>
          ))}
          {!evidence.length ? (
            <div className="mt-10 text-center text-xs text-muted-foreground">暂无证据引用。</div>
          ) : null}
        </div>
      </aside>
    </div>
  );
}

function agentForTrace(agents: Agent[], who: string) {
  return (
    agents.find((agent) => agent.name === who) ??
    agents.find((agent) => agent.id === who) ??
    agents.find((agent) => who.includes(agent.name)) ??
    agents.find((agent) => agent.role.includes(who))
  );
}
