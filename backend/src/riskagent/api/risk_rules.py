"""Risk-rule CRUD routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from riskagent.risk_rules import (
    RiskRule,
    RiskRuleCreate,
    RiskRuleRepository,
    RiskRuleSet,
    RiskRuleUpdate,
    get_default_repository,
)


router = APIRouter(prefix="/risk-rules", tags=["risk-rules"])


def _repo() -> RiskRuleRepository:
    return get_default_repository()


@router.get("", response_model=RiskRuleSet)
def get_active_rule_set() -> RiskRuleSet:
    return _repo().load()


@router.get("/sets")
def list_rule_sets() -> list[dict]:
    return _repo().list_rule_sets()


@router.get("/{rule_id}", response_model=RiskRule)
def get_rule(rule_id: str) -> RiskRule:
    try:
        return _repo().get_rule(rule_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found") from exc


@router.post("", response_model=RiskRule, status_code=status.HTTP_201_CREATED)
def create_rule(payload: RiskRuleCreate) -> RiskRule:
    return _repo().create_rule(payload)


@router.patch("/{rule_id}", response_model=RiskRule)
def update_rule(rule_id: str, payload: RiskRuleUpdate) -> RiskRule:
    try:
        return _repo().update_rule(rule_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found") from exc


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: str) -> None:
    try:
        _repo().delete_rule(rule_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found") from exc
