"""Skill document routes: Anthropic-format SKILL.md modules under backend/skills/."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from riskagent.skill_docs import get_skill_catalog

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillSummary(BaseModel):
    name: str
    version: str
    description: str
    path: str
    owners: list[str]
    execution: str
    input_schema: str | None
    output_schema: str | None
    failure_policy: str
    availability: bool
    document_hash: str


class SkillDetail(SkillSummary):
    body: str


@router.get("", response_model=list[SkillSummary])
def list_skills() -> list[SkillSummary]:
    return [
        _summary(definition)
        for definition in get_skill_catalog().definitions.values()
    ]


@router.get("/{name}", response_model=SkillDetail)
def get_skill(name: str) -> SkillDetail:
    definition = get_skill_catalog().definitions.get(name)
    if definition is not None:
        return SkillDetail(**_summary(definition).model_dump(), body=definition.document.body)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"Skill not found: {name}"
    )


def _summary(definition) -> SkillSummary:
    doc = definition.document
    return SkillSummary(
        name=doc.name,
        version=doc.version,
        description=doc.description,
        path=doc.path,
        owners=list(doc.owners),
        execution=doc.execution,
        input_schema=doc.input_schema,
        output_schema=doc.output_schema,
        failure_policy=doc.failure_policy,
        availability=definition.available,
        document_hash=doc.document_hash,
    )
