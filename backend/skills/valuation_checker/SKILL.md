---
name: valuation-checker
version: 1.0.0
description: 使用版本化静态同行数据比较发行人估值，输出口径透明、可复现的估值风险结论。
owners: [financial_agent]
execution: callable
input_schema: ValuationSkillInput
output_schema: ValuationSkillResult
failure_policy: partial
---

# 同行估值比对

## 输入

- 发行人 P/E、P/S、EV/EBITDA 或 P/B 及招股书证据。
- 冻结的同行样本、数据日期、币种、财年和估值口径。

## 输出

- 实际采用的可比指标、发行人倍数、同行中位数和溢折价。
- 同行选择依据、数据快照 ID、证据引用、风险等级与限制。

## 约束

- 缺少共同估值口径时返回证据不足。
- 不把静态历史倍数描述为当前行情，不输出投资建议。
