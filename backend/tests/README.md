# Backend Tests

Run the full suite from `backend/`:

```bash
.venv/bin/python -m pytest -q
```

The suite includes A2A protocol/Card/JSON-RPC contracts, cross-process mailbox
claim and lease behavior, TaskStore persistence, expert thread lifecycle and
peer correlation, dynamic LangGraph selection/replacement/manual recovery,
analysis API contracts, evidence gates, rules, retrieval, and worker dispatch.
