import { createFileRoute, Link } from "@tanstack/react-router";
import { UploadCloud, Check, FileText, Loader2, ArrowRight } from "lucide-react";

export const Route = createFileRoute("/intake")({
  head: () => ({
    meta: [
      { title: "招股书接入 · Verda IPO RiskAgent" },
      { name: "description", content: "上传招股书 PDF、绑定港股基本信息、完成元数据对齐，启动 Agent 协同分析。" },
    ],
  }),
  component: IntakePage,
});

const checks = [
  { label: "股票代码", value: "03750.HK", ok: true },
  { label: "公司名称", value: "宁德时代", ok: true },
  { label: "上市日期", value: "2025-05-20", ok: true },
  { label: "行业分类", value: "新能源 · 动力电池", ok: true },
  { label: "发行价", value: "HK$ 263.00", ok: true },
  { label: "发行规模", value: "HK$ 410.1 亿", ok: true },
  { label: "Sponsor", value: "JPMorgan / 中金", ok: true },
  { label: "审计机构", value: "安永会计师事务所", ok: false },
];

const parsing = [
  { label: "PDF 解析（428 页）", pct: 100, status: "done" },
  { label: "OCR 表格抽取（67 张表）", pct: 100, status: "done" },
  { label: "章节识别 & 页码定位", pct: 100, status: "done" },
  { label: "证据块向量化（4 218 段）", pct: 86, status: "running" },
  { label: "财务三表抽取", pct: 60, status: "running" },
  { label: "风险因素章节抽取", pct: 0, status: "queued" },
];

function IntakePage() {
  return (
    <div className="px-6 py-8 sm:px-10">
      <div>
        <h1 className="font-serif text-3xl font-semibold">招股书接入</h1>
        <p className="mt-1 text-sm text-muted-foreground">三步把招股书与公司基本信息接入系统 · 自动校验元数据 · 一键启动 Agent 分析</p>
      </div>

      <div className="mt-8 grid gap-6 lg:grid-cols-[1.1fr_1fr]">
        {/* Upload card */}
        <div className="rounded-2xl border border-dashed border-primary/30 bg-gradient-to-br from-[color:var(--sage-soft)]/40 to-[color:var(--paper)] p-8">
          <div className="grid size-12 place-items-center rounded-xl bg-primary/10 text-primary"><UploadCloud className="size-6" /></div>
          <h2 className="mt-4 font-serif text-xl font-semibold">上传招股书 PDF</h2>
          <p className="mt-1 text-sm text-muted-foreground">支持中英文版本 · 最大 200 MB · 自动 OCR · 自动绑定港股基本信息</p>
          <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center">
            <button className="inline-flex items-center justify-center gap-2 rounded-full bg-primary px-5 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90">
              <UploadCloud className="size-4" /> 选择文件
            </button>
            <span className="text-xs text-muted-foreground">或拖拽 PDF 到此处</span>
          </div>

          <div className="mt-6 rounded-xl border border-border bg-card/80 p-4">
            <div className="flex items-center gap-3">
              <FileText className="size-5 text-primary" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">03750_CATL_zh_final.pdf</div>
                <div className="text-xs text-muted-foreground">428 页 · 38.2 MB · 上传于 17:24</div>
              </div>
              <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">已接入</span>
            </div>
          </div>
        </div>

        {/* Metadata verify */}
        <div className="rounded-2xl border border-border bg-card p-6">
          <div className="flex items-center justify-between">
            <h2 className="font-serif text-xl font-semibold">元数据自动校验</h2>
            <span className="text-xs text-muted-foreground">来自港股基本信息表</span>
          </div>
          <div className="mt-4 divide-y divide-border rounded-xl border border-border">
            {checks.map((c) => (
              <div key={c.label} className="flex items-center justify-between px-4 py-2.5 text-sm">
                <span className="text-muted-foreground">{c.label}</span>
                <span className="flex items-center gap-2">
                  <span className="tabular">{c.value}</span>
                  {c.ok ? (
                    <Check className="size-4 text-[color:var(--risk-low)]" />
                  ) : (
                    <span className="rounded-full bg-[color:var(--risk-mid)]/15 px-2 py-0.5 text-[10px] text-[oklch(0.45_0.12_75)]">人工核对</span>
                  )}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Parsing progress */}
      <div className="mt-8 rounded-2xl border border-border bg-card p-6">
        <div className="flex items-center justify-between">
          <h2 className="font-serif text-xl font-semibold">解析进度</h2>
          <span className="text-xs text-muted-foreground">总进度 74% · 预计还需 1 分 32 秒</span>
        </div>
        <div className="mt-4 space-y-3">
          {parsing.map((p) => (
            <div key={p.label}>
              <div className="flex items-center justify-between text-xs">
                <span className="flex items-center gap-2">
                  {p.status === "done" && <Check className="size-3.5 text-[color:var(--risk-low)]" />}
                  {p.status === "running" && <Loader2 className="size-3.5 animate-spin text-primary" />}
                  {p.status === "queued" && <span className="size-2 rounded-full bg-muted-foreground/40" />}
                  {p.label}
                </span>
                <span className="tabular text-muted-foreground">{p.pct}%</span>
              </div>
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${p.pct}%` }} />
              </div>
            </div>
          ))}
        </div>
        <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
          <p className="text-xs text-muted-foreground">解析完成后可一键派发到多 Agent 协同分析。所有上市后行情仅用于回测，不会进入分析输入。</p>
          <Link to="/analyses/$id" params={{ id: "ana_03750" }} className="inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90">
            派发到 Agent 协同 <ArrowRight className="size-4" />
          </Link>
        </div>
      </div>
    </div>
  );
}
