import { createFileRoute } from "@tanstack/react-router";
import { ChevronRight, Code2 } from "lucide-react";
import { useState } from "react";

export const Route = createFileRoute("/risk-rules")({
  head: () => ({
    meta: [
      { title: "风险体系配置 · Verda IPO RiskAgent" },
      { name: "description", content: "金融专家定义风险分类、阈值、权重 · 技术团队负责工程化抽取" },
    ],
  }),
  component: RiskRulesPage,
});

interface Rule { code: string; label: string; high: string; mid: string; weight: number; source: string }

const taxonomy: { key: string; label: string; color: string; rules: Rule[] }[] = [
  {
    key: "financial", label: "财务风险", color: "var(--chart-1)",
    rules: [
      { code: "unprofitable", label: "未盈利", high: "净利润 < 0 持续 ≥ 2 期", mid: "净利润 < 0 持续 1 期", weight: 12, source: "财务报表" },
      { code: "neg_cf", label: "经营现金流为负", high: "连续 ≥ 2 期", mid: "1 期", weight: 14, source: "现金流量表" },
      { code: "cash_runway", label: "现金 runway", high: "< 1.5 年", mid: "< 3.0 年", weight: 11, source: "现金 + 季度烧钱" },
      { code: "gross_margin", label: "毛利下滑", high: "> 5pct yoy", mid: "> 2pct yoy", weight: 6, source: "利润表" },
      { code: "debt_ratio", label: "高负债率", high: "> 70%", mid: "> 55%", weight: 7, source: "资产负债表" },
    ],
  },
  {
    key: "business", label: "经营风险", color: "var(--chart-2)",
    rules: [
      { code: "customer_cc", label: "客户集中度", high: "Top5 > 70%", mid: "Top5 > 40%", weight: 9, source: "业务章节" },
      { code: "supplier_cc", label: "供应商集中度", high: "Top5 > 75%", mid: "Top5 > 50%", weight: 6, source: "采购章节" },
      { code: "single_prod", label: "单一产品依赖", high: "营收占比 > 70%", mid: "> 50%", weight: 7, source: "营收构成" },
      { code: "pipeline", label: "核心管线未商业化", high: "无收入 & 临床 ≤ II", mid: "首款产品未放量", weight: 9, source: "研发管线" },
    ],
  },
  {
    key: "legal", label: "法律合规风险", color: "var(--chart-4)",
    rules: [
      { code: "redemption", label: "对赌/赎回条款", high: "存在硬性赎回触发", mid: "存在但可豁免", weight: 11, source: "法律意见书" },
      { code: "repurchase", label: "回购义务", high: "金额 > 净资产 30%", mid: "> 10%", weight: 6, source: "股本章节" },
      { code: "related", label: "关联交易", high: "营收占比 > 40%", mid: "> 20%", weight: 8, source: "关联方披露" },
      { code: "litigation", label: "重大诉讼", high: "未决标的 > 净资产 10%", mid: "> 3%", weight: 5, source: "诉讼披露" },
    ],
  },
  {
    key: "market", label: "市场风险", color: "var(--chart-5)",
    rules: [
      { code: "weak_market", label: "上市前市场情绪", high: "板块 30 日 < −10%", mid: "< −3%", weight: 5, source: "上市前行情" },
      { code: "ipo_break", label: "近期新股破发率", high: "> 60%", mid: "> 40%", weight: 5, source: "同期 IPO" },
      { code: "valuation", label: "估值溢价", high: "PE > 同业 1.8×", mid: "> 1.3×", weight: 5, source: "同业对标" },
    ],
  },
];

function RiskRulesPage() {
  const [active, setActive] = useState("financial");
  const group = taxonomy.find((g) => g.key === active)!;

  return (
    <div className="grid h-[calc(100vh-3.5rem)] grid-cols-[260px_1fr_380px]">
      <aside className="border-r border-border bg-card/40 p-5">
        <h2 className="font-serif text-lg font-semibold">风险体系</h2>
        <p className="mt-1 text-xs text-muted-foreground">4 大类 · 16 个子风险</p>
        <div className="mt-4 space-y-1">
          {taxonomy.map((g) => (
            <button key={g.key} onClick={() => setActive(g.key)} className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm ${active === g.key ? "bg-primary/8 text-primary" : "text-muted-foreground hover:bg-accent"}`}>
              <span className="flex items-center gap-2">
                <span className="size-2 rounded-full" style={{ background: `oklch(${g.color})` }} />
                {g.label}
              </span>
              <span className="text-[11px] tabular">{g.rules.length}</span>
            </button>
          ))}
        </div>

        <div className="mt-8 rounded-lg border border-border bg-background p-3 text-xs">
          <div className="font-medium">权重分布</div>
          <div className="mt-3 space-y-2">
            {taxonomy.map((g) => {
              const sum = g.rules.reduce((s, r) => s + r.weight, 0);
              return (
                <div key={g.key}>
                  <div className="flex justify-between text-muted-foreground"><span>{g.label}</span><span className="tabular">{sum}</span></div>
                  <div className="mt-1 h-1 rounded-full bg-muted"><div className="h-1 rounded-full" style={{ width: `${sum}%`, background: `oklch(${g.color})` }} /></div>
                </div>
              );
            })}
          </div>
        </div>
      </aside>

      <section className="overflow-y-auto p-6">
        <div className="flex items-end justify-between">
          <div>
            <h1 className="font-serif text-2xl font-semibold">{group.label}</h1>
            <p className="mt-1 text-xs text-muted-foreground">共 {group.rules.length} 条规则 · 总权重 {group.rules.reduce((s, r) => s + r.weight, 0)}</p>
          </div>
          <button className="rounded-full bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90">+ 新增规则</button>
        </div>

        <div className="mt-5 space-y-3">
          {group.rules.map((r) => (
            <div key={r.code} className="rounded-xl border border-border bg-card p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="font-medium">{r.label} <span className="ml-2 text-[11px] tabular text-muted-foreground">{r.code}</span></div>
                  <div className="mt-0.5 text-[11px] text-muted-foreground">数据来源：{r.source}</div>
                </div>
                <div className="flex items-center gap-3 text-xs">
                  <span className="text-muted-foreground">权重</span>
                  <input type="range" min={0} max={20} defaultValue={r.weight} className="accent-[color:var(--primary)]" />
                  <span className="w-6 tabular text-right">{r.weight}</span>
                </div>
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <div className="flex items-center gap-2 rounded-lg bg-[color:var(--risk-high)]/8 px-3 py-2 text-xs">
                  <span className="text-[color:var(--risk-high)]">高风险阈值</span>
                  <ChevronRight className="size-3 text-muted-foreground" />
                  <span className="font-medium tabular">{r.high}</span>
                </div>
                <div className="flex items-center gap-2 rounded-lg bg-[color:var(--risk-mid)]/12 px-3 py-2 text-xs">
                  <span className="text-[oklch(0.45_0.12_75)]">中风险阈值</span>
                  <ChevronRight className="size-3 text-muted-foreground" />
                  <span className="font-medium tabular">{r.mid}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* <aside className="overflow-y-auto border-l border-border bg-[oklch(0.18_0.018_160)] p-5 text-xs leading-relaxed text-[color:var(--paper)]">
        <h3 className="flex items-center gap-2 font-serif text-sm font-semibold text-[color:var(--sage-soft)]"><Code2 className="size-4" /> YAML 实时预览</h3>
        <pre className="mt-3 font-mono text-[11px] text-[oklch(0.92_0.025_140)]">{`risk_taxonomy:
  ${group.key}:`}
{group.rules.map((r) => `
    - code: ${r.code}
      label: "${r.label}"
      weight: ${r.weight}
      thresholds:
        high: "${r.high}"
        mid:  "${r.mid}"
      source: "${r.source}"`).join("")}
        </pre>
      </aside> */}
    </div>
  );
}
