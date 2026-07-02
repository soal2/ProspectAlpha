export interface ReportData {
  id: string;
  toc: string[];
  radar: { dim: string; score: number }[];
  pie: { name: string; value: number }[];
  waterfall: { name: string; value: number }[];
  cashflow: { y: string; cfo: number; ni: number; cap: number }[];
  palette: string[];
}

export const reportData: Record<string, ReportData> = {
  ana_03750: {
    id: "ana_03750",
    toc: [
      "执行摘要 · 风险总评",
      "公司概览与发行信息",
      "财务健康度 · 盈利与现金流",
      "客户与供应商集中度",
      "核心业务 / 管线风险",
      "法律合规与对赌赎回",
      "关联交易 & 特殊股东权利",
      "行业 & 市场情绪（上市前）",
      "估值与定价博弈",
      "募资用途与资金压力",
      "同业对标",
      "风险评分卡 · 规则贡献度",
      "ML 模型预测 · SHAP 解释",
      "投研建议 & 人工复核意见",
    ],
    radar: [
      { dim: "财务", score: 64 },
      { dim: "经营", score: 58 },
      { dim: "法律", score: 78 },
      { dim: "市场", score: 42 },
      { dim: "技术", score: 36 },
      { dim: "治理", score: 52 },
    ],
    pie: [
      { name: "宁德时代", value: 35 },
      { name: "比亚迪", value: 28 },
      { name: "LG 新能源", value: 14 },
      { name: "松下", value: 8 },
      { name: "三星 SDI", value: 7 },
      { name: "其他", value: 8 },
    ],
    waterfall: [
      { name: "基线", value: 30 },
      { name: "+ 对赌条款", value: 12 },
      { name: "+ 客户集中度", value: 8 },
      { name: "+ 现金流恶化", value: 6 },
      { name: "− 现金 runway 足", value: -4 },
      { name: "+ 监管不确定", value: 6 },
      { name: "= 综合得分", value: 58 },
    ],
    cashflow: [
      { y: "2021", cfo: 410, ni: 159, cap: -180 },
      { y: "2022", cfo: 612, ni: 307, cap: -260 },
      { y: "2023", cfo: 875, ni: 451, cap: -320 },
      { y: "2024", cfo: 712, ni: 489, cap: -410 },
    ],
    palette: [
      "var(--chart-1)",
      "var(--chart-2)",
      "var(--chart-3)",
      "var(--chart-4)",
      "var(--chart-5)",
      "var(--chart-6)",
    ].map((c) => `oklch(${c})`),
  },
};
