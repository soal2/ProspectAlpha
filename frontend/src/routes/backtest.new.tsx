import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { Check, ChevronRight, ArrowLeft } from "lucide-react";

export const Route = createFileRoute("/backtest/new")({
  head: () => ({ meta: [{ title: "新建回测 · Verda" }] }),
  component: NewBacktest,
});

const steps = [
  { key: "scope", label: "样本范围", desc: "选择回测的港股 IPO 样本时间窗口、行业、规模" },
  { key: "model", label: "模型版本", desc: "选择 RiskAgent 模型版本与特征集" },
  { key: "thresh", label: "风险阈值", desc: "定义高 / 中 / 低风险的判定边界" },
  { key: "metric", label: "评估口径", desc: "选择评估窗口与指标" },
];

function NewBacktest() {
  const [step, setStep] = useState(0);
  return (
    <div className="px-6 py-8 sm:px-10">
      <Link to="/backtest" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"><ArrowLeft className="size-3.5" /> 返回回测中心</Link>
      <h1 className="mt-4 font-serif text-3xl font-semibold">发起新回测</h1>
      <p className="mt-1 text-sm text-muted-foreground">4 步配置一次可复现的回测任务 · 所有参数会持久化并附在结果报告中</p>

      <div className="mt-8 grid gap-8 lg:grid-cols-[1fr_320px]">
        <div>
          {/* steps progress */}
          <ol className="flex items-center">
            {steps.map((s, i) => (
              <li key={s.key} className="flex flex-1 items-center">
                <button onClick={() => setStep(i)} className="flex items-center gap-2">
                  <span className={`grid size-7 place-items-center rounded-full text-xs font-medium ${i < step ? "bg-primary text-primary-foreground" : i === step ? "ring-2 ring-primary bg-card text-primary" : "bg-muted text-muted-foreground"}`}>{i < step ? <Check className="size-3.5" /> : i + 1}</span>
                  <span className={`text-sm ${i === step ? "font-medium" : "text-muted-foreground"}`}>{s.label}</span>
                </button>
                {i < steps.length - 1 && <span className={`mx-3 h-px flex-1 ${i < step ? "bg-primary" : "bg-border"}`} />}
              </li>
            ))}
          </ol>

          <div className="mt-6 rounded-2xl border border-border bg-card p-6">
            <h2 className="font-serif text-lg font-semibold">{steps[step].label}</h2>
            <p className="mt-1 text-xs text-muted-foreground">{steps[step].desc}</p>

            <div className="mt-5 space-y-4 text-sm">
              {step === 0 && (
                <>
                  <Field label="时间窗口">
                    <input type="text" defaultValue="2024-01-01 ~ 2026-06-30" className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm tabular focus:outline-none focus:ring-2 focus:ring-ring" />
                  </Field>
                  <Field label="行业筛选">
                    <div className="flex flex-wrap gap-2">{["全部","新能源","生物科技","金融科技","互联网","消费","硬科技"].map((t,i) => (
                      <button key={t} className={`rounded-full px-3 py-1 text-xs ${i === 0 ? "bg-primary text-primary-foreground" : "border border-border bg-background"}`}>{t}</button>
                    ))}</div>
                  </Field>
                  <Field label="发行规模">
                    <div className="flex flex-wrap gap-2">{["不限","< 10 亿","10–50 亿","50–100 亿","> 100 亿"].map((t,i) => (
                      <button key={t} className={`rounded-full px-3 py-1 text-xs ${i === 0 ? "bg-primary text-primary-foreground" : "border border-border bg-background"}`}>{t}</button>
                    ))}</div>
                  </Field>
                </>
              )}
              {step === 1 && (
                <>
                  <Field label="模型主体">
                    <select className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm">
                      <option>RiskAgent v2.4 (规则 + LGBM)</option><option>RiskAgent v2.3</option><option>纯规则评分卡 v1.8</option>
                    </select>
                  </Field>
                  <Field label="特征集">
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      {["财务三表 v3","客户/供应商 v2","管线 / 研发 v2","对赌赎回 v4","上市前市场 v3","行业景气 v3","路演舆情 v1","基石认购 v2"].map((f, i) => (
                        <label key={f} className="flex items-center gap-2 rounded-md border border-border bg-background px-3 py-2"><input type="checkbox" defaultChecked={i !== 6} className="accent-[color:var(--primary)]" /> {f}</label>
                      ))}
                    </div>
                  </Field>
                </>
              )}
              {step === 2 && (
                <>
                  {[
                    { l: "高风险阈值（破发概率）", v: 60 },
                    { l: "中风险阈值（破发概率）", v: 35 },
                    { l: "对赌赎回额外加权", v: 12 },
                  ].map(r => (
                    <Field key={r.l} label={r.l}>
                      <div className="flex items-center gap-3">
                        <input type="range" min={0} max={100} defaultValue={r.v} className="flex-1 accent-[color:var(--primary)]" />
                        <span className="w-12 rounded-md border border-border bg-background py-1 text-center text-xs tabular">{r.v}</span>
                      </div>
                    </Field>
                  ))}
                </>
              )}
              {step === 3 && (
                <>
                  <Field label="评估窗口">
                    <div className="flex gap-2">{["T+1","T+5","T+20","T+60"].map((t,i) => <button key={t} className={`rounded-full px-3 py-1 text-xs ${i === 2 ? "bg-primary text-primary-foreground" : "border border-border bg-background"}`}>{t}</button>)}</div>
                  </Field>
                  <Field label="正样本定义">
                    <select className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm">
                      <option>破发（收盘 &lt; 发行价）</option>
                      <option>显著下跌（&gt; 15%）</option>
                      <option>显著下跌（&gt; 25%）</option>
                    </select>
                  </Field>
                  <Field label="指标">
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      {["AUC","KS","Lift@10","Lift@20","Recall@TopK","Precision@TopK","信息增益","分组累计收益"].map(m => (
                        <label key={m} className="flex items-center gap-2 rounded-md border border-border bg-background px-3 py-2"><input type="checkbox" defaultChecked className="accent-[color:var(--primary)]" /> {m}</label>
                      ))}
                    </div>
                  </Field>
                </>
              )}
            </div>

            <div className="mt-6 flex items-center justify-between border-t border-border pt-4">
              <button onClick={() => setStep(s => Math.max(0, s - 1))} disabled={step === 0} className="text-xs text-muted-foreground disabled:opacity-30">← 上一步</button>
              {step < steps.length - 1 ? (
                <button onClick={() => setStep(s => s + 1)} className="inline-flex items-center gap-1 rounded-full bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground">
                  下一步 <ChevronRight className="size-3.5" />
                </button>
              ) : (
                <Link to="/backtest/$id" params={{ id: "bt_2026q2" }} className="rounded-full bg-primary px-5 py-1.5 text-xs font-medium text-primary-foreground">启动回测</Link>
              )}
            </div>
          </div>
        </div>

        <aside className="space-y-4">
          <div className="rounded-2xl border border-border bg-card p-5">
            <div className="text-xs font-medium">实时预估</div>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <Mini label="样本量" value="186" />
              <Mini label="正样本" value="42" />
              <Mini label="预计耗时" value="3 m" />
              <Mini label="估算成本" value="0.12 K tok" />
            </div>
          </div>
          <div className="rounded-2xl border border-dashed border-primary/30 bg-[color:var(--sage-soft)]/40 p-4 text-xs leading-relaxed">
            <div className="font-medium">数据边界提示</div>
            <p className="mt-1 text-muted-foreground">上市后 T+1 / T+5 / T+20 / T+60 行情仅用作标签，绝不会回灌到模型输入。所有特征都来自上市前可获得的信息。</p>
          </div>
        </aside>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1.5 text-xs font-medium text-muted-foreground">{label}</div>
      {children}
    </div>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-muted/50 p-3">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className="mt-1 font-serif text-lg font-semibold tabular">{value}</div>
    </div>
  );
}
