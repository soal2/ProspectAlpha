import { createFileRoute, Link, notFound } from "@tanstack/react-router";
import { ArrowLeft, Sparkles, FileText, GitBranch } from "lucide-react";
import { getAgents } from "@/lib/data-service";
import { type Agent } from "@/lib/types";

export const Route = createFileRoute("/experts/$id")({
  head: ({ params }) => ({
    meta: [
      { title: `${params.id} · Agent 专家详情` },
      {
        name: "description",
        content: "Agent 专家档案：人设、技能、Prompt 模板、近期任务与调用统计",
      },
    ],
  }),
  loader: async ({ params }) => {
    const agents = await getAgents();
    const agent = agents.find((a) => a.id === params.id);
    if (!agent) throw notFound();
    return { agent } as { agent: Agent };
  },
  component: ExpertDetailPage,
  errorComponent: ({ error }) => (
    <div className="p-8 text-sm text-muted-foreground">加载失败：{error.message}</div>
  ),
  notFoundComponent: () => (
    <div className="p-8 text-sm text-muted-foreground">没有找到这位专家</div>
  ),
});

function ExpertDetailPage() {
  const { agent } = Route.useLoaderData() as { agent: Agent };
  return (
    <div className="px-6 py-8 sm:px-10">
      <Link
        to="/experts"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" /> 返回专家公会
      </Link>

      <div className="mt-4 grid gap-6 lg:grid-cols-[1fr_2fr]">
        <div className="rounded-2xl border border-border bg-card p-6">
          <div
            style={{ background: agent.accent }}
            className="grid size-20 place-items-center rounded-2xl text-2xl font-medium text-white"
          >
            {agent.avatar}
          </div>
          <h1 className="mt-4 font-serif text-2xl font-semibold">{agent.name}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {agent.role} · <span className="tabular">{agent.layer}</span>
          </p>
          <p className="mt-4 text-sm leading-relaxed">{agent.desc}</p>
          <div className="mt-5 flex flex-wrap gap-1.5">
            {agent.expertise.map((s: string) => (
              <span
                key={s}
                className="rounded-full bg-primary/8 px-2.5 py-0.5 text-[11px] text-primary"
              >
                {s}
              </span>
            ))}
          </div>
          <div className="mt-6 grid grid-cols-2 gap-3 border-t border-border pt-4 text-xs">
            <Stat label="总调用次数" value={agent.callCount.toString()} />
            <Stat label="平均 tokens" value={agent.avgTokens.toString()} />
            <Stat label="近 30 天" value="+18%" />
            <Stat label="平均耗时" value="2.4s" />
          </div>
        </div>

        <div className="space-y-6">
          <section className="rounded-2xl border border-border bg-card p-6">
            <h2 className="flex items-center gap-2 font-serif text-lg font-semibold">
              <Sparkles className="size-4 text-primary" /> System Prompt 模板
            </h2>
            <pre className="mt-4 max-h-72 overflow-auto rounded-xl bg-muted/50 p-4 text-xs leading-relaxed">{`你是 ${agent.name}，${agent.role}（层级 ${agent.layer}）。
职责：${agent.desc}
工作规范：
1. 任何结论必须挂接 ≥1 条招股书原文证据（[e_xxx]）。
2. 不确定时主动向 L3 总控请示，不得猜测。
3. 输出 JSON：{"conclusions":[...],"evidence":[...],"confidence":0..1}`}</pre>
          </section>

          <section className="rounded-2xl border border-border bg-card p-6">
            <h2 className="flex items-center gap-2 font-serif text-lg font-semibold">
              <FileText className="size-4 text-primary" /> 近期参与的任务
            </h2>
            <div className="mt-3 divide-y divide-border">
              {[
                "宁德时代 · 现金流分析",
                "京东健康 · 客户集中度",
                "蚂蚁集团 · 监管合规扫描",
                "理想汽车 · 估值定价博弈",
              ].map((t, i) => (
                <Link
                  key={t}
                  to="/analyses/$id/trace"
                  params={{ id: "ana_03750" }}
                  className="flex items-center justify-between py-3 text-sm hover:text-primary"
                >
                  <span className="flex items-center gap-2">
                    <GitBranch className="size-3.5 text-muted-foreground" />
                    {t}
                  </span>
                  <span className="text-xs text-muted-foreground tabular">
                    06-{24 - i} 14:0{i}
                  </span>
                </Link>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-muted/50 p-3">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="mt-1 font-serif text-lg font-semibold tabular">{value}</div>
    </div>
  );
}
