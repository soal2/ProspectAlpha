"""Agent expert directory persistence."""

from __future__ import annotations

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
        return [
            {
                "agent_name": spec.name,
                "title": spec.title,
                "default": spec.default,
            }
            for spec in self.list_specs()
        ]


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
