import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, CheckCircle2, CircleAlert, Copy, Database, Loader2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { createBacktest, getBacktestCandidates, getMarketSnapshots } from "@/lib/data-service";
import type { BacktestCandidatePreview, MarketSnapshotOption } from "@/lib/types";

export const Route = createFileRoute("/backtest/new")({
  head: () => ({ meta: [{ title: "新建回测 · ProspectAlpha" }] }),
  component: NewBacktest,
});

function NewBacktest() {
  const navigate = useNavigate();
  const [snapshots, setSnapshots] = useState<MarketSnapshotOption[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [candidates, setCandidates] = useState<BacktestCandidatePreview[]>([]);
  const [selectedCode, setSelectedCode] = useState("");
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const selected = snapshots.find((item) => item.snapshotId === selectedId) ?? null;

  const loadSnapshots = async () => {
    setLoading(true);
    setError("");
    try {
      const options = await getMarketSnapshots();
      setSnapshots(options);
      setSelectedId((current) => current || options.find((item) => item.canCreateBacktest)?.snapshotId || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法加载市场数据版本");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadSnapshots();
  }, []);

  useEffect(() => {
    setCandidates([]);
    setSelectedCode("");
    if (!selected?.canCreateBacktest) return;
    let cancelled = false;
    setCandidatesLoading(true);
    setError("");
    void getBacktestCandidates(selected.snapshotId)
      .then((items) => { if (!cancelled) setCandidates(items); })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : "无法加载可回测公司"); })
      .finally(() => { if (!cancelled) setCandidatesLoading(false); });
    return () => { cancelled = true; };
  }, [selected?.snapshotId, selected?.canCreateBacktest]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!selected?.canCreateBacktest) return;
    setCreating(true);
    setError("");
    try {
      const backtest = await createBacktest({ snapshot_id: selected.snapshotId, wind_code: selectedCode });
      await navigate({ to: "/backtest/$id", params: { id: backtest.id } });
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建回测失败");
    } finally {
      setCreating(false);
    }
  };
  return (
    <div className="px-6 py-8 sm:px-10">
      <Link
        to="/backtest"
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" /> 返回回测中心
      </Link>
      <form onSubmit={submit} className="mt-8 max-w-3xl rounded-2xl border border-border bg-card p-6 sm:p-7">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-primary"><Database className="size-5" /><span className="text-xs font-semibold tracking-[0.16em]">DATA VERSION</span></div>
            <h1 className="mt-3 font-serif text-2xl font-semibold">创建 T+5 风险预警回测</h1>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">系统已默认选择最新可回测的数据版本。版本会随回测结果一并保存，保证复现与审计。</p>
          </div>
          <button type="button" onClick={() => void loadSnapshots()} disabled={loading} className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs hover:bg-accent disabled:opacity-50">
            <RefreshCw className={`size-3.5 ${loading ? "animate-spin" : ""}`} /> 刷新
          </button>
        </div>
        {loading ? (
          <div className="mt-7 flex items-center gap-2 rounded-xl border border-dashed border-border p-5 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> 正在检查可用数据版本…</div>
        ) : !snapshots.some((item) => item.canCreateBacktest) ? (
          <EmptyState />
        ) : (
          <>
            <label className="mt-7 block text-xs font-medium text-muted-foreground">市场数据版本</label>
            <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)} className="mt-2 h-11 w-full rounded-lg border border-input bg-background px-3 text-sm">
              <option value="" disabled>请选择数据版本</option>
              {snapshots.map((item) => <option key={item.snapshotId} value={item.snapshotId}>{formatSnapshot(item)}</option>)}
            </select>
            {selected && <SnapshotCard snapshot={selected} />}
            {selected?.canCreateBacktest && <div className="mt-6">
              <label className="block text-xs font-medium text-muted-foreground">回测公司</label>
              {candidatesLoading ? <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="size-3.5 animate-spin" /> 正在加载满足 T+5 标签与时点治理条件的公司…</div> : candidates.length ? <>
                <select value={selectedCode} onChange={(event) => setSelectedCode(event.target.value)} className="mt-2 h-11 w-full rounded-lg border border-input bg-background px-3 text-sm"><option value="" disabled>请选择一家公司</option>{candidates.map((item) => <option key={item.windCode} value={item.windCode}>{item.windCode} · {item.companyName ?? "未命名公司"} · 风险分 {item.score}</option>)}</select>
                {selectedCode && <CandidateCard candidate={candidates.find((item) => item.windCode === selectedCode)!} />}
              </> : <p className="mt-2 text-xs text-[color:var(--risk-medium)]">该版本没有同时具备 T+5 标签、冻结评分且信息截止日不晚于上市日的公司。</p>}
            </div>}
          </>
        )}
        {error && <p className="mt-3 text-xs text-destructive">{error}</p>}
        <button disabled={!selected?.canCreateBacktest || !selectedCode || candidatesLoading || creating || loading} className="mt-6 inline-flex h-10 items-center gap-2 rounded-full bg-primary px-4 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50">
          {creating && <Loader2 className="size-4 animate-spin" />} 创建回测
        </button>
      </form>
    </div>
  );
}

function CandidateCard({ candidate }: { candidate: BacktestCandidatePreview }) {
  return <div className="mt-3 rounded-xl border border-border bg-muted/20 p-4 text-xs"><div className="font-medium">{candidate.companyName ?? candidate.windCode} · {candidate.windCode}</div><div className="mt-3 grid gap-3 sm:grid-cols-3"><Metric label="上市日期" value={candidate.listingDate} /><Metric label="冻结风险分" value={`${candidate.score} · ${candidate.riskLevel}`} /><Metric label="信息截止日" value={candidate.analysisAsOfDate} /></div><p className="mt-3 text-muted-foreground">评分来源：{candidate.sourceRunId} · {candidate.scoreId}</p></div>;
}

function SnapshotCard({ snapshot }: { snapshot: MarketSnapshotOption }) {
  const copyId = () => void navigator.clipboard?.writeText(snapshot.snapshotId);
  return <div className="mt-3 rounded-xl border border-border bg-muted/20 p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex items-center gap-2 text-sm font-medium">
        {snapshot.canCreateBacktest ? <CheckCircle2 className="size-4 text-primary" /> : <CircleAlert className="size-4 text-[color:var(--risk-medium)]" />}
        {snapshot.canCreateBacktest ? "可直接创建回测" : "暂不可创建回测"}
      </div>
      <span className="text-xs text-muted-foreground">导入于 {formatDate(snapshot.createdAt)}</span>
    </div>
    <div className="mt-4 grid gap-3 text-xs sm:grid-cols-3">
      <Metric label="行情覆盖" value={snapshot.priceStart && snapshot.priceEnd ? `${snapshot.priceStart} ～ ${snapshot.priceEnd}` : "未记录"} />
      <Metric label="T+5 标签" value={`${snapshot.t5Labels} 家`} />
      <Metric label="可回测样本" value={`${snapshot.eligibleSamples} 家`} />
    </div>
    {!snapshot.canCreateBacktest && <p className="mt-4 text-xs text-[color:var(--risk-medium)]">{snapshot.unavailableReason}</p>}
    <details className="mt-4 border-t border-border pt-3 text-xs text-muted-foreground">
      <summary className="cursor-pointer select-none">高级审计信息</summary>
      <div className="mt-2 flex items-center gap-2"><code className="rounded bg-background px-2 py-1">{snapshot.snapshotId}</code><button type="button" onClick={copyId} className="inline-flex items-center gap-1 hover:text-foreground"><Copy className="size-3" />复制</button></div>
      <p className="mt-2">证券 {snapshot.securities} 家 · 日行情 {snapshot.priceRows.toLocaleString()} 行</p>
    </details>
  </div>;
}

function EmptyState() {
  return <div className="mt-7 rounded-xl border border-dashed border-border p-5">
    <CircleAlert className="size-5 text-[color:var(--risk-medium)]" />
    <h2 className="mt-3 text-sm font-medium">暂无可用市场数据版本</h2>
    <p className="mt-1 text-xs leading-5 text-muted-foreground">请先导入覆盖上市日后的行情数据；随后完成含股票代码的报告生成，系统会自动冻结风险评分。两项齐备后即可创建 T+5 回测。</p>
  </div>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><div className="text-muted-foreground">{label}</div><div className="mt-1 font-medium tabular">{value}</div></div>;
}

function formatSnapshot(item: MarketSnapshotOption) {
  return `${item.canCreateBacktest ? "可用" : "不可用"} · ${item.eligibleSamples} 个可回测样本 · ${formatDate(item.createdAt)}`;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
