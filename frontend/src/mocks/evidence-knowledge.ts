export interface KnowledgePreview {
  evidenceId: string;
  code: string;
  section: string;
  page: number;
  linkedConclusions: string[];
}

export interface KnowledgeStats {
  totalEvidence: number;
  totalCompanies: number;
}

export interface KnowledgeFacet {
  label: string;
  items: string[];
}

export interface KnowledgeEvidence {
  id: string;
  code: string;
  name: string;
  section: string;
  page: number;
  conf: number;
  snippet: string;
  risk: string;
}

export interface KnowledgeData {
  facets: KnowledgeFacet[];
  evidence: KnowledgeEvidence[];
  stats: KnowledgeStats;
  preview: KnowledgePreview;
}

export const knowledgeData: KnowledgeData = {
  facets: [
    { label: "公司", items: ["全部", "宁德时代", "京东健康", "蚂蚁集团", "理想汽车", "美的集团"] },
    { label: "招股书章节", items: ["全部", "风险因素", "业务", "财务资料", "董事及高管", "股本"] },
    { label: "风险类别", items: ["全部", "财务", "经营", "法律", "市场"] },
    { label: "置信度", items: ["全部", "≥ 0.9 高", "0.7–0.9 中", "< 0.7 低"] },
  ],
  evidence: [
    {
      id: "e_898aa168",
      code: "03750.HK",
      name: "宁德时代",
      section: "财务资料",
      page: 218,
      conf: 0.93,
      snippet:
        "本集团 2024 年度经营性现金流净额为人民币 712.4 亿元，同比下降 18.6%，主要由于动力电池业务存货周转周期延长……",
      risk: "经营现金流",
    },
    {
      id: "e_5e8c343e",
      code: "06618.HK",
      name: "京东健康",
      section: "风险因素",
      page: 47,
      conf: 0.88,
      snippet:
        "倘若与京东集团的关联交易安排不能按照协议持续，本公司收益可能受到重大不利影响。截至往绩记录期末，相关关联收入占比 43.6%。",
      risk: "关联交易",
    },
    {
      id: "e_74ee12b1",
      code: "09988.HK",
      name: "蚂蚁集团",
      section: "风险因素",
      page: 92,
      conf: 0.95,
      snippet:
        "本公司部分业务受到不断变化的金融监管规则约束，包括但不限于资本充足率、网络小贷新规、消费者保护规定……",
      risk: "监管合规",
    },
    {
      id: "e_3551480d",
      code: "02015.HK",
      name: "理想汽车",
      section: "业务",
      page: 156,
      conf: 0.81,
      snippet:
        "前五大客户占公司销售收入比例约 12.4%。但前五大供应商占采购总额比例为 58.9%，存在一定供应商集中度风险。",
      risk: "供应商集中度",
    },
    {
      id: "e_1cdca7b3",
      code: "01024.HK",
      name: "快手生物",
      section: "风险因素",
      page: 31,
      conf: 0.91,
      snippet:
        "本公司核心管线 KS-01 仍处于临床二期阶段，截至最后实际可行日期尚未实现商业化收入，存在重大研发失败风险。",
      risk: "核心管线",
    },
    {
      id: "e_a4111a75",
      code: "00300.HK",
      name: "美的集团",
      section: "财务资料",
      page: 198,
      conf: 0.86,
      snippet:
        "截至 2024 年 12 月 31 日，本集团有息负债总额为人民币 421.6 亿元，资产负债率 64.8%，处于行业平均水平。",
      risk: "负债率",
    },
  ],
  stats: {
    totalEvidence: 4218,
    totalCompanies: 25,
  },
  preview: {
    evidenceId: "e_898aa168",
    code: "03750.HK",
    section: "财务资料",
    page: 218,
    linkedConclusions: [
      "经营现金流连续两期下滑",
      "现金 runway 仍 > 5 年（中风险）",
      "与同业宁波容百对比偏弱",
    ],
  },
};
