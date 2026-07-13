"""FastAPI application entrypoint."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from riskagent.a2a import build_dynamic_expert_a2a_routes
from riskagent.agent_experts import AgentSpec, AgentSpecRepository
from riskagent.api import agents, analysis, analysis_options, chat, documents, risk_rules
from riskagent.seed_data import AGENT_SPECS


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

    app.include_router(risk_rules.router)
    app.include_router(agents.router)
    app.include_router(analysis_options.router)
    app.include_router(documents.router)
    app.include_router(chat.router)
    app.include_router(chat.general_router)
    app.include_router(analysis.router)
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
    if os.getenv("USE_DATABASE", "1") == "0":
        specs = [
            AgentSpec(
                name=str(item["agent_name"]),
                title=str(item["title"]),
                rule_types=tuple(item.get("rule_types") or []),
                categories=tuple(item.get("categories") or []),
                default=bool(item.get("is_default")),
            )
            for item in AGENT_SPECS
        ]
    else:
        specs = AgentSpecRepository().list_specs()
    return [spec.capability_payload() for spec in specs]


app = create_app()
