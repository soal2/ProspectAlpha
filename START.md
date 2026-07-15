# ProspectAlpha 启动与使用说明

当前文档对应的运行架构：

- 前端：Vite，默认 `http://127.0.0.1:5173`
- 后端：FastAPI，默认 `http://127.0.0.1:8000`
- 检索：PostgreSQL `rag.prospectus_chunks`，使用 pgvector + pg_jieba
- PDF 文件：本地开发默认使用本地对象目录；Docker 全栈使用 MinIO
- 文档问答：每次请求必须指定一个 `document_id`，不会跨招股书检索
- Qdrant 和旧的 run-scoped retrieval index 已删除，不需要启动

## 一、首次准备

需要提前安装：

- Docker Desktop
- Python 3.11 或更高版本
- `uv`
- Node.js 与 `pnpm`

准备后端环境变量：

```bash
cd backend
cp .env.example .env
```

至少检查并填写以下配置，不要把真实密钥提交到 Git：

```dotenv
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=...
OPENAI_MODEL=...

# embedding 可使用 DashScope；也可把下面三项配置成兼容 OpenAI embeddings 的服务
DASHSCOPE_API_KEY=...
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSIONS=1024
```

安装后端和前端依赖：

```bash
cd backend
uv sync --extra pipeline --extra dev

cd ../frontend
pnpm install
```

`pipeline` extra 包含 MinerU 和 PostgreSQL 驱动。首次调用 MinerU 时可能下载较大的模型文件；普通前端 PDF 上传使用 PyMuPDF 提取页面文本块，不需要等待 MinerU 完整下载。

## 二、推荐的本地开发启动方式

该方式只在 Docker 中运行 PostgreSQL，后端与前端直接在本机启动，最适合调试“上传 PDF → 检索 → 问答 → PDF 定位”。

### 1. 启动 PostgreSQL

```bash
cd backend
docker compose up -d postgres
docker compose exec postgres pg_isready -U prospect_alpha -d prospect_alpha
```

数据库连接：

```text
postgresql+psycopg://prospect_alpha:prospect_alpha@127.0.0.1:5432/prospect_alpha
```

### 2. 启动后端

打开一个新终端：

```bash
cd backend
DATABASE_URL=postgresql+psycopg://prospect_alpha:prospect_alpha@127.0.0.1:5432/prospect_alpha \
ANALYSIS_EXECUTION_MODE=inline \
OBJECT_STORE_PROVIDER=local \
.venv/bin/uvicorn riskagent.api.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --env-file .env
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

预期输出：

```json
{"status":"ok"}
```

后端启动时会探测 embedding provider，因此有外部模型服务时启动可能需要几十秒。看到 `Application startup complete` 后再使用前端。

### 3. 启动前端

打开另一个新终端：

```bash
cd frontend
VITE_API_BASE_URL=http://127.0.0.1:8000 \
pnpm dev --host 127.0.0.1 --port 5173
```

浏览器访问：

```text
http://127.0.0.1:5173/intake
```

## 三、前端完整使用流程

1. 进入“新建 IPO 风险分析”。
2. 在“材料准备”区域点击“选择文件”，或把 PDF 拖入上传区域。
3. 等待按钮结束“上传、解析并建立索引中”状态。
4. 确认页面显示：
   - PDF 文件名、页数和文件大小；
   - “N 个检索块已入库”，其中 N 应大于 0；
   - “当前问答文档 ID：doc_...”。
5. 在“PDF 自由提问”中输入只与当前招股书相关的问题，点击“提问”。
6. 回答会流式输出；回答下方显示检索到的证据页码。
7. 点击某条 `P.N · 章节` 引用，PDF 查看器应打开对应页，并用黄色框高亮 bbox 原文。

一次问答只检索当前页面绑定的一个 `document_id`：

```text
POST /documents/{document_id}/ask
  -> PostgreSQL rag.prospectus_chunks
  -> WHERE document_id = 当前文档
  -> 流式回答 + page_number + bbox_regions
```

如果 PDF 已经上传过，可在“标的选择”输入文件名或公司关键词，点击“匹配标的”，再选择已有招股书，无需重复上传。

系统会同时读取两类文档：

- 当前前端通过 `/documents/upload` 上传的文档；
- 已经存在于 PostgreSQL `prospectus_documents`，且在 `rag.prospectus_chunks` 中有分块的历史招股书。

第二类文档会自动出现在前端列表中。选择它们时不会重新运行 MinerU、不会重新生成 embedding，而是直接复用 PostgreSQL 分块和原始 PDF。旧 MinerU 数据第一次参与问答时，后端会幂等地把历史 0-based 页码升级为公共 API 使用的 1-based 页码，并标记 bbox 为 `normalized_1000`；PDF 查看器会按页面宽高缩放，不会把归一化坐标误当成 PDF points。

## 四、命令行端到端验收

后端已经运行时，可以用真实 PDF 验证同一条业务链：

```bash
cd backend
API=http://127.0.0.1:8000 \
bash scripts/verify_e2e.sh /绝对路径/招股书.pdf
```

该脚本会：

1. 检查 `/health`；
2. 调用 `POST /documents/upload`；
3. 确认生成 `document_id` 和大于 0 的 `block_count`；
4. 调用 `POST /documents/{document_id}/ask`；
5. 确认回答包含同一 `document_id`、引用页码和 `bbox_regions`。

成功时输出类似：

```text
OK document=doc_xxx blocks=123 PostgreSQL RAG answer completed
```

上传结果也可以直接检查：

```bash
curl -F file=@/绝对路径/招股书.pdf \
  http://127.0.0.1:8000/documents/upload
```

## 五、Docker 后端全栈（可选）

需要 RQ worker、Redis 和 MinIO 时使用：

```bash
cd backend
docker compose up --build -d
docker compose ps
docker compose logs -f api worker
```

Docker 后端全栈包括：

| 服务 | 用途 | 端口 |
| --- | --- | --- |
| `api` | FastAPI | `8000` |
| `worker` | RQ 分析任务执行器 | 无公开端口 |
| `postgres` | 业务数据、pgvector、pg_jieba 检索 | `5432` |
| `redis` | RQ 队列 | `6379` |
| `minio` | PDF 对象存储 | `9000`，控制台 `9001` |

Docker Compose 会读取 `backend/.env` 中的模型密钥。全栈模式下 `ANALYSIS_EXECUTION_MODE=rq`，分析任务由 worker 执行；文档上传和文档问答 API 仍由 `api` 提供。

前端不在当前 Compose 文件中，仍按“启动前端”一节运行，并保持：

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## 六、停止服务

本地开发模式：

- 在前端和后端终端分别按 `Ctrl-C`；
- PostgreSQL 可继续保留，或执行：

```bash
cd backend
docker compose stop postgres
```

停止 Docker 全栈但保留数据：

```bash
cd backend
docker compose down
```

仅在明确要删除 PostgreSQL 和 MinIO 数据时使用：

```bash
cd backend
docker compose down -v
```

`-v` 会删除 Docker volumes，不应作为普通停止命令使用。

## 七、常用检查与排障

### 检查 PostgreSQL 检索分块

```bash
cd backend
docker compose exec postgres psql -U prospect_alpha -d prospect_alpha \
  -c "SELECT document_id, page_idx, left(text, 80), merged_bboxes FROM rag.prospectus_chunks ORDER BY created_at DESC LIMIT 10;"
```

`merged_bboxes` 必须按页保存；同一个 bbox union 不得跨页。

历史 MinerU 文档应满足：

```text
metadata.bbox_coordinate_space = normalized_1000
metadata.page_number_base = 1
```

前端快速上传的 PyMuPDF 文档使用 `pdf_points`。两种坐标都由同一个 PDF 查看器处理。

### 前端无法连接后端

依次确认：

```bash
curl http://127.0.0.1:8000/health
```

- 前端启动时的 `VITE_API_BASE_URL` 必须是 `http://127.0.0.1:8000`；
- 后端 `.env` 的 `CORS_ALLOW_ORIGINS` 必须包含 `http://127.0.0.1:5173`；
- 修改 Vite 环境变量后需要重启前端。

### 上传后 block_count 为 0

- 确认 PDF 有可提取的文字层；纯扫描图片 PDF 的快速上传路径可能无法提取文本；
- 查看后端终端报错；
- 确认 PostgreSQL 健康且 `DATABASE_URL` 不是 SQLite；
- 对扫描件或复杂版面使用 MinerU pipeline 解析流程。

### 问答失败或无引用

- 确认页面已经显示具体 `document_id` 和大于 0 的检索块数；
- 确认 `OPENAI_API_KEY`、模型和 base URL 有效；
- 确认 embedding key 已配置。未配置时会降级为 hashing vectors，检索质量会显著下降；
- 检查 `rag.prospectus_chunks` 是否存在该 `document_id` 的记录。

### PostgreSQL collation version warning

已有 volume 可能提示数据库与容器的 collation 版本不同。该警告通常不阻断本地检索，但生产环境应按 PostgreSQL 提示重建相关对象并执行 `ALTER DATABASE ... REFRESH COLLATION VERSION`，不要在未知影响时直接操作生产库。

## 八、提交前检查

```bash
cd backend
uv run pytest -q

cd ../frontend
pnpm run build
```

与 RAG 数据、证据定位和真实验收有关的设计记录见：

```text
backend/src/riskagent/RAGDESIGN.md
```
