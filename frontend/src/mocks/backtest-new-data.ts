export interface BacktestNewStep {
  key: string;
  label: string;
  desc: string;
}

export interface BacktestNewData {
  steps: BacktestNewStep[];
}

export const backtestNewData: BacktestNewData = {
  steps: [
    { key: "scope", label: "样本范围", desc: "选择回测的港股 IPO 样本时间窗口、行业、规模" },
    { key: "model", label: "模型版本", desc: "选择 RiskAgent 模型版本与特征集" },
    { key: "thresh", label: "风险阈值", desc: "定义高 / 中 / 低风险的判定边界" },
    { key: "metric", label: "评估口径", desc: "选择评估窗口与指标" },
  ],
};
