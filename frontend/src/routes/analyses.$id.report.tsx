import { createFileRoute, getRouteApi, Link } from "@tanstack/react-router";
import { useState } from "react";
import {
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  RadarChart,
  Radar,
  PolarAngleAxis,
  PolarGrid,
  Tooltip,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Legend,
  LineChart as RLineChart,
  Line,
} from "recharts";
import {
  Network,
  Database,
  Edit3,
  Download,
  FileBadge,
  Sparkles,
  Lightbulb,
  Quote,
  Layers3,
  AlertTriangle,
  ChevronRight,
} from "lucide-react";
import { getReportData } from "@/lib/data-service";
import { RiskBadge } from "@/components/risk-badge";

export const Route = createFileRoute("/analyses/$id/report")({
  loader: async ({ params }) => {
    const report = await getReportData(params.id);
    return { report };
  },
  component: ReportPage,
  head: () => ({ meta: [{ title: "IPO 风险报告 · Verda" }] }),
});

function ReportPage() {
  const api = getRouteApi("/analyses/$id");
  const { analysis } = api.useLoaderData();
  const { report } = Route.useLoaderData() as {
    report: {
      toc: string[];
      radar: { dim: string; score: number }[];
      pie: { name: string; value: number }[];
      waterfall: { name: string; value: number }[];
      cashflow: { y: string; cfo: number; ni: number; cap: number }[];
      palette: string[];
    } | null;
  };
  const [active, setActive] = useState(0);

  const toc = report?.toc ?? [];
  const radar = report?.radar ?? [];
  const pie = report?.pie ?? [];
  const waterfall = report?.waterfall ?? [];
  const cashflow = report?.cashflow ?? [];
  const palette = report?.palette ?? [];

  return (
    <div className="grid h-[calc(100vh-3.5rem-14.5rem)] min-h-[640px] grid-cols-[260px_1fr_360px]">
      {/* TOC */}
      <aside className="overflow-y-auto border-r border-border bg-card/40 px-4 py-6">
        <div className="px-2">
          <div className="font-serif text-base font-semibold">报告目录</div>
          <div className="mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
            <span>阅读进度</span>
            <span className="tabular">{Math.round(((active + 1) / toc.length) * 100)}%</span>
          </div>
          <div className="mt-1 h-1 overflow-hidden rounded-full bg-muted">
            <div
              className="h-1 rounded-full bg-primary transition-all"
              style={{ width: `${((active + 1) / toc.length) * 100}%` }}
            />
          </div>
        </div>
        <nav className="mt-5 space-y-0.5">
          {toc.map((t, i) => (
            <button
              key={t}
              onClick={() => setActive(i)}
              className={`flex w-full items-start gap-2 rounded-md px-3 py-2 text-left text-sm transition ${active === i ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-accent"}`}
            >
              <span
                className={`grid size-5 shrink-0 place-items-center rounded-full text-[10px] font-semibold tabular ${active === i ? "bg-primary text-primary-foreground" : "bg-muted"}`}
              >
                {i + 1}
              </span>
              <span className="leading-snug">{t}</span>
            </button>
          ))}
        </nav>
        <button className="mt-6 w-full rounded-lg bg-primary/10 px-3 py-2.5 text-xs font-medium text-primary hover:bg-primary/15">
          <Network className="mr-1 inline size-3.5" /> 风险知识图谱
        </button>
      </aside>

      {/* main */}
      <section className="overflow-y-auto bg-paper">
        {/* Hero */}
        <header className="relative border-b border-border">
          <div className="absolute inset-0 bg-gradient-to-br from-[color:var(--sage-soft)]/60 to-[color:var(--amber-soft)]/30 grid-paper opacity-80" />
          <div className="relative px-8 py-8">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0 max-w-3xl">
                <h1 className="font-serif text-3xl font-semibold leading-tight">
                  {analysis.name} · IPO 风险分析报告
                </h1>
                <p className="mt-2 text-sm text-muted-foreground">
                  基于 {analysis.evidenceCount} 条联网证据 · 55 个独立来源 · {analysis.experts}{" "}
                  位专家协作生成 · 专家级模式
                </p>
                <p className="mt-1 text-xs text-muted-foreground tabular">
                  {analysis.code} · 上市 {analysis.listingDate} · 发行价 HK$ {analysis.offerPrice} ·
                  发行规模 {analysis.offerSize}
                </p>
              </div>
              <div className="flex shrink-0 gap-1.5">
                <ToolBtn
                  icon={Network}
                  label="决策链路"
                  to="/analyses/$id/trace"
                  id={analysis.id}
                />
                <ToolBtn
                  icon={Database}
                  label="证据库"
                  to="/analyses/$id/evidence"
                  id={analysis.id}
                />
                <ToolBtn icon={Edit3} label="编辑" />
                <ToolBtn icon={Download} label="导出" />
              </div>
            </div>

            <div className="mt-6 grid gap-3 sm:grid-cols-4">
              <HeroStat icon={Sparkles} label="核心结论" value="14 条" />
              <HeroStat icon={Quote} label="联网证据" value={`${analysis.evidenceCount} 条`} />
              <HeroStat icon={Layers3} label="独立信源" value="55 个" />
              <HeroStat
                icon={FileBadge}
                label="高置信占比"
                value={`${Math.round((analysis.highConfidence / analysis.evidenceCount) * 100)}%`}
              />
            </div>
          </div>
        </header>

        <div className="px-8 py-8">
          {/* 1. Executive summary */}
          <Chapter num="1" title={toc[0]}>
            <Callout>
              <p>
                本次 IPO 综合风险评分{" "}
                <span className="font-serif text-2xl font-semibold text-foreground tabular">
                  {analysis.riskScore}
                </span>{" "}
                / 100，归类为 <RiskBadge level={analysis.riskLevel} className="align-middle" />。
                主要风险来源于 <Hl>对赌赎回条款</Hl> 与 <Hl>经营性现金流连续下滑</Hl>；正向因素是{" "}
                <Hl>现金 runway 充足 ≥ 6 年</Hl> 与 <Hl>头部市场份额稳固</Hl>。
              </p>
            </Callout>

            <h4 className="mt-6 font-serif text-base font-semibold">四大维度结构性判断</h4>
            <ul className="mt-3 space-y-2 text-sm leading-relaxed">
              <li>
                · <Hl>财务面</Hl>：净利润仍然增长，但经营性现金流 2024 年同比{" "}
                <span className="tabular">−18.6%</span>，应收账款与存货周转走差
                <RefChip id="e_898aa168" />。
              </li>
              <li>
                · <Hl>经营面</Hl>：客户结构高度集中，前五大客户营收占比{" "}
                <span className="tabular">68.2%</span>，与同业差距 14 pct
                <RefChip id="e_5e8c343e" />。
              </li>
              <li>
                · <Hl>法律面</Hl>：发现一项硬性回购触发条款（投资人 A），若 12
                个月内未达营收里程碑将触发
                <RefChip id="e_1cdca7b3" />。
              </li>
              <li>
                · <Hl>市场面</Hl>：上市前 30 日板块跌幅 −2.4%、近期 IPO 破发率 31%，市场情绪偏中性
                <RefChip id="e_a4111a75" />。
              </li>
            </ul>
          </Chapter>

          {/* 2. Company */}
          <Chapter num="2" title={toc[1]}>
            <div className="grid gap-4 sm:grid-cols-2">
              <KvBlock
                items={[
                  { k: "公司全称", v: "宁德时代新能源科技股份有限公司" },
                  { k: "总部", v: "福建省宁德市" },
                  { k: "成立日期", v: "2011-12-16" },
                  { k: "上市板块", v: "港交所主板" },
                  { k: "保荐人", v: "JPMorgan · 中金公司" },
                  { k: "审计机构", v: "安永会计师事务所" },
                ]}
              />
              <KvBlock
                items={[
                  { k: "发行价", v: "HK$ 263.00" },
                  { k: "发行规模", v: "HK$ 410.1 亿" },
                  { k: "公开发售比例", v: "10%" },
                  { k: "国际配售比例", v: "90%" },
                  { k: "基石投资人", v: "高瓴、淡马锡、HHLR 等 12 家" },
                  { k: "首发市值", v: "约 1.21 万亿港元" },
                ]}
              />
            </div>
          </Chapter>

          {/* 3. Financial */}
          <Chapter num="3" title={toc[2]}>
            <p className="text-sm leading-relaxed">
              公司近 4 年净利润持续增长，但 <Hl>经营性现金流自 2024 年起首次出现负向拐点</Hl>
              。考虑到资本支出仍在高速扩张，自由现金流压力将进一步上升。
            </p>
            <div className="mt-4 h-64 w-full rounded-xl border border-border bg-card p-4">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={cashflow}>
                  <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="y" stroke="var(--muted-foreground)" fontSize={12} />
                  <YAxis stroke="var(--muted-foreground)" fontSize={12} />
                  <Tooltip
                    contentStyle={{
                      background: "var(--card)",
                      border: "1px solid var(--border)",
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="ni" name="净利润" fill={palette[0]} radius={[4, 4, 0, 0]} />
                  <Bar dataKey="cfo" name="经营现金流" fill={palette[1]} radius={[4, 4, 0, 0]} />
                  <Bar dataKey="cap" name="资本支出" fill={palette[3]} radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Chapter>

          {/* 4. Market share */}
          <Chapter num="4" title={toc[3]}>
            <p className="text-sm leading-relaxed">
              全球动力电池市场份额估算（分析师推断 · 来源：行业协会、各厂披露、Wind 数据）
            </p>
            <div className="mt-4 grid gap-4 sm:grid-cols-[1.2fr_1fr]">
              <div className="h-64 rounded-xl border border-border bg-card p-4">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={pie}
                      dataKey="value"
                      innerRadius={50}
                      outerRadius={88}
                      paddingAngle={2}
                    >
                      {pie.map((_, i) => (
                        <Cell key={i} fill={palette[i % palette.length]} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        background: "var(--card)",
                        border: "1px solid var(--border)",
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                    />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="rounded-xl border border-border bg-card p-4 text-sm">
                <div className="font-medium">客户集中度</div>
                <ul className="mt-3 space-y-2 text-xs text-muted-foreground">
                  <li className="flex justify-between">
                    <span>前 5 大客户营收占比</span>
                    <span className="tabular font-medium text-foreground">68.2%</span>
                  </li>
                  <li className="flex justify-between">
                    <span>第一大客户营收占比</span>
                    <span className="tabular font-medium text-foreground">22.4%</span>
                  </li>
                  <li className="flex justify-between">
                    <span>同业均值</span>
                    <span className="tabular">54.1%</span>
                  </li>
                  <li className="flex justify-between">
                    <span>风险评级</span>
                    <RiskBadge level="mid" />
                  </li>
                </ul>
              </div>
            </div>
          </Chapter>

          {/* 12. Radar scorecard */}
          <Chapter num="12" title={toc[11]}>
            <p className="text-sm leading-relaxed">
              规则评分卡按 6
              个维度对公司进行画像，越靠近外圈代表该维度风险越高。瀑布图展示各风险因子对最终分数的贡献。
            </p>
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <div className="h-72 rounded-xl border border-border bg-card p-4">
                <ResponsiveContainer width="100%" height="100%">
                  <RadarChart data={radar}>
                    <PolarGrid stroke="var(--border)" />
                    <PolarAngleAxis
                      dataKey="dim"
                      tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                    />
                    <Radar
                      dataKey="score"
                      stroke={palette[0]}
                      fill={palette[0]}
                      fillOpacity={0.35}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "var(--card)",
                        border: "1px solid var(--border)",
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                    />
                  </RadarChart>
                </ResponsiveContainer>
              </div>
              <div className="h-72 rounded-xl border border-border bg-card p-4">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={waterfall} layout="vertical" margin={{ left: 12 }}>
                    <CartesianGrid
                      stroke="var(--border)"
                      strokeDasharray="3 3"
                      horizontal={false}
                    />
                    <XAxis type="number" stroke="var(--muted-foreground)" fontSize={11} />
                    <YAxis
                      type="category"
                      dataKey="name"
                      stroke="var(--muted-foreground)"
                      fontSize={11}
                      width={110}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "var(--card)",
                        border: "1px solid var(--border)",
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                    />
                    <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                      {waterfall.map((d, i) => (
                        <Cell
                          key={i}
                          fill={
                            d.value < 0
                              ? `oklch(${"var(--risk-low)"})`
                              : d.name === "= 综合得分"
                                ? palette[0]
                                : `oklch(${"var(--risk-mid)"})`
                          }
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </Chapter>

          {/* 13. SHAP */}
          <Chapter num="13" title={toc[12]}>
            <p className="text-sm leading-relaxed">
              ML 模型（RiskAgent v2.4 · LGBM + 规则混合）输出破发概率{" "}
              <span className="font-serif text-xl font-semibold tabular">0.34</span>，对应 Top 30%
              风险分位。下方展示对该结论的特征贡献排序。
            </p>
            <div className="mt-4 space-y-2">
              {[
                { f: "对赌赎回触发条款", v: 0.22, dir: "+" },
                { f: "客户集中度 (Top5)", v: 0.16, dir: "+" },
                { f: "经营现金流同比", v: 0.13, dir: "+" },
                { f: "现金 runway 年", v: 0.11, dir: "−" },
                { f: "行业景气 30d", v: 0.08, dir: "+" },
                { f: "近期 IPO 破发率", v: 0.07, dir: "+" },
                { f: "基石认购占比", v: 0.06, dir: "−" },
              ].map((s) => (
                <div key={s.f} className="flex items-center gap-3 text-xs">
                  <span className="w-40 shrink-0 text-muted-foreground">{s.f}</span>
                  <div className="flex-1 h-3 rounded-full bg-muted overflow-hidden">
                    <div
                      className={`h-full ${s.dir === "+" ? "bg-[color:var(--risk-high)]" : "bg-[color:var(--risk-low)]"}`}
                      style={{ width: `${s.v * 100 * 4}%` }}
                    />
                  </div>
                  <span className="w-12 tabular text-right">
                    {s.dir}
                    {s.v.toFixed(2)}
                  </span>
                </div>
              ))}
            </div>
          </Chapter>

          {/* 14. recommendation */}
          <Chapter num="14" title={toc[13]}>
            <Callout tone="amber">
              <div className="flex gap-3">
                <AlertTriangle className="size-5 shrink-0 text-[oklch(0.45_0.12_75)]" />
                <div>
                  <p className="font-medium">
                    投研建议 · 申购评级：
                    <span className="text-[oklch(0.45_0.12_75)]">中性偏谨慎</span>
                  </p>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-relaxed">
                    <li>建议在 IPO 簿记阶段控制配售比例 ≤ 0.5×；</li>
                    <li>上市 T+5 日内重点观察现金流业绩指引兑现度；</li>
                    <li>跟踪「投资人 A 回购触发器」12 个月窗口期；</li>
                    <li>对客户集中度变化做季度复核。</li>
                  </ul>
                </div>
              </div>
            </Callout>
          </Chapter>
        </div>
      </section>

      {/* annotations */}
      <aside className="overflow-y-auto border-l border-border bg-card/40 px-5 py-6">
        <div className="flex items-center justify-between text-xs">
          <div className="flex items-center gap-2 font-medium">
            <Lightbulb className="size-3.5 text-primary" /> 知识库 · 标注
          </div>
          <button className="rounded-full bg-primary px-2.5 py-0.5 text-[11px] text-primary-foreground">
            + 标注
          </button>
        </div>
        <div className="mt-4 space-y-3 text-xs">
          <Note tone="risk">
            <p>
              「经营性现金流连续下滑」与年报口径一致，但应收账款增加 41% —
              需要拆分会计政策口径与真实现金转化。
            </p>
            <span className="mt-2 inline-flex items-center gap-1 text-[10px] text-muted-foreground">
              — 周翊 · 红队 · 待复核
            </span>
          </Note>
          <Note tone="info">
            <p>
              投资人 A 的回购触发器在「股东协议」第 5.2.3 条；港交所要求 IPO
              前需解除，但本次披露文件中保留了 12 个月观察期。
            </p>
            <span className="mt-2 inline-flex items-center gap-1 text-[10px] text-muted-foreground">
              — 温叙白 · 法务
            </span>
          </Note>
          <Note tone="ok">
            <p>现金 runway 6.4 年远超 1.5 年高风险线，可对冲短期现金流压力。</p>
            <span className="mt-2 inline-flex items-center gap-1 text-[10px] text-muted-foreground">
              — 蔡宝 · 财务
            </span>
          </Note>
        </div>

        <div className="mt-6 text-xs">
          <div className="font-medium">证据来源（{312}）</div>
          <div className="mt-3 space-y-2">
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="rounded-md border border-border bg-background p-2.5">
                <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                  <span
                    className={`rounded px-1.5 py-0.5 font-medium ${i === 1 ? "bg-primary/10 text-primary" : "bg-muted"}`}
                  >
                    {i === 1 ? "官方" : "web"}
                  </span>
                  <span className="tabular">#{i}</span>
                  <span className="ml-auto tabular">可信度 {[93, 86, 81, 72, 58][i - 1]}</span>
                </div>
                <div className="mt-1 truncate text-[11px] font-medium">
                  {
                    [
                      "招股书 P.218 现金流量表",
                      "海通证券 · 深度报告",
                      "公司公告 · 关联交易",
                      "Wind · 同业对标",
                      "行业协会发布",
                    ][i - 1]
                  }
                </div>
              </div>
            ))}
            <button className="w-full text-center text-[11px] text-primary hover:underline">
              查看全部 312 条 →
            </button>
          </div>
        </div>
      </aside>
    </div>
  );
}

function Chapter({
  num,
  title,
  children,
}: {
  num: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-10 scroll-mt-20">
      <div className="mb-3 flex items-baseline gap-3">
        <span className="font-serif text-3xl font-semibold text-primary tabular">{num}</span>
        <h2 className="font-serif text-xl font-semibold">{title}</h2>
      </div>
      {children}
    </section>
  );
}

function Callout({
  children,
  tone = "sage",
}: {
  children: React.ReactNode;
  tone?: "sage" | "amber";
}) {
  return (
    <div
      className={`rounded-xl border p-4 text-sm leading-relaxed ${tone === "sage" ? "border-primary/20 bg-[color:var(--sage-soft)]/40" : "border-[color:var(--risk-mid)]/30 bg-[color:var(--amber-soft)]/40"}`}
    >
      {children}
    </div>
  );
}

function Hl({ children }: { children: React.ReactNode }) {
  return (
    <mark className="bg-[color:var(--sage-soft)] px-1 text-foreground decoration-primary/40">
      {children}
    </mark>
  );
}

function RefChip({ id }: { id: string }) {
  return (
    <span className="ml-1 cursor-pointer rounded bg-primary/8 px-1.5 py-0.5 text-[10px] tabular text-primary hover:bg-primary/12">
      [{id}]
    </span>
  );
}

function KvBlock({ items }: { items: { k: string; v: string }[] }) {
  return (
    <div className="divide-y divide-border rounded-xl border border-border bg-card">
      {items.map((i) => (
        <div key={i.k} className="flex items-center justify-between px-4 py-2.5 text-sm">
          <span className="text-muted-foreground">{i.k}</span>
          <span className="tabular">{i.v}</span>
        </div>
      ))}
    </div>
  );
}

function HeroStat({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Sparkles;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-card/80 px-4 py-3 backdrop-blur">
      <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
        <Icon className="size-3.5 text-primary" /> {label}
      </div>
      <div className="mt-1 font-serif text-2xl font-semibold tabular">{value}</div>
    </div>
  );
}

function ToolBtn({
  icon: Icon,
  label,
  to,
  id,
}: {
  icon: typeof Network;
  label: string;
  to?: "/analyses/$id/trace" | "/analyses/$id/evidence";
  id?: string;
}) {
  const cls =
    "inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-card/80 px-2.5 text-xs backdrop-blur hover:bg-accent";
  if (to && id)
    return (
      <Link to={to} params={{ id }} className={cls}>
        <Icon className="size-3.5" /> {label}
      </Link>
    );
  return (
    <button className={cls}>
      <Icon className="size-3.5" /> {label}
    </button>
  );
}

function Note({ children, tone }: { children: React.ReactNode; tone: "risk" | "info" | "ok" }) {
  const dot: Record<string, string> = {
    risk: "bg-[color:var(--risk-high)]",
    info: "bg-[color:var(--risk-mid)]",
    ok: "bg-[color:var(--risk-low)]",
  };
  return (
    <div className="rounded-md border border-border bg-background p-3 text-[11px] leading-relaxed">
      <span
        className={`mb-1.5 mr-1.5 inline-block size-2 rounded-full align-middle ${dot[tone]}`}
      />
      {children}
    </div>
  );
}
