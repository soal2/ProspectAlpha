# ProspectAlpha RAG 使用说明

本文档记录当前招股书 RAG 流程怎么启动、怎么调用、各模块负责什么，以及依赖哪些配置。

## 当前边界

当前 RAG 只服务招股书解析、分块、向量入库和检索。旧的 `/documents` 上传、PyMuPDF 文档解析、`document_assets.py` 旧资产仓库已经删除。

正式入口是：

```python
from riskagent.documents import ProspectusPipelineRequest, process_prospectus_document

result = process_prospectus_document(
    ProspectusPipelineRequest(
        source_path="data/raw/prospectus/ysyy.pdf",
        doc_key="ysyy",
        config_path="configs/rag.yaml",
        processed_root="data/processed",
        enable_image_vlm=True,
        index_chunks=True,
    )
)
```

端到端 smoke runner 是：

```bash
cd backend
uv run python scripts/run_ysyy_pipeline.py
```

这个脚本只负责跑样例和打印进度，不能复制一套 pipeline 逻辑。真实流程必须收敛在 `process_prospectus_document(...)`。

## 模块职责

### documents

文件：`backend/src/riskagent/documents.py`

职责：

- RAG 总编排入口。
- 调用 ingestion 注册原始 PDF。
- 调用 parsing 用 MinerU 解析 PDF。
- 调用 chunker 把 MinerU `content_list.json` 转成统一 chunk。
- 调用 retrieval/index 把 chunk embedding 后写入 PostgreSQL。

它不负责：

- PDF 解析细节。
- chunk 合并规则。
- VLM/embedding 的真实 API 调用细节。
- PostgreSQL 表结构和查询 SQL。

### ingestion

文件：`backend/src/riskagent/ingestion/prospectus.py`

职责：

- 接收 `source_path` 或 PDF bytes。
- 计算文件 hash。
- 生成稳定 `document_id`。
- 保存/复用原始 PDF：`backend/data/raw/prospectus/{doc_key}.pdf`。
- 把招股书元数据写入应用数据库表 `prospectus_documents`。

注意：

- `doc_key` 是人类可读别名，比如 `ysyy`。
- `document_id` 是数据库和 processed 目录使用的稳定 ID，比如 `doc_269f78d05439`。

### parsing

目录：`backend/src/riskagent/parsing/`

职责：

- `pdf_parser.py`：PDF processor factory，默认 `mineru`。
- `config.py`：读取 `configs/rag.yaml`。
- `chunker.py`：读取 MinerU `content_list.json`，生成统一 `DocumentChunk`。

MinerU 输出目录：

```text
backend/data/processed/{document_id}/ocr/
```

chunk 落盘目录：

```text
backend/data/processed/{document_id}/chunks/block/chunks.jsonl
backend/data/processed/{document_id}/chunks/block/image_descriptions.json
```

`chunks.jsonl` 是中间结果。流程中断后，可以用它排查和后续重跑入库。

### providers

目录：`backend/src/riskagent/providers/`

职责：

- `embedding.py`：embedding provider，默认 DashScope `text-embedding-v4`。
- `vllm.py`：图片 chunk 的 VLM 描述，默认 DashScope `qwen3.7-plus`。
- `config.py`：读取 `configs/providers.yaml`。

当前 VLM base URL：

```text
https://llm-brsq0ce4o43nxul5.cn-beijing.maas.aliyuncs.com/api/v1
```

### retrieval

目录：`backend/src/riskagent/retrieval/`

职责：

- `index.py`：PostgreSQL schema、pgvector、pg_jieba 检查、chunk 入库、检索。
- 支持三种检索模式：
  - `vector`：向量相似度。
  - `full_text`：PostgreSQL `jiebacfg` 全文检索。
  - `hybrid`：向量 + 全文组合。

检索必须传 `document_id`，避免跨招股书混搜。

## 配置文件

### RAG pipeline

文件：`backend/configs/rag.yaml`

关键项：

```yaml
pdf_processor:
  default: mineru
  mineru:
    method: ocr
    backend: pipeline
    lang: chinese_cht
    reuse_existing: true
    image_analysis: true

chunker:
  default: block
  block:
    max_chars: 1200
    include_types: [text, table, image]
    skip_types: [header, footer, page_number]
    merge_text: true
    require_image_description: true
    image_context_window_blocks: 3
```

常改项：

- `chunker.block.max_chars`：text/table 最大 chunk 字符数。
- `chunker.block.require_image_description`：是否要求 image chunk 必须有 VLM 描述。
- `pdf_processor.mineru.reuse_existing`：已有 MinerU 输出时是否复用。

### 模型 provider

文件：`backend/configs/providers.yaml`

默认：

- embedding：DashScope `text-embedding-v4`
- VLM：DashScope `qwen3.7-plus`

需要环境变量：

```bash
export DASHSCOPE_API_KEY="..."
```

不要把真实 key 提交到 git。

### 环境变量

本地建议复制：

```bash
cd backend
cp .env.example .env
```

至少确认：

```env
DATABASE_URL=postgresql+psycopg://prospect_alpha:prospect_alpha@localhost:5432/prospect_alpha
DASHSCOPE_API_KEY=
```

## PostgreSQL 依赖

RAG 入库和检索需要 PostgreSQL + pgvector + pg_jieba。

启动：

```bash
cd backend
docker compose up -d postgres
```

当前镜像由 `backend/docker/postgres/Dockerfile` 构建：

- 基于 `pgvector/pgvector:0.8.2-pg16`
- 编译安装 `pg_jieba`
- 初始化 `vector`、`pg_jieba`、`jiebacfg`

如果你复用了旧 volume，init SQL 不会重新执行。遇到 `pg_jieba` 或 `jiebacfg` 不存在时，要么手动执行 init SQL，要么重建 postgres volume。

连接测试：

```bash
cd backend
uv run python -c "from riskagent.database import check_database_connection; print(check_database_connection())"
```

## MinerU 依赖

默认 processor 是 MinerU。运行前需要 backend 虚拟环境已安装项目依赖：

```bash
cd backend
uv sync --extra pipeline --extra dev
```

如果 `reuse_existing: true` 且目标目录已经有：

```text
backend/data/processed/{document_id}/ocr/{document_id}_content_list.json
```

则会复用，不重新跑 MinerU。

## 数据路径

以 `ysyy` 为例：

原始 PDF：

```text
backend/data/raw/prospectus/ysyy.pdf
```

注册后：

```text
doc_key = ysyy
document_id = doc_xxxxxxxxxxxx
```

MinerU 解析输出：

```text
backend/data/processed/{document_id}/ocr/{document_id}_content_list.json
backend/data/processed/{document_id}/ocr/images/
```

chunk 中间结果：

```text
backend/data/processed/{document_id}/chunks/block/chunks.jsonl
```

图片 VLM 描述缓存：

```text
backend/data/processed/{document_id}/chunks/block/image_descriptions.json
```

## 端到端执行

准备：

```bash
cd backend
docker compose up -d postgres
export DASHSCOPE_API_KEY="..."
```

执行样例：

```bash
uv run python scripts/run_ysyy_pipeline.py
```

脚本会执行：

1. 注册 `data/raw/prospectus/ysyy.pdf`。
2. 解析到 `data/processed/{document_id}/ocr/`。
3. 分块并写 `chunks.jsonl`。
4. 对 image chunk 调 VLM 生成描述。
5. 对所有非空 chunk 生成 embedding。
6. 写入 PostgreSQL `rag` schema。
7. 分别跑 `vector`、`full_text`、`hybrid` 检索 smoke test。

## 直接检索

示例：

```python
from riskagent.retrieval import create_chunk_indexer

indexer = create_chunk_indexer("postgres", schema="rag")
try:
    results = indexer.search(
        query="BCR-ABL 是什么",
        document_id="doc_269f78d05439",
        mode="hybrid",
        limit=5,
    )
finally:
    indexer.close()
```

返回结果包含：

- `chunk_id`
- `document_id`
- `chunk_type`
- `text`
- `page_idx`
- `bbox_refs`
- `merged_bboxes`
- `image_path`
- `table_html`
- `metadata`
- `score`

前端跳转 PDF 时主要用 `page_idx`、`bbox_refs`、`merged_bboxes`。

## 常见问题

### 为什么没有 `/documents` 上传接口？

旧 `/documents` 上传接口和 PyMuPDF 文档块解析已经删除。现在招股书 RAG 的入口是 `process_prospectus_document(...)`，后续 API 应该围绕这个入口新建，而不是恢复旧 asset stack。

### 为什么 image chunk 要 VLM？

MinerU 的 image item 通常没有可 embedding 的文本。当前默认 `require_image_description: true`，因此 image chunk 会调用 VLM 生成描述，再进入统一 chunk schema 和 embedding。

### 为什么 table 有时候被拆？

超长 table 会按 chunk size 拆分，避免一个大表变成过大的 embedding 输入。拆分时会保留 source item 和 bbox 信息，前端可以跳回对应页面区域。

### 为什么全文检索不 fallback？

项目要求 PostgreSQL 自己做中文分词和全文检索。缺少 `pg_jieba` / `jiebacfg` 是环境错误，应该显式失败，而不是悄悄 fallback 到 Python 分词或 `ILIKE`。
