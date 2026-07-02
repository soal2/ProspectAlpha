export interface RiskRule {
  code: string;
  label: string;
  high: string;
  mid: string;
  weight: number;
  source: string;
}

export interface RiskGroup {
  key: string;
  label: string;
  color: string;
  rules: RiskRule[];
}

export interface RiskRulesData {
  taxonomy: RiskGroup[];
}

export const riskRulesData: RiskRulesData = {
  taxonomy: [
    {
      key: "financial",
      label: "财务风险",
      color: "var(--chart-1)",
      rules: [
        {
          code: "unprofitable",
          label: "未盈利",
          high: "净利润 < 0 持续 ≥ 2 期",
          mid: "净利润 < 0 持续 1 期",
          weight: 12,
          source: "财务报表",
        },
        {
          code: "neg_cf",
          label: "经营现金流为负",
          high: "连续 ≥ 2 期",
          mid: "1 期",
          weight: 14,
          source: "现金流量表",
        },
        {
          code: "cash_runway",
          label: "现金 runway",
          high: "< 1.5 年",
          mid: "< 3.0 年",
          weight: 11,
          source: "现金 + 季度烧钱",
        },
        {
          code: "gross_margin",
          label: "毛利下滑",
          high: "> 5pct yoy",
          mid: "> 2pct yoy",
          weight: 6,
          source: "利润表",
        },
        {
          code: "debt_ratio",
          label: "高负债率",
          high: "> 70%",
          mid: "> 55%",
          weight: 7,
          source: "资产负债表",
        },
      ],
    },
    {
      key: "business",
      label: "经营风险",
      color: "var(--chart-2)",
      rules: [
        {
          code: "customer_cc",
          label: "客户集中度",
          high: "Top5 > 70%",
          mid: "Top5 > 40%",
          weight: 9,
          source: "业务章节",
        },
        {
          code: "supplier_cc",
          label: "供应商集中度",
          high: "Top5 > 75%",
          mid: "Top5 > 50%",
          weight: 6,
          source: "采购章节",
        },
        {
          code: "single_prod",
          label: "单一产品依赖",
          high: "营收占比 > 70%",
          mid: "> 50%",
          weight: 7,
          source: "营收构成",
        },
        {
          code: "pipeline",
          label: "核心管线未商业化",
          high: "无收入 & 临床 ≤ II",
          mid: "首款产品未放量",
          weight: 9,
          source: "研发管线",
        },
      ],
    },
    {
      key: "legal",
      label: "法律合规风险",
      color: "var(--chart-4)",
      rules: [
        {
          code: "redemption",
          label: "对赌/赎回条款",
          high: "存在硬性赎回触发",
          mid: "存在但可豁免",
          weight: 11,
          source: "法律意见书",
        },
        {
          code: "repurchase",
          label: "回购义务",
          high: "金额 > 净资产 30%",
          mid: "> 10%",
          weight: 6,
          source: "股本章节",
        },
        {
          code: "related",
          label: "关联交易",
          high: "营收占比 > 40%",
          mid: "> 20%",
          weight: 8,
          source: "关联方披露",
        },
        {
          code: "litigation",
          label: "重大诉讼",
          high: "未决标的 > 净资产 10%",
          mid: "> 3%",
          weight: 5,
          source: "诉讼披露",
        },
      ],
    },
    {
      key: "market",
      label: "市场风险",
      color: "var(--chart-5)",
      rules: [
        {
          code: "weak_market",
          label: "上市前市场情绪",
          high: "板块 30 日 < −10%",
          mid: "< −3%",
          weight: 5,
          source: "上市前行情",
        },
        {
          code: "ipo_break",
          label: "近期新股破发率",
          high: "> 60%",
          mid: "> 40%",
          weight: 5,
          source: "同期 IPO",
        },
        {
          code: "valuation",
          label: "估值溢价",
          high: "PE > 同业 1.8×",
          mid: "> 1.3×",
          weight: 5,
          source: "同业对标",
        },
      ],
    },
  ],
};
