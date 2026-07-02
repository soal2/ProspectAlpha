export interface BacktestSummary {
  id: string;
  name: string;
  model: string;
  modelVersion: string;
  samples: number;
  auc: number;
  ks: number;
  lift10: number;
  recall: number;
  createdAt: string;
  status: "完成" | "运行中" | "失败";
  author: string;
}

export const backtests: BacktestSummary[] = [
  {
    id: "bt_2026q2",
    name: "2024–2026 H1 · 港股全量回测",
    model: "RiskAgent v2.4 + 规则评分卡",
    modelVersion: "v2.4.1",
    samples: 186,
    auc: 0.812,
    ks: 0.486,
    lift10: 3.4,
    recall: 0.78,
    createdAt: "2026-06-24 09:12",
    status: "完成",
    author: "Soal2",
  },
  {
    id: "bt_bio_2026",
    name: "生物科技子板块 · 18A 专项",
    model: "RiskAgent v2.4 (bio)",
    modelVersion: "v2.4.1-bio",
    samples: 64,
    auc: 0.776,
    ks: 0.452,
    lift10: 2.9,
    recall: 0.72,
    createdAt: "2026-06-22 16:30",
    status: "完成",
    author: "蔡宝",
  },
  {
    id: "bt_2025_all",
    name: "2025 全年 · 基准回测",
    model: "RiskAgent v2.3",
    modelVersion: "v2.3.0",
    samples: 142,
    auc: 0.792,
    ks: 0.471,
    lift10: 3.1,
    recall: 0.74,
    createdAt: "2026-06-20 11:08",
    status: "完成",
    author: "沈砚",
  },
  {
    id: "bt_running",
    name: "新能源板块 · 阈值调优",
    model: "RiskAgent v2.4",
    modelVersion: "v2.4.1",
    samples: 38,
    auc: 0,
    ks: 0,
    lift10: 0,
    recall: 0,
    createdAt: "2026-06-25 08:40",
    status: "运行中",
    author: "苏明哲",
  },
];
