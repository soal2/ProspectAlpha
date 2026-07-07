"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from riskagent.api import analysis, risk_rules


def create_app() -> FastAPI:
    app = FastAPI(title="ProspectAlpha RiskAgent API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(risk_rules.router)
    app.include_router(analysis.router)
    return app


app = create_app()
