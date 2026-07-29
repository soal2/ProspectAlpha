# Vercel backend deployment

Set this directory (`backend/`) as the Vercel project's Root Directory. Vercel
detects the FastAPI application from the thin `main.py` entrypoint configured in
`pyproject.toml`.

Configure these production environment variables before deploying:

```text
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE?sslmode=require
CORS_ALLOW_ORIGINS=https://<your-frontend-domain>
```

Initialize or migrate the PostgreSQL schema deliberately before deploying (for
example, from a controlled maintenance command); the Vercel request startup path
does not run schema DDL. This prevents cold requests from paying for table and
column inspection. The Vercel profile does not start Redis/RQ workers, MinerU
document processing, local object storage, or the embedding readiness probe.
Consequently, document upload and asynchronous analysis pipelines require their
corresponding services before they can be enabled in production.
