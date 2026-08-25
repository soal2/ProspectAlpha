import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { getMarketSnapshots, publishMarketSnapshot, uploadMarketData } from "@/lib/data-service";
import type { MarketSnapshotOption } from "@/lib/types";

export const Route = createFileRoute("/backtest/admin")({ component: BacktestAdmin });

function BacktestAdmin() {
  const [snapshots, setSnapshots] = useState<MarketSnapshotOption[]>([]);
  const [security, setSecurity] = useState<File | null>(null);
  const [company, setCompany] = useState<File | null>(null);
  const [prices, setPrices] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const load = async () => setSnapshots(await getMarketSnapshots());
  useEffect(() => { void load(); }, []);
  const upload = async () => {
    if (!security || !company || !prices) return;
    setBusy(true); setMessage("");
    try { const result = await uploadMarketData({ security, company, prices }); setMessage(`已生成版本 ${result.snapshot_id}，T+5 标签 ${result.labels_created} 条；请审核后发布。`); await load(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "上传失败"); }
    finally { setBusy(false); }
  };
  const publish = async (id: string) => { setBusy(true); try { await publishMarketSnapshot(id); await load(); } catch (error) { setMessage(error instanceof Error ? error.message : "发布失败"); } finally { setBusy(false); } };
  return <div className="px-6 py-8 sm:px-10">
    <Link to="/backtest" className="text-xs text-muted-foreground hover:text-foreground">← 返回回测中心</Link>
    <h1 className="mt-4 font-serif text-3xl font-semibold">市场数据管理</h1>
    <p className="mt-1 text-sm text-muted-foreground">此页面应由部署网关限制为管理员访问。上传后的版本必须通过校验并人工发布。</p>
    <section className="mt-6 max-w-3xl rounded-2xl border border-border bg-card p-6">
      <h2 className="font-medium">导入市场数据</h2>
      <div className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
        <FileInput label="证券主档 CSV" onChange={setSecurity} />
        <FileInput label="公司主档 CSV" onChange={setCompany} />
        <FileInput label="日行情 CSV" onChange={setPrices} />
      </div>
      <button disabled={busy || !security || !company || !prices} onClick={() => void upload()} className="mt-5 rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">上传并校验</button>
      {message && <p className="mt-3 text-xs text-muted-foreground">{message}</p>}
    </section>
    <section className="mt-6 overflow-hidden rounded-2xl border border-border bg-card"><table className="w-full text-sm"><thead className="bg-muted/50 text-xs text-muted-foreground"><tr><th className="p-3 text-left">版本</th><th className="p-3 text-left">质量校验</th><th className="p-3 text-right">T+5 标签</th><th className="p-3 text-left">状态</th><th className="p-3" /></tr></thead><tbody>{snapshots.map((item) => <tr key={item.snapshotId} className="border-t"><td className="p-3 font-mono text-xs">{item.snapshotId}</td><td className="p-3">{item.qualityReport?.status === "passed" ? "通过" : "待校验"}</td><td className="p-3 text-right">{item.t5Labels}</td><td className="p-3">{item.status === "published" ? "已发布" : "草稿"}</td><td className="p-3 text-right">{item.status !== "published" && <button disabled={busy} onClick={() => void publish(item.snapshotId)} className="text-xs text-primary hover:underline">发布</button>}</td></tr>)}</tbody></table></section>
  </div>;
}

function FileInput({ label, onChange }: { label: string; onChange: (file: File | null) => void }) {
  return <label className="rounded-lg border border-dashed border-border p-3 text-xs text-muted-foreground">{label}<input className="mt-2 block w-full text-xs" type="file" accept=".csv,text/csv" onChange={(event) => onChange(event.target.files?.[0] ?? null)} /></label>;
}
