# 数据库迁移

升级只补齐缺表和缺列，不删除、不重建已有数据库。ORM 与已经存在的 PostgreSQL RAG 表走同一条 Alembic 入口。不要在启动代码里再写一套 `create_all` 或手工 `ALTER`。

## 部署顺序

1. 配置 `DATABASE_URL`。SQLite 与 PostgreSQL 都支持这条升级路径。PostgreSQL 上的 RAG 只在 `prospectus_chunks` 已经存在时补 `content_sha256`，向量索引定义仍在 `retrieval/index.py`，这里不另写一份。
2. 执行迁移：`python -m riskagent.migrations upgrade`。Render 的 `scripts/start_render.sh` 会先调用 `init_reference_data()`，其中已经包含这次升级，然后再启动 worker 和 API。迁移失败时该脚本会退出，进程不会报就绪。
3. 启动 worker。`start_render.sh` 已经用 `prospect-alpha-worker &` 启动它。该进程消费 `ANALYSIS_QUEUE_NAME`（默认 `analysis`）和 `DOCUMENT_QUEUE_NAME`（默认 `documents`），并给每个队列写 Redis 键 `prospectalpha:worker:heartbeat:<queue>`。值是 `{"queue","beat_at"}`，TTL 30 秒，工作循环阻塞在长任务里时后台线程仍每 10 秒刷新。
4. 启动 API。直接用 uvicorn 启动时，迁移失败不会让进程退出：`GET /health` 仍只做 `SELECT 1`，不会因为没有心跳而变成 503。`GET /ready` 在迁移失败、修订不到 head，或已配置的 RQ 队列心跳过期时返回 503。
5. 存活探针继续用 `GET /health`。就绪探针要用 `GET /ready`。这次没有改 `render.yaml` 或 compose：它们的健康检查仍然打 `/health`，所以当前线上部署探针不会发现 worker 心跳缺失。要让部署把未就绪挡在流量之外，需要另行把就绪探针指到 `GET /ready`，并保留 `/health` 作为存活检查。不要把 `/health` 改成就绪检查，否则数据库短暂不可用和 worker 心跳会搅在同一次重启判断里。

`inline`、`thread`、`subprocess` 不要求 Redis，也不要求 worker 心跳。只有 `ANALYSIS_EXECUTION_MODE=rq` 或 `DOCUMENT_PARSING_EXECUTION_MODE=rq` 才检查 Redis 和心跳。

## 已有库打基线

已经由旧版 `create_all` 建好、而且表和列与当前模型一致时，可以显式执行：

```bash
python -m riskagent.migrations stamp-baseline
```

该命令先确认没有缺表缺列，再写入修订号，然后升到 head。它不删除行。缺列时会拒绝，并要求改跑 `upgrade`。不要对空库或不完整的库打标记，否则后续升级会以为结构已经到位。

`0001_baseline` 不允许降级到空版本，因为那会删掉历史表。`0002_extension_pattern` 不改业务表，可以降一级回到基线，用来确认后续修订的回滚不会碰到基线删除。

## 下一次结构变更

分数快照或其他流如果要加版本化表结构，在 `backend/src/riskagent/migrations/versions/` 新增修订，例如：

```python
revision = "0003_score_snapshot_version"
down_revision = "0002_extension_pattern"

def upgrade() -> None:
    op.add_column("risk_score_snapshots", sa.Column("snapshot_version", sa.String(64), nullable=True))

def downgrade() -> None:
    op.drop_column("risk_score_snapshots", "snapshot_version")
```

然后只通过 `upgrade` 应用。不要在 `database.py` 再加启动期 `ALTER`。

## 与鉴权流的衔接

`GET /ready` 的响应只有：

```json
{"ready": true, "checks": {"schema": "ok", "rag": "not_required", "redis": "not_required", "worker": "not_required"}}
```

状态只有 `ok`、`failed`、`not_required`。没有异常文本、连接串或心跳原文，可以作为和 `/health` 一样的公开探针。鉴权流若启用令牌，需要同时放行 `GET /ready`，否则探针无法匿名访问。本改动不写鉴权规则。

分数快照的下一份修订使用 `down_revision = "0002_extension_pattern"`。本基线不改 `document_blocks.page_number` 的已有值，也不给历史坐标补写定位。

## 历史块坐标

`document_blocks.page_number` 仍是非空整数。升级不会改写已有页码、bbox 或字符区间。旧的整页假框继续原样留在库里；是否可信由读取方标记，迁移本身不把它们改成某种新的定位。
