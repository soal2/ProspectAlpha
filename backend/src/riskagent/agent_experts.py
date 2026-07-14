"""Agent expert directory persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
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
    persona_id: str = ""
    display_name: str = ""
    layer: str = ""
    function: str = ""
    avatar: str = ""
    accent: str = ""
    expertise: tuple[str, ...] = ()
    role_zh: str = ""

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
        role_zh = self.role_zh or (skill.role_zh if skill else self.title)
        if skill:
            description = (
                f"{role_zh}；{skill.search_strategy}"
                f"；输出 {skill.output_schema}；{skill.evidence_requirement}"
            )
            capabilities = list(
                dict.fromkeys(
                    [skill.skill_id, *skill.rule_types, *skill.categories, *capabilities]
                )
            )
        payload: dict[str, Any] = {
            "agent_name": self.name,
            "persona_id": self.persona_id,
            "display_name": self.display_name,
            "layer": self.layer,
            "function": self.function,
            "title": self.title,
            "role": role_zh,
            "role_zh": role_zh,
            "description": description,
            "avatar": self.avatar,
            "accent": self.accent,
            "expertise": list(self.expertise),
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
            try:
                rows = session.execute(
                    select(AgentSpecRow, AgentExpertRow)
                    .join(
                        AgentExpertRow,
                        AgentSpecRow.persona_id == AgentExpertRow.agent_id,
                        isouter=True,
                    )
                    .order_by(AgentSpecRow.display_order, AgentSpecRow.agent_name)
                ).all()
                return [
                    _spec_from_joined_rows(spec_row, expert_row)
                    for spec_row, expert_row in rows
                ]
            except OperationalError as exc:
                if "agent_specs.persona_id" not in str(exc):
                    raise
                legacy_rows = session.execute(
                    select(
                        AgentSpecRow.__table__.c.agent_name,
                        AgentSpecRow.__table__.c.title,
                        AgentSpecRow.__table__.c.rule_types,
                        AgentSpecRow.__table__.c.categories,
                        AgentSpecRow.__table__.c.is_default,
                        AgentSpecRow.__table__.c.display_order,
                    ).order_by(
                        AgentSpecRow.__table__.c.display_order,
                        AgentSpecRow.__table__.c.agent_name,
                    )
                ).all()
                return [
                    AgentSpec(
                        name=str(row.agent_name),
                        title=str(row.title),
                        rule_types=tuple(row.rule_types or []),
                        categories=tuple(row.categories or []),
                        default=bool(row.is_default),
                    )
                    for row in legacy_rows
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


def _spec_from_joined_rows(spec_row: AgentSpecRow, expert_row: AgentExpertRow | None) -> AgentSpec:
    expertise = tuple(expert_row.expertise or []) if expert_row else ()
    return AgentSpec(
        name=spec_row.agent_name,
        title=spec_row.title,
        rule_types=tuple(spec_row.rule_types or []),
        categories=tuple(spec_row.categories or []),
        default=spec_row.is_default,
        persona_id=spec_row.persona_id or "",
        display_name=spec_row.display_name or "",
        layer=spec_row.layer or "",
        function=spec_row.function or "",
        avatar=expert_row.avatar if expert_row else "",
        accent=expert_row.accent if expert_row else "",
        expertise=expertise,
        role_zh=expert_row.role if expert_row else "",
    )
