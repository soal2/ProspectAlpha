---
name: legal-compliance-due-diligence
version: 1.0.0
description: 对港股 IPO 招股书中的 VIE、关联交易、牌照、处罚、诉讼、对赌、担保、税务与数据合规事项执行可溯源核查。
owners: [legal_agent]
execution: callable
input_schema: LegalComplianceSkillInput
output_schema: LegalComplianceSkillResult
failure_policy: partial
---

# 法务合规尽调

只基于本次任务提供的规则、招股书条款和冻结证据作出判断。

## 输出要求

- 逐项列明法律事项、适用规则、风险影响和待补文件。
- 每项判断关联招股书原文、页码和证据 ID。
- 缺少适用规则或原文时返回证据不足，不推断违法。

## 禁止事项

- 不以一般行业经验替代适用法律或招股书原文。
- 不把待确认事项表述为已发生违法行为。
