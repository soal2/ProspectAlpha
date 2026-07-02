import { createFileRoute, Link } from "@tanstack/react-router";
import { Plus, Play, CheckCircle2, Loader2 } from "lucide-react";
import { useState, useEffect } from "react";
import { getBacktests } from "@/lib/data-service";
import { type BacktestSummary } from "@/mocks";

export const Route = createFileRoute("/backtest/")({
  head: () => ({
    meta: [
      { title: "回测中心 · Verda IPO RiskAgent" },
      {
        name: "description",
        content: "对历史 IPO 案例进行风险预警回测 · AUC / KS / Lift / TopK 全指标评估",
      },
    ],
  }),
  component: BacktestIndex,
});

function BacktestIndex() {
  const [backtests, setBacktests] = useState<BacktestSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getBacktests().then((data) => {
      if (!cancelled) {
        setBacktests(data);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <div className="px-6 py-8 sm:px-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-semibold">回测中心</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            对历史港股 IPO 案例进行风险预警回测 · 评估模型稳定性、预测准确率与业务价值
          </p>
        </div>
        <Link
          to="/backtest/new"
          className="inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
        >
          <Plus className="size-4" /> 发起新回测
        </Link>
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Kpi label="累计回测任务" value="42" />
        <Kpi label="覆盖样本" value="486" caption="港股 IPO · 2022 – 2026 H1" />
        <Kpi label="平均 AUC" value="0.794" />
        <Kpi label="高风险召回率" value="76%" caption="对破发样本的捕获率" />
      </div>

      <div className="mt-8 overflow-hidden rounded-2xl border border-border bg-card">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-xs text-muted-foreground">
            <tr>
              <th className="px-4 py-3 text-left font-medium">名称</th>
              <th className="px-4 py-3 text-left font-medium">模型 / 版本</th>
              <th className="px-4 py-3 text-right font-medium">样本</th>
              <th className="px-4 py-3 text-right font-medium">AUC</th>
              <th className="px-4 py-3 text-right font-medium">KS</th>
              <th className="px-4 py-3 text-right font-medium">Lift@10</th>
              <th className="px-4 py-3 text-right font-medium">召回</th>
              <th className="px-4 py-3 text-left font-medium">状态</th>
              <th className="px-4 py-3 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {backtests.map((b) => (
              <tr key={b.id} className="hover:bg-muted/30">
                <td className="px-4 py-3">
                  <div className="font-medium">{b.name}</div>
                  <div className="text-[11px] text-muted-foreground">
                    {b.createdAt} · {b.author}
                  </div>
                </td>
                <td className="px-4 py-3 text-xs text-muted-foreground">
                  <div>{b.model}</div>
                  <div className="text-[11px] tabular">{b.modelVersion}</div>
                </td>
                <td className="px-4 py-3 text-right tabular">{b.samples}</td>
                <td className="px-4 py-3 text-right tabular font-medium">
                  {b.auc ? b.auc.toFixed(3) : "—"}
                </td>
                <td className="px-4 py-3 text-right tabular">{b.ks ? b.ks.toFixed(3) : "—"}</td>
                <td className="px-4 py-3 text-right tabular">
                  {b.lift10 ? `${b.lift10.toFixed(1)}×` : "—"}
                </td>
                <td className="px-4 py-3 text-right tabular">
                  {b.recall ? `${(b.recall * 100).toFixed(0)}%` : "—"}
                </td>
                <td className="px-4 py-3">
                  <span
                    className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] ${b.status === "完成" ? "bg-primary/10 text-primary" : b.status === "运行中" ? "bg-[color:var(--risk-mid)]/15 text-[oklch(0.45_0.12_75)]" : "bg-destructive/10 text-destructive"}`}
                  >
                    {b.status === "运行中" ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : (
                      <CheckCircle2 className="size-3" />
                    )}
                    {b.status}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  <Link
                    to="/backtest/$id"
                    params={{ id: b.id }}
                    className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                  >
                    <Play className="size-3" /> 查看
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Kpi({ label, value, caption }: { label: string; value: string; caption?: string }) {
  return (
    <div className="rounded-2xl border border-border bg-card p-5">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-2 font-serif text-3xl font-semibold tabular">{value}</div>
      {caption && <div className="mt-1 text-[11px] text-muted-foreground">{caption}</div>}
    </div>
  );
}
