---
name: cashflow-analyzer
version: 1.0.0
description: 基于招股书多期财务字段与原文证据评估经营造血、自由现金流、现金跑道、融资依赖和偿债风险。
owners: [financial_agent]
execution: callable
input_schema: CashflowSkillInput
output_schema: CashflowSkillResult
failure_policy: partial
---

# 现金流穿透分析

## 输入要求

- 多期现金及现金等价物、经营/投资/融资现金流、资本支出、收入、净利润和债务字段。
- 每个字段携带期间、币种、单位和 PDF 证据 ID。

## 输出

- 经营造血、盈利质量、自由现金流、资本支出、融资依赖、现金跑道、营运资金和偿债能力风险。
- 计算口径、趋势、风险等级、证据引用与缺失字段。

## 禁止事项

- 不使用示例或默认财务数据。
- 缺少关键字段时返回证据不足，不以零值代替缺失值。
