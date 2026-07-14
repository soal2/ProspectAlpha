"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

from riskagent.a2a import build_dynamic_expert_a2a_routes
from riskagent.agent_experts import AgentSpec, AgentSpecRepository
from riskagent.api import agents, analysis, analysis_options, chat, documents, knowledge, risk_rules
from riskagent.seed_data import AGENT_EXPERTS, AGENT_SPECS


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


def create_app() -> FastAPI:
    app = FastAPI(title="ProspectAlpha RiskAgent API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.on_event("startup")
    def _probe_embedding_provider() -> None:
        if not os.getenv("EMBEDDING_API_KEY", "").strip():
            logger.warning(
                "EMBEDDING_API_KEY not configured — retrieval falls back to hashing "
                "vectors; analysis runs requiring real embeddings will fail fast."
            )
            return
        from riskagent.providers.embedding import get_default_embedding_client

        try:
            client = get_default_embedding_client()
            vector = client.embed("ping")
            logger.info(
                "Embedding provider ready: %s (%d dims)",
                getattr(client, "provider_id", "unknown"),
                len(vector),
            )
        except Exception as exc:  # noqa: BLE001 - startup probe must not kill the app
            logger.error("Embedding provider probe failed: %s", exc)

    app.include_router(risk_rules.router)
    app.include_router(agents.router)
    app.include_router(analysis_options.router)
    app.include_router(documents.router)
    app.include_router(chat.router)
    app.include_router(chat.general_router)
    app.include_router(analysis.router)
    app.include_router(knowledge.router)
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
