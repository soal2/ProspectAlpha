"""Agent expert directory persistence."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from riskagent.database import SessionLocal, init_db
from riskagent.models import AgentExpertRow


class AgentExpertRepository:
    def __init__(self, *, session_factory: sessionmaker = SessionLocal, ensure_schema: bool = True) -> None:
        self.session_factory = session_factory
        self.ensure_schema = ensure_schema

    def list_agents(self) -> list[dict[str, object]]:
        if self.ensure_schema:
            init_db()
        with self.session_factory() as session:
            rows = session.scalars(
                select(AgentExpertRow).order_by(AgentExpertRow.display_order, AgentExpertRow.agent_id)
            ).all()
        return [_to_payload(row) for row in rows]


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
