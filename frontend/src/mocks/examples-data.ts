export interface HomepageExample {
  icon: string;
  title: string;
  desc: string;
}

export const homepageExamples: HomepageExample[] = [
  {
    icon: "Building2",
    title: "新能源车 IPO 交叉审视",
    desc: "对比理想 / 极氪 / 蔚来招股书的定价博弈、现金 runway 与股东权利结构。",
  },
  {
    icon: "ShieldAlert",
    title: "对赌与赎回条款扫描",
    desc: "全量识别特殊权利、回购义务、赎回触发条款与实控人变更风险。",
  },
  {
    icon: "FileText",
    title: "财务健康度深度诊断",
    desc: "解析三大报表，量化经营现金流、造血能力与资产减值敞口。",
  },
  {
    icon: "LineChart",
    title: "破发概率预警建模",
    desc: "结合上市前情绪、同业发行表现与市场流动性估算破发概率区间。",
  },
];
