import { createFileRoute, Link } from "@tanstack/react-router";
import {
  ArrowRight,
  Paperclip,
  Play,
  Zap,
  GraduationCap,
  Cpu,
  FileText,
  ShieldAlert,
  LineChart,
  Building2,
} from "lucide-react";
import { useState, useEffect } from "react";
import { getAnalyses, getAgents, getHomepageExamples } from "@/lib/data-service";
import { type AnalysisSummary, type Agent } from "@/mocks";
import { motion } from "framer-motion";

const iconMap: Record<string, typeof Building2> = {
  Building2,
  ShieldAlert,
  FileText,
  LineChart,
};

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "工作台 · ProspectAlpha" },
      {
        name: "description",
        content:
          "ProspectAlpha — 机构级港股 IPO 风险智能终端：招股书解析、风险归因、链路回溯与上市后预警。",
      },
    ],
  }),
  component: WorkspacePage,
});

const examples = [
  {
    icon: Building2,
    title: "新能源车 IPO 交叉审视",
    desc: "对比理想 / 极氪 / 蔚来招股书的定价博弈、现金 runway 与股东权利结构。",
  },
  {
    icon: ShieldAlert,
    title: "对赌与赎回条款扫描",
    desc: "全量识别特殊权利、回购义务、赎回触发条款与实控人变更风险。",
  },
  {
    icon: FileText,
    title: "财务健康度深度诊断",
    desc: "解析三大报表，量化经营现金流、造血能力与资产减值敞口。",
  },
  {
    icon: LineChart,
    title: "破发概率预警建模",
    desc: "结合上市前情绪、同业发行表现与市场流动性估算破发概率区间。",
  },
];

function WorkspacePage() {
  const [analyses, setAnalyses] = useState<AnalysisSummary[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [examplesList, setExamplesList] = useState<{ icon: string; title: string; desc: string }[]>(
    [],
  );
  const [loading, setLoading] = useState(true);
  const [sessionTime, setSessionTime] = useState("");

  useEffect(() => {
    let cancelled = false;
    Promise.all([getAnalyses(), getAgents(), getHomepageExamples()]).then(([a, ag, ex]) => {
      if (!cancelled) {
        setAnalyses(a);
        setAgents(ag);
        setExamplesList(ex);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const tick = () => {
      const now = new Date();
      const hkt = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Hong_Kong" }));
      const hours = String(hkt.getHours()).padStart(2, "0");
      const minutes = String(hkt.getMinutes()).padStart(2, "0");
      setSessionTime(`${hours}:${minutes}`);
    };
    tick();
    const interval = setInterval(tick, 60000);
    return () => clearInterval(interval);
  }, []);

  const featured = analyses.slice(0, 12);
  return (
    <div className="relative isolate">
      {/* subtle navy grid backdrop, no gradient warmth */}
      <div className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[560px] terminal-grid opacity-70" />
      <div className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[560px] bg-gradient-to-b from-white via-white/60 to-transparent" />
      {/* gold thin ring decoration */}
      <div className="pointer-events-none absolute right-8 top-16 -z-10 hidden lg:block">
        <div className="size-56 rounded-full border border-[color:var(--gold)]/30" />
        <div className="absolute inset-6 rounded-full border border-[color:var(--gold)]/15" />
      </div>

      <div className="mx-auto max-w-6xl px-6 pb-20 pt-10 sm:pt-14">
        {/* eyebrow badge */}
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center gap-2 rounded-sm border border-[color:var(--gold)]/40 bg-white px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.2em] text-[color:var(--navy)]">
            <span className="size-1.5 rounded-full bg-[color:var(--gold)]" />
            Live · Decision Replay v2
          </span>
          <span className="hidden text-[11px] uppercase tracking-[0.2em] text-muted-foreground sm:inline">
            Session · Soal2 · HKT {sessionTime || "--:--"}
          </span>
        </div>

        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="mt-10 text-center"
        >
          <h1 className="mx-auto max-w-4xl font-serif text-[40px] font-semibold leading-[1.1] tracking-tight text-[color:var(--navy)] sm:text-[52px]">
            Institutional IPO
            <br className="hidden sm:block" /> Risk Intelligence
          </h1>
          <div className="mx-auto mt-6 h-px w-16 bg-[color:var(--gold)]" />
          <p className="mx-auto mt-6 max-w-2xl text-[13px] leading-relaxed text-muted-foreground sm:text-sm">
            面向港股 IPO 的招股书解析、风险归因与上市后预警系统 · 证据先行 · 链路可追溯 · 回测可验证
          </p>
        </motion.div>

        {/* terminal composer */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1, duration: 0.5 }}
          className="mx-auto mt-10 max-w-3xl"
        >
          <div className="soft-ring overflow-hidden rounded-md border border-border bg-white">
            <div className="flex items-center justify-between border-b border-border/80 bg-[color:var(--muted)]/60 px-4 py-2">
              <div className="flex items-center gap-2">
                <span className="size-1.5 rounded-full bg-[color:var(--gold)]" />
                <span className="eyebrow text-[color:var(--navy)]/70">Analysis Terminal</span>
              </div>
              <span className="tabular text-[10px] uppercase tracking-wider text-muted-foreground">
                SESSION #A-24710
              </span>
            </div>
            <textarea
              placeholder="输入港股代码（如 03750.HK）、公司名或粘贴招股书 URL — 例：解析宁德时代招股书之现金流与对赌条款"
              rows={3}
              className="w-full resize-none bg-transparent px-5 py-4 text-[13.5px] leading-relaxed text-foreground placeholder:text-muted-foreground focus:outline-none"
            />
            <div className="flex flex-wrap items-center gap-3 border-t border-border bg-white px-4 py-3">
              <span className="eyebrow text-muted-foreground">Mode</span>
              {/* segmented control */}
              <div className="inline-flex overflow-hidden rounded-sm border border-border">
                <ModeChip icon={Zap} label="快速" />
                <ModeChip icon={Cpu} label="深度" />
                <ModeChip icon={GraduationCap} label="专家级" active />
              </div>
              <span className="hidden text-[11px] text-muted-foreground sm:inline">
                12+ 章节 · ~6-8 min · 全量证据 + 回测
              </span>
              <div className="ml-auto flex items-center gap-2">
                <button className="grid size-9 place-items-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground">
                  <Paperclip className="size-4" />
                </button>
                <button className="inline-flex h-9 items-center gap-2 rounded-sm bg-[color:var(--gold)] px-4 text-[13px] font-semibold text-[color:var(--navy-deep)] transition hover:bg-[#b8934b]">
                  <Play className="size-3.5 fill-current" />
                  启动分析
                </button>
              </div>
            </div>
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
            <span>12 位 Agent 专家协同</span>
            <span className="text-[color:var(--gold)]/60">|</span>
            <span>真实联网检索</span>
            <span className="text-[color:var(--gold)]/60">|</span>
            <span>证据先行 · 无证据不立论</span>
            <span className="text-[color:var(--gold)]/60">|</span>
            <span>SHAP / 回测双通道校验</span>
          </div>
        </motion.div>

        {/* scenario dashboard cards */}
        {/* <div className="mt-16">
          <div className="flex items-end justify-between">
            <div>
              <div className="eyebrow text-muted-foreground">Institutional Scenarios</div>
              <h2 className="mt-1 font-serif text-xl font-semibold text-[color:var(--navy)]">
                典型分析场景
              </h2>
            </div>
            <span className="text-[11px] tabular text-muted-foreground">04 templates</span>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {examplesList.map((e, i) => (
              <Link
                key={e.title}
                to="/intake"
                className="group relative overflow-hidden rounded-md border border-border bg-white p-5 transition hover:border-[color:var(--navy)]/40"
              >
                <span className="absolute inset-y-0 left-0 w-[2px] scale-y-0 bg-[color:var(--gold)] transition-transform duration-300 group-hover:scale-y-100" />
                <div className="flex items-start justify-between">
                  <div className="grid size-9 place-items-center rounded-sm bg-[color:var(--navy)] ring-1 ring-[color:var(--gold)]/30">
                    {(() => {
                      const Icon = iconMap[e.icon];
                      return <Icon className="size-4 text-[color:var(--gold)]" />;
                    })()}
                  </div>
                  <span className="tabular text-[10px] uppercase tracking-wider text-muted-foreground">
                    0{i + 1}
                  </span>
                </div>
                <div className="mt-4 text-[14px] font-semibold text-[color:var(--navy)]">
                  {e.title}
                </div>
                <div className="mt-1.5 line-clamp-3 text-[12px] leading-relaxed text-muted-foreground">
                  {e.desc}
                </div>
                <div className="mt-4 flex items-center justify-between border-t border-border/80 pt-3 text-[11px] text-muted-foreground">
                  <span>Template</span>
                  <ArrowRight className="size-3.5 text-[color:var(--navy)] transition group-hover:translate-x-0.5" />
                </div>
              </Link>
            ))}
          </div>
        </div> */}

        {/* expert bar */}
        <div className="mt-14 flex flex-col items-center gap-3">
          <div className="eyebrow text-muted-foreground">Expert Guild · 12 位专家</div>
          <div className="flex -space-x-1.5">
            {agents.slice(0, 10).map((a) => (
              <div
                key={a.id}
                title={`${a.name} · ${a.role}`}
                style={{ background: a.accent }}
                className="grid size-7 place-items-center rounded-sm text-[11px] font-medium text-white ring-1 ring-[color:var(--gold)]/40"
              >
                {a.avatar}
              </div>
            ))}
            <Link
              to="/experts"
              className="ml-3 inline-flex items-center rounded-sm border border-[color:var(--gold)]/50 bg-white px-3 text-[11px] font-medium text-[color:var(--navy)] hover:bg-[color:var(--gold)]/10"
            >
              查看全部 →
            </Link>
          </div>
        </div>

        {/* recent analyses */}
        <div className="mt-16">
          <div className="flex items-end justify-between">
            <div>
              <div className="eyebrow text-muted-foreground">Recent Analyses</div>
              <h2 className="mt-1 font-serif text-xl font-semibold text-[color:var(--navy)]">
                近期分析
              </h2>
            </div>
            <Link
              to="/analyses"
              className="text-[12px] font-medium text-[color:var(--navy)] hover:text-[color:var(--gold)]"
            >
              查看全部 →
            </Link>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {featured.slice(0, 3).map((a) => (
              <Link
                key={a.id}
                to="/analyses/$id/report"
                params={{ id: a.id }}
                className="group relative overflow-hidden rounded-md border border-border bg-white transition hover:border-[color:var(--navy)]/40"
              >
                <span
                  className="block h-[3px] w-full"
                  style={{
                    background:
                      a.riskLevel === "high"
                        ? "var(--risk-high)"
                        : a.riskLevel === "mid"
                          ? "var(--risk-mid)"
                          : "var(--risk-low)",
                  }}
                />
                <div className="p-4">
                  <div className="flex items-center justify-between text-[10.5px] uppercase tracking-wider text-muted-foreground">
                    <span className="tabular text-[color:var(--navy)]">{a.code}</span>
                    <span className="tabular">{a.createdAt}</span>
                  </div>
                  <div className="mt-2 text-[14px] font-semibold leading-snug text-[color:var(--navy)]">
                    {a.name} <span className="text-muted-foreground">· IPO 风险报告</span>
                  </div>
                  <div className="mt-1 text-[11.5px] text-muted-foreground">
                    {a.industry} · {a.experts} 位专家 ·{" "}
                    {a.mode === "expert" ? "专家级" : a.mode === "deep" ? "深度" : "快速"}
                  </div>
                  <div className="mt-4 flex items-center justify-between border-t border-border/80 pt-3 text-[11px]">
                    <div className="flex gap-4 tabular text-muted-foreground">
                      <span>
                        <span className="text-[color:var(--navy)]">{a.evidenceCount}</span> 证据
                      </span>
                      <span>
                        <span className="text-[color:var(--navy)]">{a.highConfidence}</span> 高置信
                      </span>
                    </div>
                    <span className="font-medium text-[color:var(--navy)] transition group-hover:text-[color:var(--gold)]">
                      Open →
                    </span>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function ModeChip({
  icon: Icon,
  label,
  active,
}: {
  icon: typeof Zap;
  label: string;
  active?: boolean;
}) {
  return (
    <button
      className={`inline-flex items-center gap-1.5 border-r border-border px-3 py-1.5 text-[12px] font-medium transition last:border-r-0 ${
        active
          ? "bg-[color:var(--navy)] text-white"
          : "bg-white text-[color:var(--navy)] hover:bg-muted"
      }`}
    >
      <Icon className="size-3.5" /> {label}
    </button>
  );
}
