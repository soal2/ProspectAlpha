"""FastAPI application entrypoint."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from riskagent.api import agents, analysis, analysis_options, chat, documents, risk_rules


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
    for route in app.routes:
        if not hasattr(route, "path") and hasattr(route, "effective_candidates"):
            candidates = list(route.effective_candidates())
            if candidates:
                route.path = candidates[0].path
    return app


app = create_app()
