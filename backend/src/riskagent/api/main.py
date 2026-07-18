"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from riskagent.a2a import build_dynamic_expert_a2a_routes
from riskagent.agent_experts import AgentSpec, AgentSpecRepository
from riskagent.api import agents, analysis, analysis_options, chat, documents, knowledge, reports_v2, risk_rules, skills
from riskagent.env import load_dotenv
from riskagent.seed_data import AGENT_EXPERTS, AGENT_SPECS

logger = logging.getLogger(__name__)

LOCAL_DEVELOPMENT_ORIGIN_REGEX = (
    r"^https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?$"
)
REFERENCE_CACHE_PATHS = frozenset({"/agents", "/analysis-options", "/risk-rules"})

def _cors_origins() -> list[str]:
    configured = os.getenv("CORS_ALLOW_ORIGINS")
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]


def _is_vercel_runtime() -> bool:
    """Whether this process runs inside a Vercel serverless function."""

    return bool(os.getenv("VERCEL"))


def create_app() -> FastAPI:
    app = FastAPI(title="ProspectAlpha RiskAgent API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        # Vite can move to another port when 5173 is occupied, and developers
        # commonly alternate between localhost and 127.0.0.1. Keep loopback
        # origins usable without weakening the configured production allowlist.
        allow_origin_regex=LOCAL_DEVELOPMENT_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        max_age=86400,
    )

    @app.middleware("http")
    async def cache_reference_data(request, call_next):
        response = await call_next(request)
        if request.method == "GET" and request.url.path in REFERENCE_CACHE_PATHS:
            response.headers.setdefault(
                "Cache-Control", "public, s-maxage=300, stale-while-revalidate=600"
            )
        return response

    @app.exception_handler(SQLAlchemyError)
    async def _database_unavailable(_request, _exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("Database request failed")
        return JSONResponse(
            status_code=503,
            content={
                "detail": "数据库暂不可用，已上传 PDF 不会丢失；请恢复 PostgreSQL 后重试。",
                "error_code": "database_unavailable",
            },
        )

    @app.get("/health")
    def health():
        from riskagent.database import check_database_connection

        try:
            check_database_connection()
        except SQLAlchemyError:
            return JSONResponse(
                status_code=503,
                content={"status": "degraded", "database": "unavailable"},
            )
        return {"status": "ok", "database": "ready"}

    @app.on_event("startup")
    def _probe_embedding_provider() -> None:
        load_dotenv()
        from riskagent.database import init_db

        # Vercel Functions are stateless request handlers.  The document
        # dispatcher, MinerU model probe, and embedding probe depend on local
        # files, worker processes, or external model services, none of which
        # is part of the PostgreSQL-only deployment profile.
        if _is_vercel_runtime():
            # Schema setup belongs to a deliberate deployment migration, not a
            # request-path cold start. The live Supabase schema is initialized
            # before this function is deployed.
            logger.info("Skipped schema initialization for Vercel runtime")
            return

        from riskagent.documents import recover_interrupted_prospectus_jobs
        from riskagent.parsing import mineru_runtime_health
        from riskagent.workers.documents import start_document_dispatcher

        try:
            init_db()
            recovered = recover_interrupted_prospectus_jobs()
            if recovered:
                logger.warning(
                    "Marked %d abandoned MinerU jobs as interrupted: %s",
                    len(recovered),
                    ", ".join(recovered),
                )
            start_document_dispatcher()
        except Exception:
            logger.exception("Document parsing recovery probe failed")

        parser_health = mineru_runtime_health()
        log_method = logger.info if parser_health["status"] == "ready" else logger.warning
        log_method(
            "MinerU runtime status=%s source=%s device=%s cache_present=%s: %s",
            parser_health["status"],
            parser_health["model_source"],
            parser_health["device"],
            parser_health["model_cache_present"],
            parser_health["message"],
        )
        embedding_key = (
            os.getenv("EMBEDDING_API_KEY", "").strip()
            or os.getenv("DASHSCOPE_API_KEY", "").strip()
        )
        if not embedding_key:
            raise RuntimeError(
                "EMBEDDING_API_KEY/DASHSCOPE_API_KEY not configured; "
                "hashing fallback is disabled."
            )
        from riskagent.providers.embedding import get_default_embedding_client

        try:
            client = get_default_embedding_client()
            vector = client.embed("ping")
            logger.info(
                "Embedding provider ready: %s (%d dims)",
                getattr(client, "provider_id", "unknown"),
                len(vector),
            )
        except Exception as exc:  # noqa: BLE001 - normalized startup failure
            raise RuntimeError(f"Embedding provider probe failed: {exc}") from exc

    app.include_router(risk_rules.router)
    app.include_router(agents.router)
    app.include_router(analysis_options.router)
    app.include_router(chat.general_router)
    app.include_router(documents.router)
    app.include_router(analysis.router)
    app.include_router(knowledge.router)
    app.include_router(skills.router)
    app.include_router(reports_v2.router)
    app.router.routes.extend(
        build_dynamic_expert_a2a_routes(
            _a2a_expert_directory,
            public_base_url=os.getenv("A2A_PUBLIC_BASE_URL", "http://localhost:8000"),
            send_timeout=float(os.getenv("A2A_SEND_TIMEOUT_SECONDS", "300")),
            runtime_root=os.getenv("AGENT_RUNTIME_DATA_DIR") or None,
        )
    )
    for route in app.routes:
        if not hasattr(route, "path") and hasattr(route, "effective_candidates"):
            candidates = list(route.effective_candidates())
            if candidates:
                route.path = candidates[0].path
    return app


def _a2a_expert_directory() -> list[dict[str, object]]:
    expertise_by_persona = {
        str(item["agent_id"]): item for item in AGENT_EXPERTS
    }
    if os.getenv("USE_DATABASE", "1") == "0":
        specs = [
            AgentSpec(
                name=str(item["agent_name"]),
                title=str(item["title"]),
                rule_types=tuple(item.get("rule_types") or []),
                categories=tuple(item.get("categories") or []),
                default=bool(item.get("is_default")),
                persona_id=str(item.get("persona_id") or ""),
                display_name=str(item.get("display_name") or ""),
                layer=str(item.get("layer") or ""),
                function=str(item.get("function") or ""),
                avatar=str(expertise_by_persona.get(str(item.get("persona_id") or ""), {}).get("avatar") or ""),
                accent=str(expertise_by_persona.get(str(item.get("persona_id") or ""), {}).get("accent") or ""),
                expertise=tuple(
                    expertise_by_persona.get(str(item.get("persona_id") or ""), {}).get("expertise") or []
                ),
                role_zh=str(expertise_by_persona.get(str(item.get("persona_id") or ""), {}).get("role") or ""),
            )
            for item in AGENT_SPECS
        ]
    else:
        specs = AgentSpecRepository().list_specs()
    if any(spec.name in {
        "shen_beichen_orchestrator_agent",
        "xiao_zeming_quality_agent",
        "zhou_shenyan_redteam_agent",
    } for spec in specs):
        specs = [
            *specs,
            AgentSpec(name="financial_agent", title="Financial specialist", rule_types=("quantitative_financial",)),
            AgentSpec(
                name="legal_agent",
                title="Legal and compliance specialist",
                categories=("关联交易", "对赌/赎回条款", "政策监管", "诉讼与纠纷", "担保与质押", "税务风险"),
            ),
            AgentSpec(name="market_agent", title="Market and industry specialist", categories=("行业竞争",)),
            AgentSpec(name="business_agent", title="Business and disclosure specialist", default=True),
        ]
    return [spec.capability_payload() for spec in specs]


app = create_app()
