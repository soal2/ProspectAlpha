---
name: backtest
version: 1.0.0
description: 使用版本化历史上市表现离线评估风险和情绪信号有效性，不参与单次发行人尽调。
owners: [offline]
execution: offline
failure_policy: optional
---

# 离线回测

用于模型开发与质量评估，衡量历史信号的区分度、稳定性、误报和漏报。

回测结果不能作为新 IPO 的发行人证据，不能进入运行时 Agent prompt，也不能决定单次报告是否完成。
