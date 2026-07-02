export interface TraceStage {
  key: string;
  label: string;
  owner: string;
  status: "done" | "running" | "queued";
  cost?: string;
}

export interface TraceSeedItem {
  who: string;
  kind: "execute" | "discover" | "challenge" | "verdict" | "assign";
  text: string;
  t: string;
}

export interface TraceFlowItem {
  who: string;
  kind: "execute" | "discover" | "challenge" | "verdict" | "assign";
  text: string;
  t: string;
}

export interface EvidenceFeedItem {
  id: string;
  source: string;
  title: string;
  conf: number;
  time: string;
}

export interface TraceData {
  stages: TraceStage[];
  seed: TraceSeedItem[];
  flow: TraceFlowItem[];
  evidenceFeed: EvidenceFeedItem[];
}

export const traceData: Record<string, TraceData> = {
  ana_03750: {
    stages: [
      { key: "intake", label: "需求理解 · 元数据对齐", owner: "沈砚", status: "done" },
      { key: "dispatch", label: "编排派遣 · 分组任务", owner: "沈砚", status: "done" },
      { key: "evidence", label: "证据采集 · 招股书检索", owner: "程哲", status: "running" },
      { key: "cross", label: "交叉分析 · 多专家协同", owner: "林清越", status: "queued" },
      { key: "write", label: "报告撰写 · 结构化输出", owner: "林清越", status: "queued" },
      { key: "qa", label: "质检审裁 · 红队回路", owner: "周翊", status: "queued" },
      { key: "sign", label: "签发交付", owner: "沈砚", status: "queued" },
    ],
    seed: [
      {
        who: "沈砚",
        kind: "execute",
        text: "指派 蔡宝（财报采集专家）：负责商业模式和增长指标抽取",
        t: "00:02",
      },
      {
        who: "沈砚",
        kind: "execute",
        text: "指派 温叙白（法务条款专家）：扫描对赌、赎回、关联交易、特殊股东权利",
        t: "00:03",
      },
      {
        who: "程哲",
        kind: "execute",
        text: "开始深度检索「招股书 · 风险因素」: 客户集中度、供应商集中度、关联交易、回购义务、最新动态 2026",
        t: "00:09",
      },
      {
        who: "程哲",
        kind: "discover",
        text: "「风险因素」聚合到 124 条候选片段，去重后保留 47 条",
        t: "00:18",
      },
      {
        who: "蔡宝",
        kind: "discover",
        text: "三大报表抽取完成 · 净利润 +8.2% · 经营性现金流 −18.6%",
        t: "00:24",
      },
      {
        who: "温叙白",
        kind: "discover",
        text: "发现「特殊股东权利」第 28 项 ——若上市后 12 个月内未达营收 Y，触发硬性回购",
        t: "00:31",
      },
      {
        who: "周翊",
        kind: "challenge",
        text: "对蔡宝的现金流结论提出存疑：是否区分了应收账款变动与真实经营恶化？",
        t: "00:38",
      },
      {
        who: "蔡宝",
        kind: "execute",
        text: "重新拆分：应收增加占 41%、存货占 27%、剩余为经营性恶化",
        t: "00:46",
      },
    ],
    flow: [
      {
        who: "沈砚",
        kind: "assign",
        text: "你是 Verda 首席指挥官。从专家名册中为本次调研挑选最合适的团队。规则：必须含 1 位 L3 决策层统筹、1-2 位 L2 策略顾问、3-6 位 L1 执行专家。",
        t: "00:00",
      },
      {
        who: "沈砚",
        kind: "verdict",
        text: '组队完成：{"lead":"L3-001","members":["L2-001","L2-002","L1-001","L1-002","L1-003","L1-005","L1-006"]}',
        t: "00:01",
      },
      {
        who: "程哲",
        kind: "execute",
        text: "深度检索「招股书 · 风险因素」: 客户集中度、供应商集中度、关联交易、回购义务、最新动态 2026",
        t: "00:03",
      },
      {
        who: "程哲",
        kind: "discover",
        text: "「风险因素」聚合到 124 条候选片段，去重后保留 47 条 · 已存入证据库",
        t: "00:11",
      },
      {
        who: "蔡宝",
        kind: "discover",
        text: "三大报表抽取完成：净利润 +8.2% · 经营性现金流 −18.6% · 自由现金流首次为负",
        t: "00:18",
      },
      {
        who: "温叙白",
        kind: "discover",
        text: "发现「特殊股东权利」第 28 项 ——若上市后 12 个月内未达营收 Y，触发硬性回购",
        t: "00:24",
      },
      {
        who: "周翊",
        kind: "challenge",
        text: "对蔡宝的现金流结论提出存疑：是否区分了应收账款变动与真实经营恶化？",
        t: "00:32",
      },
      {
        who: "蔡宝",
        kind: "execute",
        text: "重新拆分：应收账款增加 41%、存货增加 27%、剩余为真实经营性恶化（32%）",
        t: "00:41",
      },
      {
        who: "苏明哲",
        kind: "discover",
        text: "结合行业景气与近期 IPO 表现，给出破发概率推断：0.34（Top 30% 风险分位）",
        t: "00:48",
      },
      {
        who: "林清越",
        kind: "verdict",
        text: "终审通过 14 章节正文 · 综合风险得分 58 · 中风险 · 主要风险：对赌赎回 + 现金流",
        t: "00:57",
      },
    ],
    evidenceFeed: [
      { id: "#1", source: "官方", title: "招股书 P.218 · 现金流量表", conf: 0.93, time: "近实时" },
      {
        id: "#2",
        source: "web",
        title: "海通证券 · 宁德时代深度报告 2026Q2",
        conf: 0.62,
        time: "近一年",
      },
      {
        id: "#3",
        source: "web",
        title: "公司公告 · 关于回购义务的补充披露",
        conf: 0.81,
        time: "近半年",
      },
      {
        id: "#4",
        source: "官方",
        title: "招股书 P.92 · 风险因素 · 关联交易",
        conf: 0.88,
        time: "近实时",
      },
      {
        id: "#5",
        source: "web",
        title: "Wind · 同业宁波容百 2025 年度报告",
        conf: 0.54,
        time: "近一年",
      },
    ],
  },
};
