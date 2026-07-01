// Centralized mock data for HK-IPO RiskAgent
export type RiskLevel = "high" | "mid" | "low";

export interface AnalysisSummary {
  id: string;
  code: string;
  name: string;
  industry: string;
  listingDate: string;
  offerPrice: number;
  offerSize: string;
  riskLevel: RiskLevel;
  riskScore: number; // 0-100
  evidenceCount: number;
  highConfidence: number;
  mode: "quick" | "deep" | "expert";
  createdAt: string;
  experts: number;
}

export const analyses: AnalysisSummary[] = [
  {
    id: "ana_03750",
    code: "03750.HK",
    name: "宁德时代",
    industry: "新能源 · 动力电池",
    listingDate: "2025-05-20",
    offerPrice: 263.0,
    offerSize: "410.1 亿",
    riskLevel: "mid",
    riskScore: 58,
    evidenceCount: 312,
    highConfidence: 187,
    mode: "expert",
    createdAt: "2026-06-24 17:24",
    experts: 9,
  },
  {
    id: "ana_00300",
    code: "00300.HK",
    name: "美的集团",
    industry: "家电制造",
    listingDate: "2024-09-17",
    offerPrice: 54.8,
    offerSize: "356.7 亿",
    riskLevel: "low",
    riskScore: 28,
    evidenceCount: 256,
    highConfidence: 198,
    mode: "deep",
    createdAt: "2026-06-24 14:18",
    experts: 7,
  },
  {
    id: "ana_06618",
    code: "06618.HK",
    name: "京东健康",
    industry: "互联网医疗",
    listingDate: "2026-02-12",
    offerPrice: 70.58,
    offerSize: "264.6 亿",
    riskLevel: "high",
    riskScore: 78,
    evidenceCount: 198,
    highConfidence: 91,
    mode: "expert",
    createdAt: "2026-06-23 11:02",
    experts: 9,
  },
  {
    id: "ana_02015",
    code: "02015.HK",
    name: "理想汽车-W",
    industry: "新能源汽车",
    listingDate: "2025-08-12",
    offerPrice: 118.0,
    offerSize: "115.5 亿",
    riskLevel: "mid",
    riskScore: 52,
    evidenceCount: 221,
    highConfidence: 142,
    mode: "deep",
    createdAt: "2026-06-22 09:41",
    experts: 7,
  },
  {
    id: "ana_09988",
    code: "09988.HK",
    name: "蚂蚁集团",
    industry: "金融科技",
    listingDate: "2026-04-08",
    offerPrice: 80.0,
    offerSize: "1340 亿",
    riskLevel: "high",
    riskScore: 82,
    evidenceCount: 412,
    highConfidence: 246,
    mode: "expert",
    createdAt: "2026-06-21 16:55",
    experts: 12,
  },
  {
    id: "ana_01810",
    code: "01810.HK",
    name: "极氪科技",
    industry: "新能源汽车",
    listingDate: "2026-03-21",
    offerPrice: 26.5,
    offerSize: "44.1 亿",
    riskLevel: "mid",
    riskScore: 61,
    evidenceCount: 168,
    highConfidence: 102,
    mode: "deep",
    createdAt: "2026-06-20 10:12",
    experts: 7,
  },
  {
    id: "ana_02331",
    code: "02331.HK",
    name: "李宁控股",
    industry: "运动鞋服",
    listingDate: "2025-11-30",
    offerPrice: 32.4,
    offerSize: "68.0 亿",
    riskLevel: "low",
    riskScore: 31,
    evidenceCount: 142,
    highConfidence: 119,
    mode: "quick",
    createdAt: "2026-06-19 14:22",
    experts: 5,
  },
  {
    id: "ana_01024",
    code: "01024.HK",
    name: "快手生物",
    industry: "生物科技",
    listingDate: "2026-01-15",
    offerPrice: 18.0,
    offerSize: "30.2 亿",
    riskLevel: "high",
    riskScore: 84,
    evidenceCount: 188,
    highConfidence: 96,
    mode: "expert",
    createdAt: "2026-06-18 19:03",
    experts: 9,
  },
  {
    id: "ana_06699",
    code: "06699.HK",
    name: "盛源物流",
    industry: "供应链物流",
    listingDate: "2026-05-09",
    offerPrice: 12.6,
    offerSize: "21.4 亿",
    riskLevel: "mid",
    riskScore: 49,
    evidenceCount: 154,
    highConfidence: 88,
    mode: "deep",
    createdAt: "2026-06-17 12:45",
    experts: 7,
  },
];

export interface Agent {
  id: string;
  name: string;
  role: string;
  layer: "L3" | "L2" | "L1";
  desc: string;
  expertise: string[];
  callCount: number;
  avgTokens: number;
  avatar: string; // emoji or initial
  accent: string; // hex
}

export const agents: Agent[] = [
  { id: "L3-001", name: "沈砚", role: "首席指挥官 · 总监", layer: "L3", desc: "统筹全局，拆解风险维度，组建专家队、终审报告并签发。", expertise: ["策略", "总控", "报告"], callCount: 312, avgTokens: 4820, avatar: "沈", accent: "#0b243d" },
  { id: "L3-002", name: "林清越", role: "首席分析师", layer: "L3", desc: "把控分析质量与逻辑严谨性，裁定结论是否成立。", expertise: ["质检", "逻辑"], callCount: 287, avgTokens: 3960, avatar: "林", accent: "#173a5e" },
  { id: "L3-003", name: "周翊", role: "质量总检 · 红队", layer: "L3", desc: "扮演魔鬼代言人，主导事实校验与反馈闭环，决定是否打回。", expertise: ["红队", "事实核查"], callCount: 198, avgTokens: 3210, avatar: "周", accent: "#b42318" },
  { id: "L2-001", name: "苏明哲", role: "风险策略顾问", layer: "L2", desc: "从行业格局给出战略级判断与风险权重建议。", expertise: ["策略", "行业"], callCount: 156, avgTokens: 2850, avatar: "苏", accent: "#1e3a5f" },
  { id: "L2-002", name: "何知微", role: "定价策略师", layer: "L2", desc: "拆解发行定价模型、估值溢价与博弈，识别破发风险。", expertise: ["估值", "定价"], callCount: 142, avgTokens: 2640, avatar: "何", accent: "#c9a35b" },
  { id: "L2-003", name: "顾晚晴", role: "用户研究 · 路演解读", layer: "L2", desc: "构建投资者画像、解读路演问答、归纳市场口碑。", expertise: ["路演", "舆情"], callCount: 121, avgTokens: 2310, avatar: "顾", accent: "#7b8ba1" },
  { id: "L1-001", name: "蔡宝", role: "财报采集 · 财务分析", layer: "L1", desc: "解析三大报表、毛利与现金流，识别盈利模式与造血能力。", expertise: ["财务", "现金流"], callCount: 412, avgTokens: 2120, avatar: "蔡", accent: "#3b6fa0" },
  { id: "L1-002", name: "江竞洲", role: "市场情报 · 竞品", layer: "L1", desc: "系统化追踪同业动态、市场份额、客户与供应商集中度。", expertise: ["竞品", "市场"], callCount: 388, avgTokens: 1980, avatar: "江", accent: "#2c5f7a" },
  { id: "L1-003", name: "温叙白", role: "法务合规 · 条款", layer: "L1", desc: "拆解对赌、赎回、关联交易、特殊股东权利与诉讼条款。", expertise: ["法务", "合规"], callCount: 296, avgTokens: 2480, avatar: "温", accent: "#4a5a7a"},
  { id: "L1-004", name: "陆则铭", role: "技术与管线", layer: "L1", desc: "评估技术壁垒、研发投入、管线进展与商业化里程碑。", expertise: ["研发", "管线"], callCount: 218, avgTokens: 2280, avatar: "陆", accent: "#b54708" },
  { id: "L1-005", name: "程哲", role: "证据检索 · 文档", layer: "L1", desc: "在招股书全文跨章节检索证据片段，定位页码与 bbox。", expertise: ["检索", "RAG"], callCount: 562, avgTokens: 1240, avatar: "程", accent: "#4a5568" },
  { id: "L1-006", name: "白宛", role: "市场情绪 · 行情", layer: "L1", desc: "解读上市前市场情绪、板块流动性、近期新股表现与发行窗口。", expertise: ["行情", "情绪"], callCount: 247, avgTokens: 1860, avatar: "白", accent: "#8a6a3b" },
];

export const dashboardStats = {
  totalAnalyses: 25,
  evidenceCount: 4218,
  conclusions: 312,
  crossValidationRate: 68,
  hoursSaved: 96,
  efficiency: 24,
  avgSources: 5.6,
  tokenK: 2480,
};

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
  { id: "bt_2026q2", name: "2024–2026 H1 · 港股全量回测", model: "RiskAgent v2.4 + 规则评分卡", modelVersion: "v2.4.1", samples: 186, auc: 0.812, ks: 0.486, lift10: 3.4, recall: 0.78, createdAt: "2026-06-24 09:12", status: "完成", author: "林研究员" },
  { id: "bt_bio_2026", name: "生物科技子板块 · 18A 专项", model: "RiskAgent v2.4 (bio)", modelVersion: "v2.4.1-bio", samples: 64, auc: 0.776, ks: 0.452, lift10: 2.9, recall: 0.72, createdAt: "2026-06-22 16:30", status: "完成", author: "蔡宝" },
  { id: "bt_2025_all", name: "2025 全年 · 基准回测", model: "RiskAgent v2.3", modelVersion: "v2.3.0", samples: 142, auc: 0.792, ks: 0.471, lift10: 3.1, recall: 0.74, createdAt: "2026-06-20 11:08", status: "完成", author: "沈砚" },
  { id: "bt_running", name: "新能源板块 · 阈值调优", model: "RiskAgent v2.4", modelVersion: "v2.4.1", samples: 38, auc: 0, ks: 0, lift10: 0, recall: 0, createdAt: "2026-06-25 08:40", status: "运行中", author: "苏明哲" },
];
