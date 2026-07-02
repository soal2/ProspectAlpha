import { createFileRoute } from "@tanstack/react-router";
import { Search, FileText, ExternalLink } from "lucide-react";

export const Route = createFileRoute("/evidence")({
  head: () => ({
    meta: [
      { title: "证据知识库 · Verda IPO RiskAgent" },
      {
        name: "description",
        content: "全量招股书原文证据 · 跨公司、跨章节、跨风险类别的检索与复用",
      },
    ],
  }),
  component: EvidencePage,
});

const facets = [
  { label: "公司", items: ["全部", "宁德时代", "京东健康", "蚂蚁集团", "理想汽车", "美的集团"] },
  { label: "招股书章节", items: ["全部", "风险因素", "业务", "财务资料", "董事及高管", "股本"] },
  { label: "风险类别", items: ["全部", "财务", "经营", "法律", "市场"] },
  { label: "置信度", items: ["全部", "≥ 0.9 高", "0.7–0.9 中", "< 0.7 低"] },
];

const evidence = [
  {
    id: "e_898aa168",
    code: "03750.HK",
    name: "宁德时代",
    section: "财务资料",
    page: 218,
    conf: 0.93,
    snippet:
      "本集团 2024 年度经营性现金流净额为人民币 712.4 亿元，同比下降 18.6%，主要由于动力电池业务存货周转周期延长……",
    risk: "经营现金流",
  },
  {
    id: "e_5e8c343e",
    code: "06618.HK",
    name: "京东健康",
    section: "风险因素",
    page: 47,
    conf: 0.88,
    snippet:
      "倘若与京东集团的关联交易安排不能按照协议持续，本公司收益可能受到重大不利影响。截至往绩记录期末，相关关联收入占比 43.6%。",
    risk: "关联交易",
  },
  {
    id: "e_74ee12b1",
    code: "09988.HK",
    name: "蚂蚁集团",
    section: "风险因素",
    page: 92,
    conf: 0.95,
    snippet:
      "本公司部分业务受到不断变化的金融监管规则约束，包括但不限于资本充足率、网络小贷新规、消费者保护规定……",
    risk: "监管合规",
  },
  {
    id: "e_3551480d",
    code: "02015.HK",
    name: "理想汽车",
    section: "业务",
    page: 156,
    conf: 0.81,
    snippet:
      "前五大客户占公司销售收入比例约 12.4%。但前五大供应商占采购总额比例为 58.9%，存在一定供应商集中度风险。",
    risk: "供应商集中度",
  },
  {
    id: "e_1cdca7b3",
    code: "01024.HK",
    name: "快手生物",
    section: "风险因素",
    page: 31,
    conf: 0.91,
    snippet:
      "本公司核心管线 KS-01 仍处于临床二期阶段，截至最后实际可行日期尚未实现商业化收入，存在重大研发失败风险。",
    risk: "核心管线",
  },
  {
    id: "e_a4111a75",
    code: "00300.HK",
    name: "美的集团",
    section: "财务资料",
    page: 198,
    conf: 0.86,
    snippet:
      "截至 2024 年 12 月 31 日，本集团有息负债总额为人民币 421.6 亿元，资产负债率 64.8%，处于行业平均水平。",
    risk: "负债率",
  },
];

function EvidencePage() {
  return (
    <div className="grid h-[calc(100vh-3.5rem)] grid-cols-[260px_1fr_360px]">
      {/* facets */}
      <aside className="border-r border-border bg-card/40 p-5">
        <h2 className="font-serif text-lg font-semibold">证据知识库</h2>
        <p className="mt-1 text-xs text-muted-foreground">4 218 条证据 · 25 家公司</p>
        <div className="relative mt-4">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            placeholder="关键词搜索"
            className="h-9 w-full rounded-lg border border-border bg-background pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="mt-6 space-y-5">
          {facets.map((f) => (
            <div key={f.label}>
              <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                {f.label}
              </div>
              <ul className="mt-2 space-y-1 text-sm">
                {f.items.map((it, i) => (
                  <li key={it}>
                    <button
                      className={`flex w-full items-center justify-between rounded-md px-2 py-1 text-left ${i === 0 ? "bg-primary/8 text-primary" : "text-muted-foreground hover:bg-accent"}`}
                    >
                      <span>{it}</span>
                      <span className="text-[10px] tabular">
                        {Math.floor(Math.random() * 80) + 4}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </aside>

      {/* evidence list */}
      <section className="overflow-y-auto p-6">
        <div className="flex items-end justify-between">
          <div>
            <h3 className="font-serif text-xl font-semibold">最近沉淀的证据</h3>
            <p className="mt-1 text-xs text-muted-foreground">6 / 4 218 条 · 按置信度排序</p>
          </div>
          <div className="flex gap-2 text-xs">
            <button className="rounded-full border border-border bg-card px-3 py-1.5 hover:bg-accent">
              按时间
            </button>
            <button className="rounded-full bg-primary px-3 py-1.5 text-primary-foreground">
              按置信度
            </button>
          </div>
        </div>
        <div className="mt-5 space-y-3">
          {evidence.map((e) => (
            <article
              key={e.id}
              className="rounded-xl border border-border bg-card p-4 transition hover:border-primary/30"
            >
              <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                <span className="tabular font-medium text-foreground">{e.code}</span>
                <span>·</span>
                <span>{e.name}</span>
                <span>·</span>
                <span>
                  {e.section} P.{e.page}
                </span>
                <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-primary/8 px-2 py-0.5 font-medium text-primary tabular">
                  置信度 {e.conf.toFixed(2)}
                </span>
              </div>
              <p className="mt-3 text-sm leading-relaxed text-foreground">{e.snippet}</p>
              <div className="mt-3 flex items-center justify-between border-t border-border pt-3">
                <div className="flex flex-wrap gap-1.5">
                  <span className="rounded-full bg-[color:var(--risk-mid)]/15 px-2 py-0.5 text-[10px] text-[oklch(0.45_0.12_75)]">
                    {e.risk}
                  </span>
                  <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground tabular">
                    [{e.id}]
                  </span>
                </div>
                <button className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
                  查看原文 <ExternalLink className="size-3" />
                </button>
              </div>
            </article>
          ))}
        </div>
      </section>

      {/* preview */}
      <aside className="border-l border-border bg-card/40 p-5">
        <h3 className="flex items-center gap-2 font-serif text-base font-semibold">
          <FileText className="size-4 text-primary" /> 原文预览
        </h3>
        <p className="mt-1 text-[11px] text-muted-foreground">
          [e_898aa168] · 03750.HK · 财务资料 · P.218
        </p>
        <div className="mt-4 aspect-[3/4] overflow-hidden rounded-lg border border-border bg-paper">
          <div className="grid h-full place-items-center bg-gradient-to-br from-[color:var(--sage-soft)]/30 to-[color:var(--amber-soft)]/30 text-center text-xs text-muted-foreground">
            <div>
              <FileText className="mx-auto size-8 text-primary/40" />
              <p className="mt-3 px-4 leading-relaxed">
                招股书 PDF 第 218 页 ·<br />
                bbox [72.1, 118.5, 520.4, 360.2]
              </p>
              <p className="mt-2 text-[10px]">点击放大查看原始截图</p>
            </div>
          </div>
        </div>
        <div className="mt-4 rounded-lg border border-border bg-background p-3 text-xs">
          <div className="font-medium">证据链接到的结论</div>
          <ul className="mt-2 space-y-1 text-muted-foreground">
            <li>· 经营现金流连续两期下滑</li>
            <li>· 现金 runway 仍 &gt; 5 年（中风险）</li>
            <li>· 与同业宁波容百对比偏弱</li>
          </ul>
        </div>
      </aside>
    </div>
  );
}
