# Backend Tests

Use Python 3.12 and uv from `backend/` to run the CI unit suite:

```bash
export PROSPECTALPHA_SKIP_DOTENV=1
uv lock --check
uv sync --locked --extra dev --python 3.12
.venv/bin/python ../.github/scripts/check-pytest-skips.py -q \
  -k 'not test_dashscope_embedding_client_real_call' \
  --deselect tests/test_database_migrations.py::test_postgres_upgrade_preserves_rows
```

The paid DashScope test is explicitly excluded. The only allowed skips are for
missing `IPO_data.xlsx` in these three `test_skill_runtime.py` tests:

- `test_subscription_history_uses_versioned_local_dataset`
- `test_sentiment_matches_local_history_without_structured_fallback`
- `test_valuation_uses_versioned_historical_peers_by_industry`

Any other pytest skip fails the job. The unit suite includes SQLite migrations,
readiness, and worker heartbeat tests. Those tests use fake Redis or run without
Redis, so CI does not provision a live Redis service.

The separate PostgreSQL job runs the row-preservation test against
`pgvector/pgvector:0.8.2-pg16`. For a local equivalent, provision a disposable
PostgreSQL instance, create a UTF-8 database named exactly
`prospectalpha_ephemeral`, and point `PROSPECTALPHA_TEST_POSTGRES_URL` at it using
the `postgresql+psycopg` dialect. The test drops that database's public schema.
Use only an isolated throwaway instance. This job requires the test to run and
pass.

After the locked dev install above, install only the hashed Psycopg requirements
from the lockfile's pipeline export:

```bash
set -o pipefail
uv export --locked --extra pipeline --no-emit-project --no-annotate |
  awk '/^[[:alnum:]]/ { driver = /^psycopg(-binary)?==/ } driver && NF' > postgres-driver.txt
uv pip install --python .venv/bin/python --no-deps --require-hashes -r postgres-driver.txt
.venv/bin/python ../.github/scripts/check-pytest-skips.py -q \
  tests/test_database_migrations.py::test_postgres_upgrade_preserves_rows
```

The suite includes A2A protocol/Card/JSON-RPC contracts, cross-process mailbox
claim and lease behavior, TaskStore persistence, expert thread lifecycle and
peer correlation, dynamic LangGraph selection/replacement/manual recovery,
analysis API contracts, evidence gates, rules, retrieval, and worker dispatch.
