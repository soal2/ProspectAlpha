"""Minimal intake analysis option routes."""

from __future__ import annotations

from fastapi import APIRouter

from riskagent.agents.supervisor import AgentTeamSupervisor
from riskagent.risk_rules import get_default_repository


router = APIRouter(prefix="/analysis-options", tags=["analysis-options"])


@router.get("")
def get_analysis_options() -> dict[str, object]:
    rule_repository = get_default_repository()
    rule_set = rule_repository.load()
    supervisor = AgentTeamSupervisor(repository=rule_repository)
    return {
        "risk_rule_sets": [
            {
                "rule_set_id": rule_set.rule_set_id,
                "version": rule_set.version,
                "name": rule_set.name,
                "rule_count": rule_set.totals.get("rule_count", len(rule_set.rules)),
                "active": True,
            }
        ],
        "agents": supervisor.agent_options(),
        "analysis_depths": [
            {"depth": "quick", "label": "快速", "max_findings": 8},
            {"depth": "standard", "label": "标准", "max_findings": 20},
            {"depth": "deep", "label": "深度", "max_findings": 40},
        ],
    }
