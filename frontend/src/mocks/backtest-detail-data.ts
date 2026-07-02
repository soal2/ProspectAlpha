export interface BacktestDetailData {
  roc: { fpr: number; tpr: number }[];
  groupRet: { d: string; high: number; mid: number; low: number; hsi: number }[];
  lift: { p: string; lift: number; baseline: number }[];
  cm: { label: string; n: number; hint: string; tone: "ok" | "warn" }[];
  fnSamples: {
    code: string;
    name: string;
    actual: number;
    score: number;
    reason: string;
    risk: "high" | "mid" | "low";
  }[];
  fpSamples: {
    code: string;
    name: string;
    actual: number;
    score: number;
    reason: string;
    risk: "high" | "mid" | "low";
  }[];
  palette: string[];
  hoursSaved: number;
  factorContributions: { f: string; v: number }[];
  precision: number;
  recall: number;
  f1: number;
}

export const backtestDetailData: Record<string, BacktestDetailData> = {
  bt_2026q2: {
    roc: Array.from({ length: 11 }).map((_, i) => ({
      fpr: i / 10,
      tpr: Math.min(1, Math.pow(i / 10, 0.4) + 0.05),
    })),
    groupRet: [
      { d: "T", high: 0, mid: 0, low: 0, hsi: 0 },
      { d: "T+1", high: -3.2, mid: -0.8, low: 1.4, hsi: 0.4 },
      { d: "T+5", high: -7.6, mid: -1.4, low: 3.2, hsi: 0.9 },
      { d: "T+20", high: -12.4, mid: -2.1, low: 5.8, hsi: 1.6 },
      { d: "T+60", high: -18.6, mid: -3.8, low: 9.2, hsi: 2.7 },
    ],
    lift: Array.from({ length: 10 }).map((_, i) => ({
      p: `${(i + 1) * 10}%`,
      lift: 3.4 - i * 0.27,
      baseline: 1,
    })),
    cm: [
      { label: "预测高·实际破发", n: 32, hint: "True Positive", tone: "ok" },
      { label: "预测高·未破发", n: 9, hint: "False Positive · 误报", tone: "warn" },
      { label: "预测低·实际破发", n: 11, hint: "False Negative · 漏报", tone: "warn" },
      { label: "预测低·未破发", n: 134, hint: "True Negative", tone: "ok" },
    ],
    fnSamples: [
      {
        code: "01024.HK",
        name: "快手生物",
        actual: -22.4,
        score: 38,
        reason: "管线进展超预期被低估",
        risk: "high" as const,
      },
      {
        code: "06699.HK",
        name: "盛源物流",
        actual: -16.8,
        score: 42,
        reason: "供应商集中度信息披露不全",
        risk: "mid" as const,
      },
      {
        code: "02031.HK",
        name: "宏元食品",
        actual: -14.1,
        score: 31,
        reason: "上市前舆情未纳入特征",
        risk: "low" as const,
      },
    ],
    fpSamples: [
      {
        code: "01810.HK",
        name: "极氪科技",
        actual: 8.2,
        score: 68,
        reason: "保荐人维稳与基石锁定",
        risk: "mid" as const,
      },
      {
        code: "03996.HK",
        name: "中国能建",
        actual: 4.6,
        score: 72,
        reason: "国资背景未充分加权",
        risk: "high" as const,
      },
    ],
    palette: ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)"].map(
      (c) => `oklch(${c})`,
    ),
    hoursSaved: 96,
    factorContributions: [
      { f: "对赌赎回触发条款", v: 0.082 },
      { f: "经营性现金流走势", v: 0.071 },
      { f: "客户集中度 Top5", v: 0.064 },
      { f: "关联交易比例", v: 0.052 },
      { f: "上市前板块景气", v: 0.041 },
      { f: "估值溢价 vs 同业", v: 0.037 },
      { f: "现金 runway 年", v: 0.028 },
    ],
    precision: 0.78,
    recall: 0.744,
    f1: 0.762,
  },
};
