# 数据库迁移

升级只补齐缺表和缺列，不删除、不重建已有数据库。ORM 与已经存在的 PostgreSQL RAG 表走同一条 Alembic 入口。不要在启动代码里再写一套 `create_all` 或手工 `ALTER`。

## 部署顺序

1. 配置 `DATABASE_URL`。SQLite 与 PostgreSQL 都支持这条升级路径。PostgreSQL 上的 RAG 只在 `prospectus_chunks` 已经存在时补 `content_sha256`，向量索引定义仍在 `retrieval/index.py`，这里不另写一份。
2. 执行迁移：`python -m riskagent.migrations upgrade`。Render 的 `scripts/start_render.sh` 会先调用 `init_reference_data()`，其中已经包含这次升级，然后再启动 worker 和 API。迁移失败时该脚本会退出，进程不会报就绪。
3. 启动 worker。`start_render.sh` 已经用 `prospect-alpha-worker &` 启动它，不需要再声称部署里没有 worker。RQ 模式下 worker 会立刻写入 Redis 键 `prospectalpha:worker:heartbeat`，每 10 秒刷新，30 秒过期。
4. 启动 API。直接用 uvicorn 启动时，迁移失败不会让进程退出：`GET /health` 仍可做存活检查，`GET /ready` 返回 503。
5. 存活探针继续用 `GET /health`（`SELECT 1`）。就绪探针用 `GET /ready`。当前 `render.yaml` 和 compose 的健康检查仍指向 `/health`，这次没有改线上部署配置。

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

`/health` 保持公开的最小存活检查。另一条流如果只放行 `/health`，`/ready` 在令牌启用后会要求 `Authorization`。不能带令牌的就绪探针需要那条流同时放行 `/ready`。本改动不修改鉴权规则。
