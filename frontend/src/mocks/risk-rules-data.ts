export interface RiskRule {
  rule_id: string;
  dimension_code: string;
  dimension: string;
  rule_type: string;
  category: string;
  risk_item: string;
  description: string;
  trigger_condition: string;
  calculation_or_method: string;
  source_sections: string;
  enabled: boolean;
}

export interface RiskDimension {
  dimension_code: string;
  dimension: string;
  rule_type: string;
  category_count: number;
  rule_count: number;
  description?: string;
  categories: { name: string; rule_count: number }[];
}

export interface RiskRulesData {
  rule_set_id?: string;
  version?: string;
  name?: string;
  source_workbook?: string;
  source_main_sheet?: string;
  source_summary_sheet?: string;
  scope?: string;
  usage_notes: string[];
  totals: {
    dimension_count: number;
    category_count: number;
    rule_count: number;
  };
  dimensions: RiskDimension[];
  rules: RiskRule[];
}

export const riskRulesData: RiskRulesData = {
  // rule_set_id: "hk_ipo_prospectus_risk_framework_v1",
  // version: "v1.0",
  // name: "港股IPO招股书风险识别框架",
  // source_workbook: "港股IPO招股书风险识别框架_7.3.xlsx",
  // scope: "港股 IPO 招股说明书风险识别",
  usage_notes: ["应用时需根据具体公司招股书逐项核验。"],
  totals: {
    dimension_count: 3,
    category_count: 4,
    rule_count: 6,
  },
  dimensions: [
    {
      dimension_code: "D1",
      dimension: "维度1：标准化财务指标风险",
      rule_type: "quantitative_financial",
      category_count: 2,
      rule_count: 3,
      categories: [
        { name: "盈利能力", rule_count: 1 },
        { name: "现金流", rule_count: 2 },
      ],
    },
    {
      dimension_code: "D2",
      dimension: "维度2：非标隐性风险",
      rule_type: "qualitative_disclosure",
      category_count: 1,
      rule_count: 2,
      categories: [{ name: "关联交易", rule_count: 2 }],
    },
    {
      dimension_code: "D3",
      dimension: "维度3：文本粉饰度风险",
      rule_type: "textual_nlp",
      category_count: 1,
      rule_count: 1,
      categories: [{ name: "语言模糊性", rule_count: 1 }],
    },
  ],
  rules: [
    {
      rule_id: "HKIPO-001",
      dimension_code: "D1",
      dimension: "维度1：标准化财务指标风险",
      rule_type: "quantitative_financial",
      category: "盈利能力",
      risk_item: "毛利率偏低或持续下滑",
      description: "毛利率低于行业平均水平或报告期内呈持续下降趋势。",
      trigger_condition: "毛利率 < 行业均值×0.85 或连续2期下降幅度 > 5个百分点",
      calculation_or_method: "毛利率 = (营业收入 - 营业成本) / 营业收入 × 100%",
      source_sections: "综合损益表 / 财务资料章节",
      enabled: true,
    },
    {
      rule_id: "HKIPO-002",
      dimension_code: "D1",
      dimension: "维度1：标准化财务指标风险",
      rule_type: "quantitative_financial",
      category: "现金流",
      risk_item: "经营现金流连续为负",
      description: "经营活动现金流持续为负，可能反映回款能力或商业模式质量不足。",
      trigger_condition: "经营现金流连续2期为负",
      calculation_or_method: "读取现金流量表经营活动现金流净额",
      source_sections: "现金流量表 / 财务资料章节",
      enabled: true,
    },
    {
      rule_id: "HKIPO-003",
      dimension_code: "D2",
      dimension: "维度2：非标隐性风险",
      rule_type: "qualitative_disclosure",
      category: "关联交易",
      risk_item: "关联交易占比较高",
      description: "关联交易占比较高，需核验商业合理性和定价公允性。",
      trigger_condition: "关联交易金额 / 收入或采购额 > 20%",
      calculation_or_method: "抽取关联交易表并计算占比",
      source_sections: "关联交易 / 业务章节",
      enabled: true,
    },
    {
      rule_id: "HKIPO-004",
      dimension_code: "D2",
      dimension: "维度2：非标隐性风险",
      rule_type: "qualitative_disclosure",
      category: "关联交易",
      risk_item: "关联方依赖未充分披露",
      description: "对关联方销售、采购或资金往来解释不足。",
      trigger_condition: "存在关联方依赖但缺少定价或替代方案说明",
      calculation_or_method: "章节检索 + 表格抽取 + 说明完整性检查",
      source_sections: "关联交易 / 风险因素",
      enabled: true,
    },
    {
      rule_id: "HKIPO-005",
      dimension_code: "D3",
      dimension: "维度3：文本粉饰度风险",
      rule_type: "textual_nlp",
      category: "语言模糊性",
      risk_item: "模糊限定词密度高",
      description: "频繁使用可能、一定程度、通常等模糊限定词。",
      trigger_condition: "模糊限定词密度 > 6%",
      calculation_or_method: "模糊词密度 = 模糊限定词总数 / 章节总词数",
      source_sections: "风险因素 / 合约安排",
      enabled: true,
    },
    {
      rule_id: "HKIPO-006",
      dimension_code: "D1",
      dimension: "维度1：标准化财务指标风险",
      rule_type: "quantitative_financial",
      category: "现金流",
      risk_item: "现金 runway 较短",
      description: "可用现金相对经营消耗不足。",
      trigger_condition: "现金 runway < 18个月",
      calculation_or_method: "期末现金 / 月均经营现金净流出",
      source_sections: "资产负债表 / 现金流量表",
      enabled: true,
    },
  ],
};
