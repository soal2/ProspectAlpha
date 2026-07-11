# 多阶段分析流程卡顿问题分析与故障解决报告

## 问题现象

分析任务长时间停留在“专家编排 / 多阶段分析计划生成”阶段，前端无法继续展示证据抽取、风险识别和报告生成进度。

## 根因

1. API 启动分析使用同步 inline 执行，长 PDF 会阻塞启动请求。
2. 进入证据抽取阶段后未立即持久化阶段状态，前端持续显示“专家编排”。
3. 大型招股书每次保存都重复序列化大量 `document_blocks`，造成高 CPU、长文件锁和慢响应。
4. YAML 存储非原子读写，后台写入期间前端读取可能得到不完整内容。
5. `USE_DATABASE=0` 时检索索引和 Agent 配置仍可能访问全局 SQLite，造成锁竞争。
6. 旧任务恢复后，前端将历史 `evidence_found` 事件重复合并，导致证据数量显示偏大。

## 解决方案

- API `/analysis-runs/{id}/start` 改为后台线程派发，立即返回 queued 状态。
- SSE 检测到非终态任务停留在已完成阶段时，自动恢复执行，并对同一任务做线程去重。
- 进入 `evidence_extraction` 后立即保存阶段状态。
- YAML run store 增加路径级锁和原子替换写入。
- 已保存的 `document_blocks` 在后续保存时复用，避免重复序列化大型 PDF。
- `USE_DATABASE=0` 时使用隔离的本地检索 SQLite 和 seed Agent 配置。
- 完成态前端只使用 canonical evidence feed，避免合并历史重复事件。

## 验证结果

- `run_b27f62b3d414` 已恢复完成。
- 九个阶段全部完成，进度为 100%。
- 前端显示“分析已结束”、证据库和“交付完成”。
- Backend：15 个目标测试通过。
- Frontend：`pnpm lint && pnpm build` 通过，仅有既有 Fast Refresh warnings。
- `git diff --check` 通过。

## Git 回溯历史

| SHA | 提交 | 作用 |
| --- | --- | --- |
| `93110d2` | `feat: 增强分析运行的调度功能，支持线程模式和恢复机制` | 本次故障修复：后台线程、任务恢复、SQLite 隔离、YAML 并发安全、阶段状态持久化和重复证据处理。 |
| `765277e` | `feat: Refactor analysis trace page and enhance evidence handling` | 建立多 Agent 风险调研工作台、Domain Skills、SSE Trace、证据库和报告页面。 |
| `6d42086` | `feat：为分析运行添加问答功能和编排流程` | 增加分析编排流程、Agent supervisor、分析 API 和前端协作流程。 |
| `7b2e8e9` | `feat: 接入后端风险规则体系` | 接入港股 IPO 风险规则库、规则 API、规则抽取和基础分析流程。 |

本次修复的主要回溯入口：`93110d2`。

