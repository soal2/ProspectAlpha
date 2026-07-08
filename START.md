# 启动命令

## 后端本地启动

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
python -m pip install -U pip setuptools wheel
python -m pip install -e ".[pipeline,dev]"
ANALYSIS_EXECUTION_MODE=inline uvicorn riskagent.api.main:app --host 127.0.0.1 --port 8000
```

## 前端本地启动

```bash
cd frontend
pnpm install
VITE_USE_MOCK=false VITE_API_BASE_URL=http://127.0.0.1:8000 pnpm dev --host 127.0.0.1 --port 5173
```

## Docker 全栈启动

```bash
cd backend
docker compose up --build
```

## Docker 容器说明

### api

后端 FastAPI 服务。

```bash
docker compose up --build api
```

- 启动命令：`uvicorn riskagent.api.main:app --host 0.0.0.0 --port 8000`
- 端口：`8000`
- 依赖：`postgres`、`redis`、`qdrant`、`minio`
- 检查：

```bash
curl http://127.0.0.1:8000/health
docker compose logs -f api
```

### worker

RQ 后台分析任务执行器。

```bash
docker compose up --build worker
```

- 启动命令：`prospect-alpha-worker`
- 队列：`analysis`
- 依赖：`postgres`、`redis`、`qdrant`、`minio`
- 检查：

```bash
docker compose logs -f worker
```

### postgres

运行时数据库。

```bash
docker compose up -d postgres
```

- 镜像：`postgres:16`
- 端口：`5432`
- 数据库：`prospect_alpha`
- 用户/密码：`prospect_alpha` / `prospect_alpha`
- 检查：

```bash
docker compose exec postgres pg_isready -U prospect_alpha -d prospect_alpha
docker compose exec postgres psql -U prospect_alpha -d prospect_alpha
```

### redis

RQ 队列与任务调度依赖。

```bash
docker compose up -d redis
```

- 镜像：`redis:7`
- 端口：`6379`
- 检查：

```bash
docker compose exec redis redis-cli ping
```

### qdrant

向量检索服务。

```bash
docker compose up -d qdrant
```

- 镜像：`qdrant/qdrant:v1.12.1`
- HTTP 端口：`6333`
- gRPC 端口：`6334`
- 检查：

```bash
curl http://127.0.0.1:6333/collections
```

### minio

PDF 文件对象存储。

```bash
docker compose up -d minio
```

- 镜像：`minio/minio:RELEASE.2024-10-13T13-34-11Z`
- API 端口：`9000`
- Console 端口：`9001`
- 用户/密码：`change-me` / `change-me`
- 检查：

```bash
open http://127.0.0.1:9001
```

## 停止 Docker 全栈

```bash
cd backend
docker compose down -v
```

## 常用检查

```bash
curl http://127.0.0.1:8000/health
cd backend && .venv/bin/python -m pytest -q
cd frontend && pnpm build
```
