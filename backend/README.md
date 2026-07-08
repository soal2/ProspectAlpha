# ProspectAlpha Backend

港股 IPO RiskAgent 流水线的 FastAPI 运行时：负责招股书入库、风险规则管理、分析运行编排与证据检索。本目录是后端的实际运行实现，不再只是模块骨架。

## 当前能力概览

后端已经具备以下运行时能力（对应 `riskagent` 包内已落地的模块）：

- **FastAPI 应用**：`riskagent.api.main:app`，挂载 5 个路由模块（`risk_rules` / `agents` / `analysis_options` / `documents` / `analysis`），外加 `/health` 健康检查。
- **SQLAlchemy 持久化层**：`database.py` 提供引擎、会话工厂与 `Base`；`models.py` 定义 6 张运行时表。
- **文档入库与查询**：`documents.py` + `parsing/`，支持 PDF 与纯文本入库、`sha256` 去重、文本切块与页码定位。
- **providers 抽象层**：`providers/embedding.py`、`providers/object_store.py`（local 或 MinIO）、`providers/vector_store.py`（默认 in-memory，可切 Qdrant）。
- **分析运行编排**：`analysis_runs.py` 实现运行状态机（`clarifying` / `ready` / `running` / `completed`），支持 `inline` 与 `rq` 两种执行模式。
- **RQ worker**：`workers/rq_worker.py`，控制台入口 `prospect-alpha-worker`，监听 `analysis` 队列。
- **多智能体编排**：`agent_experts.py` 提供 Agent 专家目录，`agents/supervisor.py` 负责分析过程编排。
- **混合检索**：`retrieval/hybrid.py` 提供 BM25 + 向量召回的混合检索能力。
- **风险规则体系**：`risk_rules.py` 加载 `configs/risk_rules.yaml` 与 `configs/hk_ipo_risk_rules_v1.json`，并暴露规则 CRUD 与快照接口。
- **测试套件**：`tests/` 覆盖 worker、analysis pipeline contracts、retrieval hybrid、supervisor、agent experts、risk rules 等。

## 模块地图

```text
backend/
├── configs/                    # 风险 taxonomy、风险规则、agent graph、模型与特征配置
├── data/                       # 本地开发数据目录（raw / processed / gold_labels）
├── docs/backend_api.md         # FastAPI 路由的完整 API 文档
├── docker-compose.yml          # api + worker + postgres + redis + qdrant + minio
├── Dockerfile
├── pyproject.toml
└── src/riskagent/
    ├── api/                    # FastAPI 应用与路由
    │   ├── main.py             # create_app、CORS、/health、路由注册
    │   ├── risk_rules.py       # /risk-rules CRUD
    │   ├── agents.py           # /agents 专家目录
    │   ├── analysis_options.py # /analysis-options 选项聚合
    │   ├── documents.py        # /documents 上传与查询
    │   ├── analysis.py         # /analysis-runs 状态机与事件
    │   ├── evidence.py         # 证据相关占位路由
    │   └── health.py
    ├── database.py             # SQLAlchemy 引擎、SessionLocal、init_db()
    ├── models.py               # 6 张运行时表的 ORM（见下方"数据模型"）
    ├── documents.py            # 文档资产入库、解析、切块
    ├── analysis_runs.py        # 分析运行状态机、事件流、findings
    ├── risk_rules.py           # 风险规则加载、CRUD 与规则集快照
    ├── agent_experts.py        # Agent 专家目录（前端 expert guild 数据源）
    ├── agents/                 # 多智能体协同
    │   ├── supervisor.py       # 分析过程编排
    │   ├── orchestrator.py     # 占位
    │   ├── financial.py / legal.py / business.py / market.py / reporter.py / verifier.py  # 仍是骨架占位
    ├── skills/                 # 可复用 Agent Skill
    │   ├── risk_extract.py     # 已接入风险规则体系
    │   ├── evidence_verify.py / report_generate.py  # 仍是骨架占位
    ├── providers/              # 外部依赖抽象
    │   ├── embedding.py
    │   ├── object_store.py     # local 或 MinIO
    │   └── vector_store.py     # in-memory（默认）或 Qdrant
    ├── workers/                # 分析任务执行
    │   ├── analysis.py         # 任务体
    │   └── rq_worker.py        # RQ worker 入口（prospect-alpha-worker）
    ├── retrieval/
    │   ├── hybrid.py           # 已实现：BM25 + 向量混合检索
    │   ├── evidence_store.py / full_text.py / vector.py  # 占位
    ├── ingestion/              # 数据接入（占位）
    ├── parsing/                # PDF 解析、章节识别、表格抽取（占位）
    ├── features/               # 财务 / 经营 / 法务 / 市场风险特征（占位）
    ├── prediction/             # 规则评分卡与后续 ML 模型服务（占位）
    ├── backtesting/            # 回测评估（占位）
    ├── reporting/              # Markdown / HTML / PDF 报告生成（占位）
    └── common/
        ├── cache.py            # 运行内缓存
        ├── config.py / logging.py / exceptions.py / types.py
```

## 数据模型

`src/riskagent/models.py` 定义了 6 张运行时表：

| ORM 类 | 表名 | 用途 |
| --- | --- | --- |
| `DocumentAssetRow` | `documents` | 招股书文件资产（`file_sha256` 去重、`page_count`、`text_chars`、`block_count` 等） |
| `DocumentBlockRow` | `document_blocks` | 文档切块（`page_number`、`section_path`、`bbox`、`char_start/end`、`source_hash`） |
| `AnalysisRunRow` | `analysis_runs` | 分析运行实例（`status`、`current_stage`、`inputs_frozen`、`payload`） |
| `EvidenceLibraryRow` | `evidence_library` | 跨运行证据库（`dedupe_key` 去重、`source_run_id`、`document_block_id`） |
| `RetrievalIndexRow` | `retrieval_index` | 检索索引（`terms`、`embedding`、`payload`） |
| `AgentExpertRow` | `agent_experts` | Agent 专家目录（前端 expert guild 页数据源） |

默认情况下数据库由 `database.py:init_db()` 调用 `Base.metadata.create_all` 初始化，落库到 `data/processed/prospect_alpha.db`（SQLite）；生产环境通过 `DATABASE_URL` 切到 PostgreSQL。

## 运行时架构

```text
HTTP 请求
   │
   ▼
FastAPI 路由（api/）
   │
   ▼
analysis_runs 状态机（analysis_runs.py）
   │
   ├──► inline 模式：同进程内执行
   └──► rq 模式：投递到 Redis 的 analysis 队列
                │
                ▼
           RQ worker（workers/rq_worker.py）
                │
                ▼
           分析任务体（workers/analysis.py）
                │
                ▼
  providers：database / vector_store / object_store / embedding
                │
                ▼
  事件、findings、证据 → 写回 PostgreSQL / SQLite
                │
                ▼
  前端通过 /analysis-runs/{id}/events 轮询读取
```

## 配置

参考 `.env.example`，关键环境变量：

| 变量 | 用途 |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy 数据库连接串；未设置时回退到本地 SQLite |
| `REDIS_URL` | RQ 队列依赖的 Redis 连接 |
| `ANALYSIS_EXECUTION_MODE` | `inline`（开发默认）或 `rq`（生产） |
| `ANALYSIS_QUEUE_NAME` | RQ 队列名，默认 `analysis` |
| `QDRANT_URL` / `VECTOR_STORE_PROVIDER` | 向量检索服务地址与 provider 选择 |
| `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` / `MINIO_SECURE` | MinIO 对象存储连接 |
| `OBJECT_STORE_PROVIDER` / `OBJECT_STORE_BUCKET` / `OBJECT_STORE_LOCAL_ROOT` | 对象存储 provider（`minio` 或 `local`）与 bucket / 本地根目录 |
| `CORS_ALLOW_ORIGINS` | 逗号分隔的允许跨域来源 |
| `LLM_PROVIDER` / `LLM_API_KEY` | LLM provider 与密钥（agent 推理使用） |

## 本地运行

完整命令见根目录 `START.md`，最短路径：

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
python -m pip install -U pip setuptools wheel
python -m pip install -e ".[pipeline,dev]"

# Inline 模式（开发推荐）
ANALYSIS_EXECUTION_MODE=inline uvicorn riskagent.api.main:app --host 127.0.0.1 --port 8000
```

需要 RQ 异步执行时，另起一个终端：

```bash
prospect-alpha-worker  # 等价于 python -m riskagent.workers.rq_worker
```

Docker 全栈启动（含 postgres / redis / qdrant / minio）：

```bash
cd backend
docker compose up --build
```

## 测试

```bash
cd backend
.venv/bin/python -m pytest -q
```

a778037 引入或扩展的关键测试：`test_workers.py`、`test_analysis_pipeline_contracts.py`、`test_retrieval_hybrid.py`、`test_agents_supervisor.py`、`test_agent_experts.py`、`test_analysis_runs.py`、`test_risk_rules.py`、`test_risk_rules_api.py`。

## API 文档

完整的请求/响应契约见 [`docs/backend_api.md`](docs/backend_api.md)。路由前缀汇总：

| 路径 | 模块 |
| --- | --- |
| `GET /health` | `api/main.py` |
| `/agents` | `api/agents.py` |
| `/analysis-options` | `api/analysis_options.py` |
| `/documents`、`/documents/upload` | `api/documents.py` |
| `/analysis-runs[/...]` | `api/analysis.py` |
| `/risk-rules[/...]` | `api/risk_rules.py` |

启动后也可访问 `http://127.0.0.1:8000/docs` 查看 OpenAPI 交互式文档。

## 仍待实现（Roadmap）

诚实标注尚未真正落地的部分，便于协作时设定期望：

- **专家 Agent**：`agents/financial.py`、`agents/legal.py`、`agents/business.py`、`agents/market.py`、`agents/orchestrator.py`、`agents/reporter.py`、`agents/verifier.py` 目前仍是骨架占位；只有 `agents/supervisor.py` 提供编排逻辑。
- **Skill**：`skills/evidence_verify.py` 与 `skills/report_generate.py` 仍为占位。
- **数据接入与解析**：`ingestion/` 与 `parsing/` 的具体实现仍待补齐。
- **风险特征抽取**：`features/` 仍为占位。
- **预测与评分卡**：`prediction/` 仍为占位。
- **回测评估**：`backtesting/` 仍为占位。
- **报告生成**：`reporting/` 仍为占位（Markdown / HTML / PDF）。
- **数据库迁移**：当前依赖 `init_db()` 的 `create_all`，尚未引入 Alembic 迁移（`pyproject.toml` 已把 `alembic` 列入 `pipeline` 可选依赖）。
