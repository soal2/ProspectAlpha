"""Agent expert directory persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from riskagent.database import SessionLocal, init_reference_data
from riskagent.models import AgentExpertRow, AgentSpecRow


@dataclass(frozen=True)
class AgentSpec:
    name: str
    title: str
    rule_types: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    default: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.name):
            raise ValueError(
                "agent name must contain only letters, digits, dot, underscore, or hyphen"
            )

    # 这个函数返回一个字典，描述了该 Agent 的运行时能力，包括名称、标题、角色、描述、能力列表、规则类型、类别、默认状态、输入输出模式等信息。
    def capability_payload(self, *, public_base_url: str | None = None) -> dict[str, Any]:
        """Return the runtime capability description shared by strategy and A2A."""

        from riskagent.skills.risk_skill_registry import skill_for_agent

        skill = skill_for_agent(self.name)
        capabilities = list(dict.fromkeys((*self.rule_types, *self.categories)))
        description = self.title
        if skill:
            description = (
                f"{skill.role_zh}；{skill.search_strategy}"
                f"；输出 {skill.output_schema}；{skill.evidence_requirement}"
            )
            capabilities = list(
                dict.fromkeys(
                    [skill.skill_id, *skill.rule_types, *skill.categories, *capabilities]
                )
            )
        payload: dict[str, Any] = {
            "agent_name": self.name,
            "title": self.title,
            "role": skill.role_zh if skill else self.title,
            "description": description,
            "capabilities": capabilities,
            "rule_types": list(self.rule_types),
            "categories": list(self.categories),
            "default": self.default,
            "input_modes": ["application/json"],
            "output_modes": ["application/json"],
            "output_schema": "ExpertTaskOutput",
        }
        if skill:
            payload.update(
                {
                    "skill_id": skill.skill_id,
                    "domain_result_schema": skill.output_schema,
                }
            )
        if public_base_url:
            payload["agent_card_url"] = (
                f"{public_base_url.rstrip('/')}/a2a/{self.name}/.well-known/agent-card.json"
            )
        return payload


class AgentExpertRepository:
    def __init__(self, *, session_factory: sessionmaker = SessionLocal, ensure_schema: bool = True) -> None:
        self.session_factory = session_factory
        self.ensure_schema = ensure_schema

    def list_agents(self) -> list[dict[str, object]]:
        if self.ensure_schema:
            init_reference_data(self.session_factory)
        with self.session_factory() as session:
            rows = session.scalars(
                select(AgentExpertRow).order_by(AgentExpertRow.display_order, AgentExpertRow.agent_id)
            ).all()
        return [_to_payload(row) for row in rows]


class AgentSpecRepository:
    def __init__(self, *, session_factory: sessionmaker = SessionLocal, ensure_schema: bool = True) -> None:
        self.session_factory = session_factory
        self.ensure_schema = ensure_schema

    def list_specs(self) -> list[AgentSpec]:
        if self.ensure_schema:
            init_reference_data(self.session_factory)
        with self.session_factory() as session:
            rows = session.scalars(
                select(AgentSpecRow).order_by(AgentSpecRow.display_order, AgentSpecRow.agent_name)
            ).all()
        return [
            AgentSpec(
                name=row.agent_name,
                title=row.title,
                rule_types=tuple(row.rule_types or []),
                categories=tuple(row.categories or []),
                default=row.is_default,
            )
            for row in rows
        ]

    def list_options(self) -> list[dict[str, Any]]:
        return [spec.capability_payload() for spec in self.list_specs()]


def _to_payload(row: AgentExpertRow) -> dict[str, object]:
    return {
        "id": row.agent_id,
        "name": row.name,
        "role": row.role,
        "layer": row.layer,
        "desc": row.description,
        "expertise": row.expertise,
        "callCount": row.call_count,
        "avgTokens": row.avg_tokens,
        "avatar": row.avatar,
        "accent": row.accent,
    }
