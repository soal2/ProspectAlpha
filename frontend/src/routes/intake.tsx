import { createFileRoute, Link } from "@tanstack/react-router";
import {
  UploadCloud,
  Check,
  FileText,
  Loader2,
  ArrowRight,
  Search,
  Database,
  Settings2,
  Users,
} from "lucide-react";
import { useState, useEffect } from "react";
import { getIntakeData } from "@/lib/data-service";

export const Route = createFileRoute("/intake")({
  head: () => ({
    meta: [
      { title: "新建 IPO 风险分析 · ProspectAlpha IPO RiskAgent" },
      {
        name: "description",
        content: "选择标的、准备材料、配置风险规则与专家组，启动 Agent 协同分析。",
      },
    ],
  }),
  component: IntakePage,
});

const workflowSteps = [
  { label: "标的选择", desc: "公司名称 / 申请编号 / URL", icon: Search },
  { label: "材料准备", desc: "招股书 PDF 与基础信息", icon: FileText },
  { label: "分析配置", desc: "规则版本 / 专家组 / 知识库", icon: Settings2 },
  { label: "启动分析", desc: "解析后派发 Agent 协同", icon: ArrowRight },
];

const analysisConfigs = [
  { label: "风险规则版本", value: "HK-IPO Taxonomy v2.4", icon: Settings2 },
  { label: "Agent 专家组", value: "专家级 · 9 位核心专家", icon: Users },
  { label: "知识库增强", value: "开启 · 招股书证据 + 历史案例", icon: Database },
];

function IntakePage() {
  const [analysisId] = useState(() => {
    const now = new Date();
    const hkt = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Hong_Kong" }));
    const dateKey = `${hkt.getFullYear()}${hkt.getMonth()}${hkt.getDate()}`;
    const stored = sessionStorage.getItem(`sid-${dateKey}`);
    if (stored) {
      const m = stored.match(/#([A-Z])-(\d{4})/);
      if (m) return `ana_${m[1].toLowerCase()}${m[2]}`;
    }
    const prefix = String.fromCharCode(65 + Math.floor(Math.random() * 26));
    const num = String(hkt.getHours()).padStart(2, "0") + String(hkt.getMinutes()).padStart(2, "0");
    return `ana_${prefix.toLowerCase()}${num}`;
  });

  const [data, setData] = useState<{
    checks: { label: string; value: string; ok: boolean }[];
    parsing: { label: string; pct: number; status: string }[];
    uploadedFile: { name: string; pages: number; size: string; uploadedAt: string };
    progressText: string;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    getIntakeData().then((d) => {
      if (!cancelled) setData(d);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <div className="px-6 py-8 sm:px-10">
      <div>
        <h1 className="font-serif text-3xl font-semibold">新建 IPO 风险分析</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          选择标的、准备材料、校验元数据、配置分析方案，然后启动 Agent 协同
        </p>
      </div>

      <div className="mt-6 grid gap-3 md:grid-cols-4">
        {workflowSteps.map((step, index) => (
          <div key={step.label} className="rounded-xl border border-border bg-card p-4">
            <div className="flex items-center justify-between">
              <span className="tabular text-[11px] text-muted-foreground">
                {String(index + 1).padStart(2, "0")}
              </span>
              <step.icon className="size-4 text-primary" />
            </div>
            <div className="mt-3 text-sm font-medium">{step.label}</div>
            <div className="mt-1 text-xs text-muted-foreground">{step.desc}</div>
          </div>
        ))}
      </div>

      <div className="mt-8 grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
        <div className="space-y-6">
          <div className="rounded-2xl border border-border bg-card p-6">
            <div className="flex items-center gap-3">
              <div className="grid size-10 place-items-center rounded-lg bg-primary/10 text-primary">
                <Search className="size-5" />
              </div>
              <div>
                <h2 className="font-serif text-xl font-semibold">标的选择</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  输入公司名称、申请编号、招股书 URL 或拟上市代码（如已分配）
                </p>
              </div>
            </div>
            <div className="mt-5 flex flex-col gap-3 sm:flex-row">
              <input
                defaultValue="宁德时代 · A1 申请稿"
                className="h-10 flex-1 rounded-md border border-border bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                aria-label="分析标的"
              />
              <button className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-border bg-background px-4 text-sm font-medium hover:bg-accent">
                <Search className="size-4" /> 匹配标的
              </button>
            </div>
          </div>

          {/* Upload card */}
          <div className="rounded-2xl border border-dashed border-primary/30 bg-gradient-to-br from-[color:var(--sage-soft)]/40 to-[color:var(--paper)] p-8">
            <div className="grid size-12 place-items-center rounded-xl bg-primary/10 text-primary">
              <UploadCloud className="size-6" />
            </div>
            <h2 className="mt-4 font-serif text-xl font-semibold">材料准备</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              上传招股书 PDF，系统将绑定公司基础信息并准备进入解析流程
            </p>
            <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center">
              <button className="inline-flex items-center justify-center gap-2 rounded-full bg-primary px-5 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90">
                <UploadCloud className="size-4" /> 选择文件
              </button>
              <span className="text-xs text-muted-foreground">或拖拽 PDF 到此处</span>
            </div>

            {data?.uploadedFile.name && (
              <div className="mt-6 rounded-xl border border-border bg-card/80 p-4">
                <div className="flex items-center gap-3">
                  <FileText className="size-5 text-primary" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{data.uploadedFile.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {data.uploadedFile.pages} 页 · {data.uploadedFile.size} · 上传于{" "}
                      {data.uploadedFile.uploadedAt}
                    </div>
                  </div>
                  <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                    已接入
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="space-y-6">
          {/* Metadata verify */}
          <div className="rounded-2xl border border-border bg-card p-6">
            <div className="flex items-center justify-between">
              <h2 className="font-serif text-xl font-semibold">元数据自动校验</h2>
              {/* <span className="text-xs text-muted-foreground">来自港股基本信息表</span> */}
            </div>
            <div className="mt-4 divide-y divide-border rounded-xl border border-border">
              {data?.checks.map((c) => (
                <div
                  key={c.label}
                  className="flex items-center justify-between px-4 py-2.5 text-sm"
                >
                  <span className="text-muted-foreground">{c.label}</span>
                  <span className="flex items-center gap-2">
                    <span className="tabular">{c.value}</span>
                    {c.ok ? (
                      <Check className="size-4 text-[color:var(--risk-low)]" />
                    ) : (
                      <span className="rounded-full bg-[color:var(--risk-mid)]/15 px-2 py-0.5 text-[10px] text-[oklch(0.45_0.12_75)]">
                        人工核对
                      </span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-border bg-card p-6">
            <div className="flex items-center justify-between">
              <h2 className="font-serif text-xl font-semibold">分析配置</h2>
              <span className="rounded-full bg-primary/8 px-2.5 py-1 text-[11px] font-medium text-primary">
                仅展示
              </span>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              本轮分析将沿用当前风险规则、知识库与专家组设置，不新增真实配置写入
            </p>
            <div className="mt-4 space-y-3">
              {analysisConfigs.map((item) => (
                <div
                  key={item.label}
                  className="flex items-center gap-3 rounded-xl border border-border bg-background px-4 py-3"
                >
                  <div className="grid size-9 place-items-center rounded-lg bg-primary/8 text-primary">
                    <item.icon className="size-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-xs text-muted-foreground">{item.label}</div>
                    <div className="truncate text-sm font-medium">{item.value}</div>
                  </div>
                  <Check className="size-4 text-[color:var(--risk-low)]" />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Parsing progress */}
      <div className="mt-8 rounded-2xl border border-border bg-card p-6">
        <div className="flex items-center justify-between">
          <h2 className="font-serif text-xl font-semibold">启动分析准备</h2>
          <span className="text-xs text-muted-foreground">{data?.progressText}</span>
        </div>
        <div className="mt-4 space-y-3">
          {data?.parsing.map((p) => (
            <div key={p.label}>
              <div className="flex items-center justify-between text-xs">
                <span className="flex items-center gap-2">
                  {p.status === "done" && (
                    <Check className="size-3.5 text-[color:var(--risk-low)]" />
                  )}
                  {p.status === "running" && (
                    <Loader2 className="size-3.5 animate-spin text-primary" />
                  )}
                  {p.status === "queued" && (
                    <span className="size-2 rounded-full bg-muted-foreground/40" />
                  )}
                  {p.label}
                </span>
                <span className="tabular text-muted-foreground">{p.pct}%</span>
              </div>
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary transition-all"
                  style={{ width: `${p.pct}%` }}
                />
              </div>
            </div>
          ))}
        </div>
        <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
          <p className="text-xs text-muted-foreground">
            文档解析完成后可派发到多 Agent 协同分析。所有上市后行情仅用于回测，不会进入分析输入。
          </p>
          <Link
            to="/analyses/$id"
            params={{ id: analysisId }}
            className="inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            派发到 Agent 协同 <ArrowRight className="size-4" />
          </Link>
        </div>
      </div>
    </div>
  );
}
