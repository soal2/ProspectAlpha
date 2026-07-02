export interface EvidenceDetailItem {
  id: string;
  section: string;
  page: number;
  conf: number;
  snippet: string;
  risk: string;
}

export const evidenceDetailItems: Record<string, EvidenceDetailItem[]> = {
  ana_03750: [
    {
      id: "e_898aa168",
      section: "财务资料",
      page: 218,
      conf: 0.93,
      snippet: "本集团 2024 年度经营性现金流净额为人民币 712.4 亿元，同比下降 18.6%……",
      risk: "经营现金流",
    },
    {
      id: "e_5e8c343e",
      section: "风险因素",
      page: 47,
      conf: 0.88,
      snippet: "倘若与主要客户的长期供应协议无法续签，可能对本公司业务构成不利影响……",
      risk: "客户集中度",
    },
    {
      id: "e_74ee12b1",
      section: "业务",
      page: 92,
      conf: 0.81,
      snippet: "本集团动力电池业务存货周转天数从 78 天延长至 96 天……",
      risk: "存货周转",
    },
    {
      id: "e_fcab5c47",
      section: "财务资料",
      page: 188,
      conf: 0.95,
      snippet:
        "截至最后实际可行日期，公司现金及等价物余额为 1 542 亿元，可覆盖未来约 6.4 年的经营性支出。",
      risk: "现金 runway",
    },
    {
      id: "e_3551480d",
      section: "股本",
      page: 312,
      conf: 0.86,
      snippet: "投资人 A 享有反摊薄保护、清算优先权及特殊董事提名权……",
      risk: "特殊股东权利",
    },
    {
      id: "e_1cdca7b3",
      section: "风险因素",
      page: 31,
      conf: 0.91,
      snippet: "若公司未能在 2027 年前实现指定营收里程碑，需向投资人 A 回购股份……",
      risk: "回购义务",
    },
  ],
};
