import { createFileRoute, getRouteApi } from "@tanstack/react-router";
import { useState, useEffect } from "react";
import {
  CheckCircle2,
  Loader2,
  Circle,
  Workflow,
  PlayCircle,
  Rewind,
  FastForward,
  X,
  Zap,
  FileText,
  GitBranch,
  Search,
} from "lucide-react";
import { getAgents } from "@/lib/data-service";
import { type Agent } from "@/mocks";
import { motion, AnimatePresence } from "framer-motion";

export const Route = createFileRoute("/analyses/$id/trace")({
  component: TracePage,
  head: () => ({ meta: [{ title: "Agent 决策链路回溯 · ProspectAlpha" }] }),
});

const stages = [
  { key: "intake", label: "需求理解", owner: "沈砚", status: "done", cost: "0.4s" },
  { key: "dispatch", label: "编排派遣", owner: "沈砚", status: "done", cost: "0.6s" },
  { key: "evidence", label: "证据采集", owner: "程哲", status: "done", cost: "8.2s" },
  { key: "feature", label: "特征抽取", owner: "蔡宝", status: "done", cost: "4.1s" },
  { key: "cross", label: "多 Agent 协同", owner: "林清越", status: "done", cost: "12.7s" },
  { key: "predict", label: "风险预测", owner: "苏明哲", status: "done", cost: "1.2s" },
  { key: "qa", label: "质检审裁", owner: "周翊", status: "done", cost: "5.3s" },
  { key: "sign", label: "签发交付", owner: "沈砚", status: "done", cost: "0.8s" },
];

const flow = [
  {
    who: "沈砚",
    kind: "assign",
    text: "你是 ProspectAlpha 首席指挥官。从专家名册中为本次调研挑选最合适的团队。规则：必须含 1 位 L3 决策层统筹、1-2 位 L2 策略顾问、3-6 位 L1 执行专家。",
    t: "00:00",
  },
  {
    who: "沈砚",
    kind: "verdict",
    text: '组队完成：{"lead":"L3-001","members":["L2-001","L2-002","L1-001","L1-002","L1-003","L1-005","L1-006"]}',
    t: "00:01",
  },
  {
    who: "程哲",
    kind: "execute",
    text: "深度检索「招股书 · 风险因素」: 客户集中度、供应商集中度、关联交易、回购义务、最新动态 2026",
    t: "00:03",
  },
  {
    who: "程哲",
    kind: "discover",
    text: "「风险因素」聚合到 124 条候选片段，去重后保留 47 条 · 已存入证据库",
    t: "00:11",
  },
  {
    who: "蔡宝",
    kind: "discover",
    text: "三大报表抽取完成：净利润 +8.2% · 经营性现金流 −18.6% · 自由现金流首次为负",
    t: "00:18",
  },
  {
    who: "温叙白",
    kind: "discover",
    text: "发现「特殊股东权利」第 28 项 ——若上市后 12 个月内未达营收 Y，触发硬性回购",
    t: "00:24",
  },
  {
    who: "周翊",
    kind: "challenge",
    text: "对蔡宝的现金流结论提出存疑：是否区分了应收账款变动与真实经营恶化？",
    t: "00:32",
  },
  {
    who: "蔡宝",
    kind: "execute",
    text: "重新拆分：应收账款增加 41%、存货增加 27%、剩余为真实经营性恶化（32%）",
    t: "00:41",
  },
  {
    who: "苏明哲",
    kind: "discover",
    text: "结合行业景气与近期 IPO 表现，给出破发概率推断：0.34（Top 30% 风险分位）",
    t: "00:48",
  },
  {
    who: "林清越",
    kind: "verdict",
    text: "终审通过 14 章节正文 · 综合风险得分 58 · 中风险 · 主要风险：对赌赎回 + 现金流",
    t: "00:57",
  },
];

const kindMeta: Record<string, { label: string; cls: string }> = {
  execute: { label: "执行", cls: "bg-primary/10 text-primary" },
  discover: { label: "发现", cls: "bg-[color:var(--risk-low)]/15 text-[color:var(--risk-low)]" },
  challenge: { label: "质疑", cls: "bg-[color:var(--risk-mid)]/15 text-[oklch(0.45_0.12_75)]" },
  verdict: { label: "裁定", cls: "bg-[color:var(--risk-high)]/15 text-[color:var(--risk-high)]" },
  assign: { label: "指派", cls: "bg-sage/15 text-[color:var(--sage)]" },
};

function TracePage() {
  const api = getRouteApi("/analyses/$id");
  const { analysis } = api.useLoaderData();
  const [traceIdx, setTraceIdx] = useState<number | null>(1);
  const [view, setView] = useState<"chat" | "dag">("chat");
  const [playing, setPlaying] = useState(false);
  const [agentsList, setAgentsList] = useState<Agent[]>([]);

  useEffect(() => {
    let cancelled = false;
    getAgents().then((data) => {
      if (!cancelled) setAgentsList(data);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="grid h-[calc(100vh-3.5rem-14.5rem)] min-h-[640px] grid-cols-[260px_1fr_360px]">
      {/* pipeline */}
      <aside className="overflow-y-auto border-r border-border bg-card/40 px-5 py-6">
        <div className="flex items-center gap-2 text-xs font-medium">
          <Workflow className="size-3.5 text-primary" /> 任务流水线 · {stages.length} 阶段
        </div>
        <ol className="mt-4 space-y-1">
          {stages.map((s, i) => (
            <li key={s.key} className="relative pl-6">
              <span className="absolute left-0 top-1.5">
                {s.status === "done" && <CheckCircle2 className="size-4 text-primary" />}
                {s.status === "running" && <Loader2 className="size-4 animate-spin text-primary" />}
                {s.status === "queued" && <Circle className="size-4 text-muted-foreground/40" />}
              </span>
              <div className="rounded-md px-2 py-1 text-xs">
                <div className="font-medium leading-snug">{s.label}</div>
                <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                  <span>{s.owner}</span>
                  <span className="tabular">{s.cost}</span>
                </div>
              </div>
              {i < stages.length - 1 && (
                <span className="absolute left-2 top-7 h-5 w-px bg-border" />
              )}
            </li>
          ))}
        </ol>

        <div className="mt-8 rounded-lg border border-border bg-background p-3">
          <div className="text-[11px] font-medium text-muted-foreground">
            专家队伍（{analysis.experts}） · 三层架构
          </div>
          <div className="mt-3 space-y-2 text-[11px]">
            {(["L3", "L2", "L1"] as const).map((layer) => (
              <div key={layer}>
                <div className="text-muted-foreground">
                  {layer === "L3" ? "决策层" : layer === "L2" ? "策略层" : "执行层"} {layer}
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {agentsList
                    .filter((a) => a.layer === layer)
                    .slice(0, layer === "L1" ? 4 : 3)
                    .map((a) => (
                      <div
                        key={a.id}
                        title={a.name}
                        style={{ background: a.accent }}
                        className="grid size-7 place-items-center rounded-full text-[10px] font-medium text-white ring-2 ring-background"
                      >
                        {a.avatar}
                      </div>
                    ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </aside>

      {/* main thought stream */}
      <section className="flex flex-col overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-6 py-3 text-xs">
          <span className="inline-flex items-center gap-1.5 font-medium">
            <Workflow className="size-3.5 text-primary" /> 实时协作思维流 · 10 / 10 完成
          </span>
          <div className="ml-auto flex items-center gap-1">
            <div className="mr-2 inline-flex rounded-full border border-border bg-card p-0.5">
              {(["chat", "dag"] as const).map((v) => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={`rounded-full px-2.5 py-1 text-[11px] ${view === v ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}
                >
                  {v === "chat" ? "对话" : "DAG"}
                </button>
              ))}
            </div>
            <button className="grid size-7 place-items-center rounded-full text-muted-foreground hover:bg-accent">
              <Rewind className="size-3.5" />
            </button>
            <button
              onClick={() => setPlaying((p) => !p)}
              className="inline-flex items-center gap-1 rounded-full bg-primary px-3 py-1 text-[11px] font-medium text-primary-foreground"
            >
              <PlayCircle className="size-3.5" /> {playing ? "暂停" : "决策回放"}
            </button>
            <button className="grid size-7 place-items-center rounded-full text-muted-foreground hover:bg-accent">
              <FastForward className="size-3.5" />
            </button>
            <span className="ml-3 text-muted-foreground">
              累计 <span className="tabular text-foreground">38 642</span> tokens · 17 次调用
            </span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          {view === "chat" ? (
            <div className="space-y-3">
              {flow.map((b, i) => {
                const a = agentsList.find((x) => x.name === b.who);
                const meta = kindMeta[b.kind];
                return (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: i * 0.04 }}
                    className="flex gap-3"
                  >
                    <div
                      style={{ background: a?.accent ?? "#888" }}
                      className="grid size-8 shrink-0 place-items-center rounded-full text-xs font-medium text-white"
                    >
                      {a?.avatar ?? b.who[0]}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 text-[11px]">
                        <span className={`rounded-full px-1.5 py-0.5 font-medium ${meta.cls}`}>
                          {meta.label}
                        </span>
                        <span className="font-medium">{b.who}</span>
                        <span className="text-muted-foreground">{a?.layer}</span>
                        <span className="ml-auto text-muted-foreground tabular">+{b.t}</span>
                      </div>
                      <button
                        onClick={() => setTraceIdx(i)}
                        className={`mt-1 block w-full rounded-xl border bg-card px-3 py-2 text-left text-sm leading-relaxed transition ${traceIdx === i ? "border-primary ring-1 ring-primary/30" : "border-border hover:border-primary/30"}`}
                      >
                        {b.text}
                        <div className="mt-1.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                          <span className="rounded bg-muted px-1.5 py-0.5 tabular">
                            {[1200, 820, 1640, 520, 980, 1140, 640, 1380, 720, 1820][i] ?? 1000} tok
                          </span>
                          <span className="tabular">2.{i % 9}s</span>
                          <span className="text-primary hover:underline">查看 Trace →</span>
                        </div>
                      </button>
                    </div>
                  </motion.div>
                );
              })}
            </div>
          ) : (
            <DagView />
          )}
        </div>
      </section>

      {/* trace detail */}
      <aside className="overflow-y-auto border-l border-border bg-card/40 px-5 py-6">
        <div className="flex items-center justify-between text-xs font-medium">
          <span className="inline-flex items-center gap-1.5">
            <Zap className="size-3.5 text-primary" /> Agent 决策日志 · Trace
          </span>
          {traceIdx !== null && (
            <button
              onClick={() => setTraceIdx(null)}
              className="grid size-6 place-items-center rounded-md text-muted-foreground hover:bg-accent"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>

        <AnimatePresence mode="wait">
          {traceIdx !== null && flow[traceIdx] ? (
            <motion.div
              key={traceIdx}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              className="mt-4 space-y-4 text-xs"
            >
              <div className="flex items-center gap-2">
                {(() => {
                  const a = agentsList.find((x) => x.name === flow[traceIdx].who);
                  return (
                    <>
                      <div
                        style={{ background: a?.accent }}
                        className="grid size-7 place-items-center rounded-full text-[10px] font-medium text-white"
                      >
                        {a?.avatar}
                      </div>
                      <div>
                        <div className="font-medium">{a?.name}</div>
                        <div className="text-[10px] text-muted-foreground">{a?.role}</div>
                      </div>
                    </>
                  );
                })()}
                <span className="ml-auto rounded bg-muted px-1.5 py-0.5 tabular">
                  {traceIdx + 1} / {flow.length}
                </span>
              </div>
              <div>
                <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                  Prompt
                </div>
                <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-3 leading-relaxed">{`[system] 你是 Verda 首席指挥官，从专家名册中挑选团队。
规则：必须含 1 位 L3 决策层统筹、1-2 位 L2 策略顾问、3-6 位 L1 执行专家。为每位被选专家给出 ≤ 30 字的具体指派理由。只输出 JSON：{"lead":"专家id","members":[...]}

[user] 任务：${flow[traceIdx].text}`}</pre>
              </div>
              <div>
                <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                  Output
                </div>
                <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-3 leading-relaxed">{`{"lead":"L3-001","members":[
  {"id":"L2-001","reason":"行业策略 + 风险权重"},
  {"id":"L2-002","reason":"定价博弈与破发概率"},
  {"id":"L1-001","reason":"三大报表与现金流"},
  {"id":"L1-002","reason":"客户与供应商集中度"},
  {"id":"L1-003","reason":"对赌赎回与关联交易"},
  {"id":"L1-005","reason":"招股书证据检索"}
]}`}</pre>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div className="rounded-md bg-muted/50 p-2">
                  <div className="text-[10px] text-muted-foreground">输入</div>
                  <div className="tabular text-sm font-medium">2 664</div>
                </div>
                <div className="rounded-md bg-muted/50 p-2">
                  <div className="text-[10px] text-muted-foreground">输出</div>
                  <div className="tabular text-sm font-medium">1 807</div>
                </div>
                <div className="rounded-md bg-primary/10 p-2 text-primary">
                  <div className="text-[10px]">合计</div>
                  <div className="tabular text-sm font-medium">4 471</div>
                </div>
              </div>
              <div>
                <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                  引用证据 (3)
                </div>
                <div className="mt-2 space-y-1.5">
                  {["e_898aa168", "e_5e8c343e", "e_1cdca7b3"].map((e) => (
                    <div
                      key={e}
                      className="flex items-center justify-between rounded-md border border-border bg-background px-2 py-1.5"
                    >
                      <span className="flex items-center gap-1.5">
                        <FileText className="size-3 text-primary" /> {e}
                      </span>
                      <span className="tabular text-muted-foreground">P.218</span>
                    </div>
                  ))}
                </div>
              </div>
            </motion.div>
          ) : (
            <div className="mt-10 px-2 text-center text-[11px] text-muted-foreground">
              <Search className="mx-auto mb-2 size-5 text-primary/50" />
              点击中间任一气泡 ·<br />
              查看 Prompt / Output / 引用证据
            </div>
          )}
        </AnimatePresence>
      </aside>
    </div>
  );
}

function DagView() {
  // simple SVG dag
  const nodes = [
    { id: "intake", x: 90, y: 220, label: "需求", owner: "沈砚" },
    { id: "evd", x: 260, y: 120, label: "证据采集", owner: "程哲" },
    { id: "fin", x: 260, y: 230, label: "财务", owner: "蔡宝" },
    { id: "legal", x: 260, y: 340, label: "法务", owner: "温叙白" },
    { id: "cross", x: 460, y: 220, label: "交叉协同", owner: "林清越" },
    { id: "pred", x: 640, y: 160, label: "风险预测", owner: "苏明哲" },
    { id: "qa", x: 640, y: 280, label: "质检红队", owner: "周翊" },
    { id: "sign", x: 830, y: 220, label: "签发", owner: "沈砚" },
  ];
  const edges = [
    ["intake", "evd"],
    ["intake", "fin"],
    ["intake", "legal"],
    ["evd", "cross"],
    ["fin", "cross"],
    ["legal", "cross"],
    ["cross", "pred"],
    ["cross", "qa"],
    ["pred", "sign"],
    ["qa", "sign"],
  ];
  const find = (id: string) => nodes.find((n) => n.id === id)!;
  return (
    <div className="h-[560px] w-full rounded-xl border border-border bg-card">
      <svg viewBox="0 0 940 460" className="h-full w-full">
        <defs>
          <marker
            id="arrow"
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M0,0 L10,5 L0,10 z" fill="oklch(0.58 0.05 150)" />
          </marker>
        </defs>
        {edges.map(([a, b]) => {
          const A = find(a),
            B = find(b);
          return (
            <line
              key={`${a}-${b}`}
              x1={A.x + 50}
              y1={A.y}
              x2={B.x - 50}
              y2={B.y}
              stroke="oklch(0.58 0.05 150 / 0.5)"
              strokeWidth={1.5}
              markerEnd="url(#arrow)"
            />
          );
        })}
        {nodes.map((n) => (
          <g key={n.id} transform={`translate(${n.x - 50}, ${n.y - 24})`}>
            <rect
              width={100}
              height={48}
              rx={10}
              fill="var(--card)"
              stroke="oklch(0.46 0.055 155)"
            />
            <text
              x={50}
              y={20}
              textAnchor="middle"
              className="fill-foreground"
              fontSize={12}
              fontWeight={500}
            >
              {n.label}
            </text>
            <text x={50} y={36} textAnchor="middle" className="fill-muted-foreground" fontSize={10}>
              {n.owner}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}
