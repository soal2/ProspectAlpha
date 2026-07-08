import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  ArrowRight,
  Paperclip,
  FileText,
  ShieldAlert,
  LineChart,
  Building2,
  Loader2,
  Send,
} from "lucide-react";
import { useState, useEffect, useRef } from "react";
import {
  getAnalyses,
  getAgents,
  getHomepageExamples,
  chatWithAssistant,
  createAnalysisRun,
  type ChatMessage,
  type ChatUnderstanding,
} from "@/lib/data-service";
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

type TerminalStatus = "draft" | "clarifying" | "ready";

const depthOptions = ["快速", "深度", "专家级"];
const ruleOptions = ["默认风险规则", "HK-IPO Taxonomy v2.4"];
const agentOptions = ["默认专家组", "财务 Agent 优先", "法务 Agent 优先", "市场 Agent 优先"];

function inferRequest(text: string) {
  const trimmed = text.trim();
  const companyMatch = trimmed.match(
    /([\u4e00-\u9fa5A-Za-z0-9-]{2,})(?:公司|集团|科技|时代|控股|生物|健康)?/,
  );
  const focus = ["现金流", "对赌", "赎回", "关联交易", "法务", "市场", "估值"].filter((keyword) =>
    trimmed.includes(keyword),
  );
  const hasMaterial =
    /https?:\/\//.test(trimmed) || trimmed.includes("招股书") || trimmed.includes("A1");

  return {
    company: companyMatch?.[0] || "待确认标的公司",
    material: hasMaterial ? "已提供招股书线索" : "未提供招股书材料",
    focus: focus.length > 0 ? focus.join("、") : "待确认分析重点",
  };
}

function WorkspacePage() {
  const navigate = useNavigate();
  const [analyses, setAnalyses] = useState<AnalysisSummary[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [examplesList, setExamplesList] = useState<{ icon: string; title: string; desc: string }[]>(
    [],
  );
  const [loading, setLoading] = useState(true);
  const [sessionTime, setSessionTime] = useState("");
  const [requestText, setRequestText] = useState("");
  const [terminalStatus, setTerminalStatus] = useState<TerminalStatus>("draft");
  const [prospectusMaterial, setProspectusMaterial] = useState("");
  const [analysisDepth, setAnalysisDepth] = useState("专家级");
  const [riskRule, setRiskRule] = useState("默认风险规则");
  const [agentMode, setAgentMode] = useState("默认专家组");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatting, setChatting] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [understanding, setUnderstanding] = useState<ChatUnderstanding | null>(null);
  const [dispatching, setDispatching] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const [sessionId] = useState(() => {
    const now = new Date();
    const hkt = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Hong_Kong" }));
    const dateKey = `${hkt.getFullYear()}${hkt.getMonth()}${hkt.getDate()}`;
    const stored = sessionStorage.getItem(`sid-${dateKey}`);
    if (stored) return stored;
    const prefix = String.fromCharCode(65 + Math.floor(Math.random() * 26));
    const num = String(hkt.getHours()).padStart(2, "0") + String(hkt.getMinutes()).padStart(2, "0");
    const id = `#${prefix}-${num}`;
    sessionStorage.setItem(`sid-${dateKey}`, id);
    return id;
  });

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
  const understood = inferRequest(requestText);
  const companyDisplay = understanding?.company ?? understood.company;
  const materialDisplay =
    understanding?.material_hints ?? (prospectusMaterial || understood.material);
  const focusDisplay = understanding?.focus?.length
    ? understanding.focus.join("、")
    : understood.focus;
  const canEnterAnalysis = messages.length > 0 && !chatting && !dispatching;

  const handleStartCheck = async () => {
    const trimmed = requestText.trim();
    if (!trimmed || chatting) return;
    const userMsg: ChatMessage = { role: "user", content: trimmed };
    const prevMessages = messages;
    const nextMessages = [...prevMessages, userMsg];
    const assistantIdx = nextMessages.length;
    setMessages([...nextMessages, { role: "assistant", content: "" }]);
    setRequestText("");
    setChatting(true);
    setChatError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const result = await chatWithAssistant(
        trimmed,
        prevMessages,
        (_delta, full) => {
          setMessages((cur) => {
            const cp = [...cur];
            cp[assistantIdx] = { role: "assistant", content: full };
            return cp;
          });
        },
        { signal: controller.signal },
      );
      setMessages((cur) => {
        const cp = [...cur];
        cp[assistantIdx] = { role: "assistant", content: result.reply };
        return cp;
      });
      if (result.understanding) setUnderstanding(result.understanding);
      setTerminalStatus("clarifying");
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setChatError(err instanceof Error ? err.message : "对话失败");
      // 回滚占位消息
      setMessages((cur) => cur.filter((_, i) => i !== assistantIdx));
    } finally {
      setChatting(false);
      abortRef.current = null;
    }
  };

  const handleDispatchAnalysis = async () => {
    if (!canEnterAnalysis) return;
    setDispatching(true);
    setChatError(null);
    try {
      const documentText = messages
        .map((m) => `${m.role === "user" ? "[用户]" : "[沈北辰]"} ${m.content}`)
        .join("\n\n");
      const run = await createAnalysisRun({
        company_name: companyDisplay || "未命名标的",
        document_text: documentText,
        structured_data: {
          focus_areas: understanding?.focus?.join("、") ?? "",
          material_hints: understanding?.material_hints ?? "",
          clarifying_questions: understanding?.clarifying_questions?.join("；") ?? "",
          conversation_turns: String(messages.length),
        },
      });
      navigate({ to: "/analyses/$id/orchestra", params: { id: run.run_id } });
    } catch (err) {
      setDispatching(false);
      setChatError(err instanceof Error ? err.message : "派发分析失败");
    }
  };

  const handleAttachMaterial = () => {
    setProspectusMaterial("已选择本地招股书 PDF");
    if (terminalStatus !== "draft") setTerminalStatus("ready");
  };

  const handleMaterialChange = (value: string) => {
    setProspectusMaterial(value);
    if (terminalStatus !== "draft" && value.trim()) setTerminalStatus("ready");
  };

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
                SESSION {sessionId}
              </span>
            </div>
            <textarea
              value={requestText}
              onChange={(event) => setRequestText(event.target.value)}
              placeholder="告诉 AI 你要分析的 IPO 项目、关注风险或招股书线索。AI 会自动判断是否需要补充材料、规则或专家组配置。"
              rows={3}
              className="w-full resize-none bg-transparent px-5 py-4 text-[13.5px] leading-relaxed text-foreground placeholder:text-muted-foreground focus:outline-none"
            />
            <div className="flex flex-wrap items-center gap-3 border-t border-border bg-white px-4 py-3">
              <span className="text-[11px] text-muted-foreground">
                先描述问题，AI 会识别缺失信息并生成补充问答
              </span>
              <div className="ml-auto flex items-center gap-2">
                <button
                  onClick={handleAttachMaterial}
                  className="grid size-9 place-items-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground"
                  aria-label="选择招股书材料"
                >
                  <Paperclip className="size-4" />
                </button>
                <button
                  onClick={handleStartCheck}
                  disabled={chatting || !requestText.trim()}
                  className="inline-flex h-9 items-center gap-2 rounded-sm bg-[color:var(--gold)] px-4 text-[13px] font-semibold text-[color:var(--navy-deep)] transition hover:bg-[#b8934b] disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {chatting ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Send className="size-3.5" />
                  )}
                  {chatting ? "沈北辰思考中…" : "启动分析"}
                </button>
              </div>
            </div>
          </div>
          {terminalStatus !== "draft" && (
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.28 }}
              className="mt-4 overflow-hidden rounded-md border border-border bg-white text-left soft-ring"
            >
              <div className="grid gap-0 lg:grid-cols-[0.9fr_1.1fr]">
                <div className="border-b border-border bg-[color:var(--muted)]/35 p-4 lg:border-b-0 lg:border-r">
                  <div className="eyebrow text-[color:var(--navy)]/60">AI 已理解</div>
                  <div className="mt-3 space-y-3 text-sm">
                    <div>
                      <div className="text-[11px] text-muted-foreground">分析对象</div>
                      <div className="mt-0.5 font-medium text-[color:var(--navy)]">
                        {companyDisplay || "待确认标的公司"}
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] text-muted-foreground">材料线索</div>
                      <div className="mt-0.5 font-medium text-[color:var(--navy)]">
                        {materialDisplay}
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] text-muted-foreground">关注重点</div>
                      <div className="mt-0.5 font-medium text-[color:var(--navy)]">
                        {focusDisplay}
                      </div>
                    </div>
                    {understanding?.clarifying_questions?.length ? (
                      <div>
                        <div className="text-[11px] text-muted-foreground">待澄清</div>
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                          {understanding.clarifying_questions.map((q) => (
                            <span
                              key={q}
                              className="rounded-sm bg-white px-2 py-1 text-[11px] text-[color:var(--navy)] ring-1 ring-[color:var(--gold)]/30"
                            >
                              {q}
                            </span>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>
                </div>

                <div className="p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="eyebrow text-[color:var(--navy)]/60">还需要补充</div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        只补必要信息；未选择项将使用系统默认方案
                      </p>
                    </div>
                    <span
                      className={`rounded-sm px-2 py-1 text-[11px] font-medium ${
                        canEnterAnalysis
                          ? "bg-[color:var(--risk-low)]/10 text-[color:var(--risk-low)]"
                          : "bg-[color:var(--risk-mid)]/12 text-[oklch(0.45_0.12_75)]"
                      }`}
                    >
                      {canEnterAnalysis
                        ? "可派发 Agent 协同"
                        : chatting
                          ? "沈北辰思考中…"
                          : "等待对话"}
                    </span>
                  </div>

                  <div className="mt-4 space-y-4">
                    <div>
                      <label className="text-[11px] font-medium text-muted-foreground">
                        招股书材料
                      </label>
                      <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                        <input
                          value={prospectusMaterial}
                          onChange={(event) => handleMaterialChange(event.target.value)}
                          placeholder="粘贴招股书链接，或点击附件选择 PDF"
                          className="h-9 flex-1 rounded-sm border border-border bg-background px-3 text-xs focus:outline-none focus:ring-2 focus:ring-ring"
                        />
                        <button
                          onClick={handleAttachMaterial}
                          className="inline-flex h-9 items-center justify-center gap-1.5 rounded-sm border border-border bg-background px-3 text-xs font-medium hover:bg-muted"
                        >
                          <Paperclip className="size-3.5" /> 选择 PDF
                        </button>
                      </div>
                    </div>

                    <QuestionOptions
                      label="分析深度"
                      value={analysisDepth}
                      options={depthOptions}
                      onChange={setAnalysisDepth}
                    />
                    <QuestionOptions
                      label="风险规则"
                      value={riskRule}
                      options={ruleOptions}
                      onChange={setRiskRule}
                    />
                    <QuestionOptions
                      label="Agent 使用"
                      value={agentMode}
                      options={agentOptions}
                      onChange={setAgentMode}
                    />
                  </div>

                  <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
                    <span className="text-[11px] text-muted-foreground">
                      当前方案：{analysisDepth} · {riskRule} · {agentMode}
                    </span>
                    <button
                      onClick={handleDispatchAnalysis}
                      disabled={!canEnterAnalysis}
                      className="inline-flex h-9 items-center gap-2 rounded-sm bg-[color:var(--navy)] px-4 text-xs font-semibold text-white transition hover:bg-[color:var(--navy-deep)] disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground"
                    >
                      {dispatching ? (
                        <>
                          <Loader2 className="size-3.5 animate-spin" /> 派发中…
                        </>
                      ) : (
                        <>
                          进入分析流程 <ArrowRight className="size-3.5" />
                        </>
                      )}
                    </button>
                  </div>
                </div>
              </div>
            </motion.div>
          )}
          {messages.length > 0 && (
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.28 }}
              className="mt-4 overflow-hidden rounded-md border border-border bg-white text-left soft-ring"
            >
              <div className="flex items-center justify-between border-b border-border bg-[color:var(--muted)]/40 px-4 py-2.5">
                <div className="flex items-center gap-2">
                  <span className="size-1.5 rounded-full bg-[color:var(--gold)]" />
                   <span className="eyebrow text-[color:var(--navy)]/70">对话历史 · 沈北辰</span>
                </div>
                <span className="tabular text-[10px] uppercase tracking-wider text-muted-foreground">
                  {messages.length} turn{messages.length > 1 ? "s" : ""}
                </span>
              </div>
              <div className="max-h-80 space-y-2.5 overflow-y-auto px-4 py-3">
                {messages.map((m, i) => (
                  <div
                    key={i}
                    className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
                  >
                    <div
                      className={`max-w-[80%] whitespace-pre-wrap rounded-md px-3 py-2 text-[12.5px] leading-relaxed ${
                        m.role === "user"
                          ? "bg-[color:var(--navy)] text-white"
                          : "bg-[color:var(--muted)] text-foreground"
                      }`}
                    >
                      {m.content || "…"}
                    </div>
                  </div>
                ))}
                {chatting && (
                  <div className="flex justify-start">
                    <div className="flex items-center gap-1.5 rounded-md bg-[color:var(--muted)] px-3 py-2 text-[11px] text-muted-foreground">
                      <span className="size-1.5 animate-pulse rounded-full bg-primary" />
                      生成中…
                    </div>
                  </div>
                )}
              </div>
            </motion.div>
          )}
          {chatError && (
            <div className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-[11.5px] text-destructive">
              {chatError}
            </div>
          )}
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

function QuestionOptions({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  return (
    <div>
      <div className="text-[11px] font-medium text-muted-foreground">{label}</div>
      <div className="mt-2 flex flex-wrap gap-2">
        {options.map((option) => (
          <button
            key={option}
            onClick={() => onChange(option)}
            className={`rounded-sm border px-3 py-1.5 text-xs transition ${
              value === option
                ? "border-[color:var(--gold)] bg-[color:var(--gold)]/10 text-[color:var(--navy)]"
                : "border-border bg-background text-muted-foreground hover:bg-muted"
            }`}
          >
            {option}
          </button>
        ))}
      </div>
    </div>
  );
}
