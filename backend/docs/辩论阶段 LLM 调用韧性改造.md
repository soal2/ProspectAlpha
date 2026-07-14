# 面试话术：辩论阶段 LLM 调用韧性改造

> 适用问题：请介绍一个你做过的有挑战性的工程改造 / 你是怎么处理线上稳定性问题的 / 讲一个你设计过的容错机制
>
> 项目背景：ProspectAlpha 港股 IPO 招股书风险识别系统，多 Agent 协作架构

---

## 一、一句话开场（30 秒电梯陈述）

"我最近在一个多 Agent 风险分析系统里，把辩论阶段的 LLM 调用从『完全裸调用』重构成了一个三层防御的韧性架构 —— 内层 tenacity 重试、中层部分失败容忍、外层熔断器，加上全链路结构化日志。改造覆盖 7 个文件、新增 8 个单测，91 个测试零回归。核心收益是把单次瞬时失败的爆炸半径从『整轮辩论作废』降到了『单条 finding 失败可降级』。"

---

## 二、问题发现：怎么定位出来的

> **面试官**：这个问题是怎么发现的？

"我们的系统里有一个『专家辩论』阶段 ——5 个不同领域的 Agent（财务、法律、市场等）会互相质证对方的 risk finding，第二轮根据质证意见修订，最后由一个 L3 红队 Agent 仲裁。整个辩论用 `asyncio.gather` 并发跑 N*(N-1) 条 critique，N=5 时一次辩论就是 20 条 LLM 调用。

代码 review 的时候，我注意到 `debate.py` 第 74-126 行的 `_generate_critique` 和 `_generate_revision`，直接 `get_default_llm_client().chat_json(...)` 裸调，**完全没有 try-except**。当时第一反应是『这不科学』，就去看了上层 `_run_debate_stage` 怎么处理的 —— 发现外层有个 `except Exception` 兜底，失败就跳过辩论直接进 cross_validation。

表面上看好像有安全网，但我顺着这条链路一推就发现两个真实问题："

---

## 三、根因分析：为什么不能只加 try-except

> **面试官**：那为什么不直接在外层那个 try-except 上加个重试就好？

"我分析下来，根本不是『加个重试』能解决的。问题有三层：

### 问题 1：`asyncio.gather` 的失败传播是『一损俱损』

`asyncio.gather(*tasks)` 默认 `return_exceptions=False`，**任意一个 task 抛错，整个 gather 就抛**。这意味着 N=5 时 20 条 critique 里只要有 1 条触发 `openai.APITimeoutError`，整轮 20 条全部作废 —— 包括已经成功的 19 条。再用上层 `except Exception` 一接，整个辩论阶段直接跳过。100% 的辩论价值归零，就因为 5% 的瞬时失败率。

### 问题 2：异常不分类，重试策略无法分流

`chat_json` 把 `openai.APITimeoutError`、`RateLimitError`、`InternalServerError`（**这些都该重试**）和 JSON 解析失败（**重试也没用，同样的 prompt 大概率出同样的错**）全部以原生类型上抛。上层只能 `except Exception` 一刀切，根本没法做"瞬时重试 / 永久失败"的策略分流。

### 问题 3：没有熔断器，可能雪崩

最坏情况：N=5 专家 × 20 critique × 3 次重试 = 60 次并发请求打到上游 LLM 服务。如果上游已经挂了，这 60 次请求会全部失败然后被外层吞掉，紧接着下一个分析 run 又来 60 次 —— 没有任何机制能在『上游持续不可用』时快速失败，把后端挡在 LLM 服务前面。"

---

## 四、方案设计：三层防御

> **面试官**：那你的方案是什么？

"我设计了一个三层防御，每一层解决一个不同尺度的问题：

| 层级 | 作用域 | 解决的问题 | 机制 |
|------|--------|-----------|------|
| 内层 | 单次调用 | 瞬时网络抖动 / 限流 | tenacity 指数退避重试（仅对 LLMTransientError 生效） |
| 中层 | 单轮 gather | 部分 finding 失败不该连累整轮 | `gather(return_exceptions=True)` + 失败比例聚合 |
| 外层 | 进程级 | 上游服务持续不可用时挡住雪崩 | CircuitBreaker：CLOSED → OPEN → HALF_OPEN |

### 关键设计决策

**1. 异常分类用名字匹配，不强依赖 openai 包**
我们的测试环境不一定装 openai SDK。所以我做了一个 `_TRANSIENT_OPENAI_EXC_NAMES = {"APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError"}` 集合，用 `type(exc).__name__` 匹配。这样测试可以用假的 exception 类，生产时分类逻辑又能正确识别 SDK 异常。

**2. Pydantic ValidationError 不重试**
LLM 返回了 JSON 但 schema 不对，重试意义不大 —— 同样的 prompt 大概率出同样的破输出。所以 ValidationError 直接抛上去，由中层 gather 容忍。

**3. 部分失败容忍，但全部失败必须抛**
中层 gather 改成 `return_exceptions=True` 后，原本『任一失败=整体失败』变成了『全部失败=整体失败』。我在 `DebatePhaseResult` 上加了 `partial_failure_count` 字段，让下游（前端 trace 页、报告生成）知道辩论结果是否完整可信。但所有 critique 全失败时，必须抛 `DebateLLMError(phase="critique", failed=N, total=N)`，否则仲裁阶段拿不到输入，后面会出更隐蔽的 bug。

**4. 熔断器是进程级单例**
多 worker 部署时各自熔断状态独立 —— 这是一个已知的妥协，文档化下来。如果未来需要跨进程协调，可以上 Redis。

**5. 全链路结构化日志约定**
每个层级有自己的事件名前缀，便于 grep：
- LLM 边界：`llm_call_ok` / `llm_transient_exhausted` / `llm_parse_failed`
- 熔断器：`circuit_breaker_open` / `circuit_breaker_state_change`
- 辩论层：`critique_partial_failure` / `revision_partial_failure` / `debate_arbiter_failed`
- 管道层：复用既有 `debate_round_failed`，但 payload 加上 `phase/failed/total/detail` 结构化字段

所有日志都带 `run_id / phase / agent_name / attempt / exception_type`，线上故障可以按 grep 还原完整链路。"

---

## 五、实施细节：关键文件与代码

> **面试官**：具体怎么落地的？能讲讲关键代码吗？

"改动覆盖 7 个文件：

### 新增 3 个文件

**`providers/llm.py` 扩展异常层级**
```python
class LLMError(RuntimeError): ...           # 所有调用期错误基类
class LLMTransientError(LLMError): ...      # 可重试：超时、连接、429、5xx
class LLMParseError(LLMError): ...          # 不可重试：JSON 解析失败
```
`chat_json` 把 `client.chat.completions.create` 包在 try-except 里，通过 `_classify_openai_error` 归类。

**`agents/llm_resilience.py`（新文件，核心）**
- `CircuitBreaker` 类：CLOSED → OPEN（连续 N 次失败）→ HALF_OPEN（冷却后探活）→ CLOSED（探活成功）
- 滚动窗口计数：`deque[float]` 存最近 60 秒的失败时间戳
- `call_llm_with_resilience(...)`：用 `tenacity.AsyncRetrying` 配 `retry_if_exception_type(LLMTransientError)` + `wait_exponential_jitter(initial=1.0, max=8.0)` + `stop_after_attempt(3)`
- 环境变量可调：`LLM_MAX_ATTEMPTS / LLM_RETRY_INITIAL_SECONDS / LLM_RETRY_MAX_SECONDS / LLM_CIRCUIT_FAILURE_THRESHOLD / LLM_CIRCUIT_RECOVERY_SECONDS`

**`agents/exceptions.py`（新文件）**
```python
class DebateLLMError(RuntimeError):
    def __init__(self, *, phase, failed, total, detail): ...
```
带结构化属性，方便上游 handler 提取信息塞到事件 payload。

### 改造 3 个既有文件

**`agents/debate.py`**
- `_generate_critique` / `_generate_revision` 裸 `chat_json` 全换成 `call_llm_with_resilience(...)`，传入 `context={"phase": "critique", "critic": ..., "target": ...}` 供日志关联
- `run_debate_phase` 改造：`gather(return_exceptions=True)` → 拆分 success/fail → 100% 失败才抛 `DebateLLMError`
- 加了 `_split_gather_results` 和 `_failure_record` 辅助函数，统一失败日志格式
- `DebatePhaseResult` 加 `partial_failure_count` 字段，`history` 加 `failures: {"critique": [...], "revision": [...]}` 节点供前端可视化降级节点

**`agents/orchestrator.py`**
`arbitrate_debate` 函数里的 `chat_json` 换成 `call_llm_with_resilience(...)`，函数签名不变。

**`analysis_runs.py`**
`_run_debate_stage` 的 `except Exception` 拆成两个分支：
- `except DebateLLMError as exc:` —— payload 注入 `phase/failed/total/detail` 结构化字段
- `except Exception as exc:` —— 保留原有兜底行为

### `pyproject.toml`
显式声明 `tenacity>=8.2.0`（之前只是 openai 的传递依赖，存在漂移风险）。"

---

## 六、测试策略：怎么保证零回归

> **面试官**：测试这块你怎么设计的？

"测试我分了三层，8 个新测试：

### 单元层：韧性组件本身（`test_llm_resilience.py`，7 个）

1. `test_classify_openai_error_distinguishes_transient_from_persistent` —— 验证 SDK 异常名字正确归类
2. `test_chat_json_wraps_openai_sdk_errors_as_llm_errors` —— 注入假的 `openai` 模块到 `sys.modules`，验证 `chat_json` 把 SDK 异常翻译成我们的层级
3. `test_resilience_retries_transient_then_succeeds` —— 第一次抛 `LLMTransientError`、第二次成功，断言调用 2 次、最终成功、熔断器保持 CLOSED
4. `test_resilience_does_not_retry_parse_error` —— 抛 `LLMParseError`，断言只调用 1 次（不重试）、熔断器不计失败
5. `test_circuit_breaker_trips_after_threshold_failures` —— 连续 N 次失败后断言 `allow() == False`
6. `test_circuit_breaker_recovers_after_cooldown` —— 用 fake clock 推进时间，断言 OPEN → HALF_OPEN → CLOSED 的完整状态机
7. `test_call_rejected_when_breaker_open` —— 熔断 OPEN 时调用直接抛 `CircuitBreakerOpen`，**断言底层 LLM 调用次数为 0**（这是熔断器的核心价值证明）

### 集成层：辩论阶段的部分失败容忍（`test_debate_phase.py`，+2 个）

8. `test_debate_phase_continues_with_partial_critique_failures` —— 3 专家 6 条 critique，故意让 1 条抛 `LLMTransientError`，断言：辩论仍返回；`partial_failure_count == 1`；`history["failures"]["critique"]` 长度 1；revision 和 arbitration 仍正常执行；最终 `decision == "proceed"`
9. `test_debate_phase_raises_DebateLLMError_when_all_critiques_fail` —— 6 条全失败，断言抛 `DebateLLMError`，且 `exc.phase == "critique"`、`exc.failed == exc.total == 6`

### 端到端层：管道降级行为（`test_analysis_runs.py`，+1 个）

10. `test_run_debate_stage_emits_structured_failure_event_on_DebateLLMError` —— monkeypatch `run_debate_phase_sync` 让它抛 `DebateLLMError(phase="arbitration", failed=1, total=1)`，断言：
   - run 最终 `status == "completed"`（降级成功）
   - 有且仅有 1 条 `debate_round_failed` 事件
   - 事件 payload 含 `phase / failed / total / detail` 四个结构化字段

### 测试中的两个关键设计决策

**依赖注入而非全局 mock**：`call_llm_with_resilience` 接受 `client=` 和 `breaker=` kwarg，测试可以直接传入 doubles，不必 monkeypatch 全局单例。`get_default_llm_client()` 是延迟解析（每次 attempt 调用时才取），测试用 `monkeypatch.setattr(llm_resilience_module, "get_default_llm_client", lambda: fake_llm)` 在调用间无缝替换。

**测试速度**：所有触发重试路径的测试都 `monkeypatch.setattr(resilience, "LLM_RETRY_INITIAL_SECONDS", 0.0)` + `LLM_RETRY_MAX_SECONDS=0.0`，避免 3 次重试的退避等待拖慢测试。整个 91 个测试套件 60 秒内跑完。

### 回归结果

```
91 passed, 6 warnings in 59.78s
```
原有 83 个测试零回归，新增 8 个全部通过。"

---

## 七、面试收尾：反思与可改进点

> **面试官**：这个方案有什么可以改进的地方？

"有几个我主动标识的妥协：

**1. 熔断器是进程级单例，不是分布式**
多 worker 部署时各自熔断状态独立。这意味着 4 个 worker 部署时，上游需要承受 4 × threshold 次失败才会被全部挡住。短期可接受，长期如需跨进程协调应该上 Redis 共享状态。

**2. Pydantic ValidationError 还在直接抛**
LLM 返回了 JSON 但 schema 不符时，目前是抛错被外层 gather 容忍。更优雅的做法是返回一个『降级 placeholder critique』，但这需要业务上定义什么是可接受的降级内容，留给下一轮迭代。

**3. 重试放大请求量**
N=5 时单轮最坏 60 次调用（20 × 3 次重试）。我用熔断器在 5 次连续失败后切断，但更彻底的做法是给每个 agent 单独的 quota / rate limiter。这是个待评估的优化点。

**4. 没有真实的混沌测试**
单元测试用 fake clock、fake LLM，能验证逻辑正确性但验证不了真实 SDK 行为。生产侧我建议临时配置一个会偶发 503 的 LLM endpoint 做一次完整 run 的灰度验证，看日志里是否真的能看到 `llm_transient_exhausted` 和 `critique_partial_failure` 事件。

---

## 八、整体复盘：可迁移的方法论

如果让我提炼这次改造的可迁移经验，大概是这三条：

**1. 看到『裸调用』要顺着调用链往下推**
不是看到没 try-except 就加 try-except。要往下推一层，看上层是怎么兜底的，再判断当前缺的是『分类』、『降级』还是『隔离』。这次外层其实有 `except Exception`，但它的存在反而掩盖了问题。

**2. 异常分类是容错的前提**
没有 `LLMTransientError` vs `LLMParseError` 的分类，重试策略、熔断策略全都是空中楼阁。**先分类，再谈策略**。这一步投入产出比最高。

**3. 三层防御的尺度划分**
内层（per-call）解决瞬时、中层（per-batch）解决部分失败、外层（per-service）解决雪崩。每一层的尺度不同，机制也不同，不要试图用一层包打天下。"

---

## 附录：关键文件清单

| 文件 | 角色 | 关键改动 |
|------|------|----------|
| `backend/pyproject.toml` | 依赖 | + `tenacity>=8.2.0` |
| `backend/src/riskagent/providers/llm.py` | LLM 边界 | 异常分类层级 + `chat_json` 异常归类 |
| `backend/src/riskagent/agents/exceptions.py` | 新建 | `DebateLLMError(phase, failed, total, detail)` |
| `backend/src/riskagent/agents/llm_resilience.py` | 新建，核心 | `CircuitBreaker` + `call_llm_with_resilience` |
| `backend/src/riskagent/agents/debate.py` | 辩论阶段 | 韧性调用 + `gather(return_exceptions=True)` + 部分失败容忍 |
| `backend/src/riskagent/agents/orchestrator.py` | 仲裁 | `arbitrate_debate` 改用韧性调用 |
| `backend/src/riskagent/analysis_runs.py` | 管道 | `_run_debate_stage` 拆分 `DebateLLMError` 分支 |
| `backend/tests/test_llm_resilience.py` | 新建，7 个测试 | 异常分类 / 重试 / 熔断器 |
| `backend/tests/test_debate_phase.py` | 扩展，+2 测试 | 部分失败容忍 / 全部失败抛错 |
| `backend/tests/test_analysis_runs.py` | 扩展，+1 测试 | 结构化降级事件 |

## 附录：核心配置默认值

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `LLM_MAX_ATTEMPTS` | 3 | 单次调用最多重试次数 |
| `LLM_RETRY_INITIAL_SECONDS` | 1.0 | 指数退避初始间隔 |
| `LLM_RETRY_MAX_SECONDS` | 8.0 | 退避上限 |
| `LLM_CIRCUIT_FAILURE_THRESHOLD` | 5 | 触发熔断的连续失败次数 |
| `LLM_CIRCUIT_RECOVERY_SECONDS` | 30.0 | OPEN → HALF_OPEN 冷却时间 |
| `LLM_CIRCUIT_WINDOW_SECONDS` | 60.0 | 滚动窗口长度 |
