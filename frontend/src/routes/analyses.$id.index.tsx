import { createFileRoute, getRouteApi } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import {
  Activity,
  CheckCircle2,
  Loader2,
  Circle,
  FileText,
  ExternalLink,
  Search,
  Wand2,
} from "lucide-react";
import { getAgents } from "@/lib/data-service";
import { type Agent } from "@/mocks";
import { motion, AnimatePresence } from "framer-motion";

export const Route = createFileRoute("/analyses/$id/")({
  component: RealtimeStream,
  head: () => ({ meta: [{ title: "实时协作思维流 · IPO RiskAgent" }] }),
});

const stages = [
  { key: "intake", label: "需求理解 · 元数据对齐", owner: "沈砚", status: "done" },
  { key: "dispatch", label: "编排派遣 · 分组任务", owner: "沈砚", status: "done" },
  { key: "evidence", label: "证据采集 · 招股书检索", owner: "程哲", status: "running" },
  { key: "cross", label: "交叉分析 · 多专家协同", owner: "林清越", status: "queued" },
  { key: "write", label: "报告撰写 · 结构化输出", owner: "林清越", status: "queued" },
  { key: "qa", label: "质检审裁 · 红队回路", owner: "周翊", status: "queued" },
  { key: "sign", label: "签发交付", owner: "沈砚", status: "queued" },
];

const seed = [
  {
    who: "沈砚",
    kind: "execute",
    text: "指派 蔡宝（财报采集专家）：负责商业模式和增长指标抽取",
    t: "00:02",
  },
  {
    who: "沈砚",
    kind: "execute",
    text: "指派 温叙白（法务条款专家）：扫描对赌、赎回、关联交易、特殊股东权利",
    t: "00:03",
  },
  {
    who: "程哲",
    kind: "execute",
    text: "开始深度检索「招股书 · 风险因素」: 客户集中度、供应商集中度、关联交易、回购义务、最新动态 2026",
    t: "00:09",
  },
  {
    who: "程哲",
    kind: "discover",
    text: "「风险因素」聚合到 124 条候选片段，去重后保留 47 条",
    t: "00:18",
  },
  {
    who: "蔡宝",
    kind: "discover",
    text: "三大报表抽取完成 · 净利润 +8.2% · 经营性现金流 −18.6%",
    t: "00:24",
  },
  {
    who: "温叙白",
    kind: "discover",
    text: "发现「特殊股东权利」第 28 项 ——若上市后 12 个月内未达营收 Y，触发硬性回购",
    t: "00:31",
  },
  {
    who: "周翊",
    kind: "challenge",
    text: "对蔡宝的现金流结论提出存疑：是否区分了应收账款变动与真实经营恶化？",
    t: "00:38",
  },
  {
    who: "蔡宝",
    kind: "execute",
    text: "重新拆分：应收增加占 41%、存货占 27%、剩余为经营性恶化",
    t: "00:46",
  },
];

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
  assign: "bg-sage/15 text-sage",
};

const evidenceFeed = [
  { id: "#1", source: "官方", title: "招股书 P.218 · 现金流量表", conf: 0.93, time: "近实时" },
  {
    id: "#2",
    source: "web",
    title: "海通证券 · 宁德时代深度报告 2026Q2",
    conf: 0.62,
    time: "近一年",
  },
  {
    id: "#3",
    source: "web",
    title: "公司公告 · 关于回购义务的补充披露",
    conf: 0.81,
    time: "近半年",
  },
  {
    id: "#4",
    source: "官方",
    title: "招股书 P.92 · 风险因素 · 关联交易",
    conf: 0.88,
    time: "近实时",
  },
  {
    id: "#5",
    source: "web",
    title: "Wind · 同业宁波容百 2025 年度报告",
    conf: 0.54,
    time: "近一年",
  },
];

function RealtimeStream() {
  const api = getRouteApi("/analyses/$id");
  const { analysis } = api.useLoaderData();
  const [bubbles, setBubbles] = useState(seed.slice(0, 4));
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

  useEffect(() => {
    let i = 4;
    const t = setInterval(() => {
      if (i >= seed.length) return;
      setBubbles((b) => [...b, seed[i]]);
      i++;
    }, 1600);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="grid h-[calc(100vh-3.5rem-14.5rem)] min-h-[640px] grid-cols-[240px_1fr_340px]">
      {/* pipeline */}
      <aside className="border-r border-border bg-card/40 px-5 py-6">
        <div className="flex items-center gap-2 text-xs font-medium">
          <Activity className="size-3.5 text-primary" /> 任务流水线
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
                <div className="text-[11px] text-muted-foreground">{s.owner}</div>
              </div>
              {i < stages.length - 1 && (
                <span className="absolute left-2 top-6 h-6 w-px bg-border" />
              )}
            </li>
          ))}
        </ol>

        <div className="mt-8 rounded-lg border border-border bg-background p-3">
          <div className="text-[11px] font-medium text-muted-foreground">
            专家队（{analysis.experts}）
          </div>
          <div className="mt-2 flex flex-wrap gap-1">
            {agentsList.slice(0, analysis.experts).map((a) => (
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
      </aside>

      {/* thought stream */}
      <section className="flex flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-border px-6 py-3 text-xs">
          <span className="inline-flex items-center gap-1.5 font-medium">
            <Wand2 className="size-3.5 text-primary" /> 实时协作思维流
          </span>
          <span className="text-muted-foreground">
            已用 Token <span className="tabular text-foreground">5 994</span> · 5 次调用
          </span>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">
          <AnimatePresence initial={false}>
            {bubbles.map((b, i) => {
              const agent = agentsList.find((a) => a.name === b.who);
              return (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="mb-4 flex gap-3"
                >
                  <div
                    style={{ background: agent?.accent ?? "#888" }}
                    className="grid size-8 shrink-0 place-items-center rounded-full text-xs font-medium text-white"
                  >
                    {agent?.avatar ?? b.who[0]}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 text-[11px]">
                      <span
                        className={`rounded-full px-1.5 py-0.5 font-medium ${kindClass[b.kind]}`}
                      >
                        {kindLabel[b.kind]}
                      </span>
                      <span className="font-medium">{b.who}</span>
                      <span className="text-muted-foreground tabular">+{b.t}</span>
                    </div>
                    <div className="mt-1 rounded-xl rounded-tl-sm border border-border bg-card px-3 py-2 text-sm leading-relaxed">
                      {b.text}
                    </div>
                  </div>
                </motion.div>
              );
            })}
          </AnimatePresence>
          <motion.div
            animate={{ opacity: [0.4, 1, 0.4] }}
            transition={{ duration: 1.4, repeat: Infinity }}
            className="inline-flex items-center gap-2 text-xs text-muted-foreground"
          >
            <span className="size-1.5 rounded-full bg-primary" /> Agent 仍在协作中…
          </motion.div>
        </div>
      </section>

      {/* evidence */}
      <aside className="overflow-y-auto border-l border-border bg-card/40 px-5 py-6">
        <div className="flex items-center gap-2 text-xs font-medium">
          <FileText className="size-3.5 text-primary" /> 证据库（实时）
        </div>
        <div className="relative mt-3">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            placeholder="过滤证据"
            className="h-8 w-full rounded-md border border-border bg-background pl-8 pr-3 text-xs focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="mt-4 space-y-2.5">
          {evidenceFeed.map((e) => (
            <div key={e.id} className="rounded-lg border border-border bg-background p-3">
              <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                <span
                  className={`rounded px-1.5 py-0.5 font-medium ${e.source === "官方" ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"}`}
                >
                  {e.source}
                </span>
                <span className="tabular">{e.id}</span>
                <span>{e.time}</span>
                <span className="tabular">{Math.round(e.conf * 100)}</span>
              </div>
              <div className="mt-1.5 text-xs font-medium leading-snug">{e.title}</div>
              <button className="mt-1 inline-flex items-center gap-1 text-[11px] text-primary hover:underline">
                查看原文 <ExternalLink className="size-3" />
              </button>
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}
