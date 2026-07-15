"""Skill document routes: Anthropic-format SKILL.md modules under backend/skills/."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from riskagent.skill_docs import load_skill_docs

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillSummary(BaseModel):
    name: str
    description: str
    path: str


class SkillDetail(SkillSummary):
    body: str


@router.get("", response_model=list[SkillSummary])
def list_skills() -> list[SkillSummary]:
    return [
        SkillSummary(name=doc.name, description=doc.description, path=doc.path)
        for doc in load_skill_docs()
    ]


@router.get("/{name}", response_model=SkillDetail)
def get_skill(name: str) -> SkillDetail:
    for doc in load_skill_docs():
        if doc.name == name:
            return SkillDetail(
                name=doc.name, description=doc.description, path=doc.path, body=doc.body
            )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"Skill not found: {name}"
    )
