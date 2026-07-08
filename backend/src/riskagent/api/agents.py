"""Agent expert directory routes."""

from __future__ import annotations

from fastapi import APIRouter

from riskagent.agent_experts import AgentExpertRepository


router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
def list_agents() -> list[dict[str, object]]:
    return AgentExpertRepository().list_agents()
