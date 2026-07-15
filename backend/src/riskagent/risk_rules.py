"""Risk-rule repository and agent-facing selection helpers."""

from __future__ import annotations

import copy
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from riskagent.database import Base, SessionLocal, init_db, init_reference_data, make_engine
from riskagent.models import RiskRuleDimensionRow, RiskRuleRow, RiskRuleSetRow


BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = BACKEND_ROOT / "configs"
DEFAULT_RULES_PATH = CONFIG_ROOT / "risk_rules.yaml"

# Serializes rule CRUD across threads: create does a read-then-write to pick
# the next rule id, and _write_data replaces the whole rule set, so
# interleaved writers could otherwise duplicate ids or drop rows.
_WRITE_LOCK = threading.Lock()


class RiskRule(BaseModel):
    rule_id: str
    dimension_code: str
    dimension: str
    rule_type: str
    category: str
    risk_item: str
    description: str
    trigger_condition: str
    calculation_or_method: str
    source_sections: str
    enabled: bool = True


class RiskRuleSet(BaseModel):
    rule_set_id: str = "hk_ipo_prospectus_risk_framework_v1"
    version: str = "v1.0"
    name: str = "港股IPO招股书风险识别框架"
    source_workbook: str | None = None
    source_main_sheet: str | None = None
    source_summary_sheet: str | None = None
    scope: str | None = None
    usage_notes: list[str] = Field(default_factory=list)
    totals: dict[str, int] = Field(default_factory=dict)
    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    rules: list[RiskRule] = Field(default_factory=list)


class RiskRuleCreate(BaseModel):
    dimension_code: str
    dimension: str
    rule_type: str
    category: str
    risk_item: str
    description: str
    trigger_condition: str
    calculation_or_method: str
    source_sections: str
    enabled: bool = True


class RiskRuleUpdate(BaseModel):
    dimension_code: str | None = None
    dimension: str | None = None
    rule_type: str | None = None
    category: str | None = None
    risk_item: str | None = None
    description: str | None = None
    trigger_condition: str | None = None
    calculation_or_method: str | None = None
    source_sections: str | None = None
    enabled: bool | None = None


class RiskRuleRepository:
    def __init__(
        self,
        path: Path | str | None = None,
        *,
        session_factory: sessionmaker | None = None,
        ensure_schema: bool = True,
    ) -> None:
        configured = path or os.getenv("RISK_RULES_PATH") or DEFAULT_RULES_PATH
        self.seed_path = Path(configured)
        isolated_path = self.seed_path.resolve() != DEFAULT_RULES_PATH.resolve()
        if session_factory is None and isolated_path and (path is not None or os.getenv("RISK_RULES_PATH")):
            engine = make_engine(f"sqlite:///{self.seed_path.with_suffix('.db')}")
            self.session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
        else:
            self.session_factory = session_factory or SessionLocal
        self.ensure_schema_enabled = ensure_schema

    def ensure_store(self) -> None:
        if not self.ensure_schema_enabled:
            return
        init_reference_data(self.session_factory)

    def list_rule_sets(self) -> list[dict[str, Any]]:
        self.ensure_store()
        with self.session_factory() as session:
            rows = session.scalars(
                select(RiskRuleSetRow).order_by(RiskRuleSetRow.display_order, RiskRuleSetRow.rule_set_id)
            ).all()
        return [
            {
                "rule_set_id": row.rule_set_id,
                "version": row.version,
                "name": row.name,
                "totals": self._totals(row.rule_set_id),
                "active": row.active,
            }
            for row in rows
        ]

    def load(self) -> RiskRuleSet:
        self.ensure_store()
        with self.session_factory() as session:
            row = session.scalars(
                select(RiskRuleSetRow)
                .where(RiskRuleSetRow.active.is_(True))
                .order_by(RiskRuleSetRow.display_order, RiskRuleSetRow.rule_set_id)
            ).first()
            if row is None:
                return RiskRuleSet()
            rules = session.scalars(
                select(RiskRuleRow)
                .where(RiskRuleRow.rule_set_id == row.rule_set_id)
                .order_by(RiskRuleRow.display_order, RiskRuleRow.rule_id)
            ).all()
            dimensions = session.scalars(
                select(RiskRuleDimensionRow)
                .where(RiskRuleDimensionRow.rule_set_id == row.rule_set_id)
                .order_by(RiskRuleDimensionRow.display_order, RiskRuleDimensionRow.dimension_code)
            ).all()
        data = {
            "rule_set_id": row.rule_set_id,
            "version": row.version,
            "name": row.name,
            "source_workbook": row.source_workbook,
            "source_main_sheet": row.source_main_sheet,
            "source_summary_sheet": row.source_summary_sheet,
            "scope": row.scope,
            "usage_notes": row.usage_notes or [],
            "dimensions": [_dimension_to_dict(item) for item in dimensions],
            "rules": [_rule_to_dict(item) for item in rules],
        }
        return RiskRuleSet.model_validate(self._recalculate(self._normalize_data(data)))

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.load().model_dump())

    def get_rule(self, rule_id: str) -> RiskRule:
        for rule in self.load().rules:
            if rule.rule_id == rule_id:
                return rule
        raise KeyError(rule_id)

    def create_rule(self, payload: RiskRuleCreate) -> RiskRule:
        with _WRITE_LOCK:
            rule_set = self.load()
            rule = RiskRule(rule_id=self._next_rule_id([item.model_dump() for item in rule_set.rules]), **payload.model_dump())
            self._ensure_dimension(rule_set.rule_set_id, rule)
            with self.session_factory() as session:
                max_order = max(
                    [
                        row.display_order
                        for row in session.scalars(
                            select(RiskRuleRow).where(RiskRuleRow.rule_set_id == rule_set.rule_set_id)
                        ).all()
                    ],
                    default=0,
                )
                session.add(RiskRuleRow(**rule.model_dump(), rule_set_id=rule_set.rule_set_id, display_order=max_order + 1))
                session.commit()
            return rule

    def update_rule(self, rule_id: str, payload: RiskRuleUpdate) -> RiskRule:
        patch = payload.model_dump(exclude_unset=True)
        with _WRITE_LOCK:
            with self.session_factory() as session:
                row = session.get(RiskRuleRow, rule_id)
                if row is None:
                    raise KeyError(rule_id)
                rule_set_id = row.rule_set_id
                existing = _rule_to_dict(row)
                updated = RiskRule(**{**existing, **patch})
                for key, value in updated.model_dump().items():
                    setattr(row, key, value)
                session.commit()
            self._ensure_dimension(rule_set_id, updated)
            return updated

    def delete_rule(self, rule_id: str) -> None:
        with _WRITE_LOCK:
            with self.session_factory() as session:
                row = session.get(RiskRuleRow, rule_id)
                if row is None:
                    raise KeyError(rule_id)
                session.delete(row)
                session.commit()

    def select_rules(
        self,
        *,
        dimension_code: str | None = None,
        rule_type: str | None = None,
        category: str | None = None,
        enabled_only: bool = True,
    ) -> list[RiskRule]:
        rules = self.load().rules
        if enabled_only:
            rules = [rule for rule in rules if rule.enabled]
        if dimension_code:
            rules = [rule for rule in rules if rule.dimension_code == dimension_code]
        if rule_type:
            rules = [rule for rule in rules if rule.rule_type == rule_type]
        if category:
            rules = [rule for rule in rules if rule.category == category]
        return rules

    def _write_data(self, data: dict[str, Any]) -> None:
        data = self._recalculate(self._normalize_data(data))
        with _WRITE_LOCK:
            if self.session_factory is SessionLocal:
                init_db()
            else:
                bind = getattr(self.session_factory, "kw", {}).get("bind")
                if bind is not None:
                    Base.metadata.create_all(bind=bind)
            with self.session_factory() as session:
                rule_set_id = data["rule_set_id"]
                session.execute(delete(RiskRuleRow).where(RiskRuleRow.rule_set_id == rule_set_id))
                session.execute(delete(RiskRuleDimensionRow).where(RiskRuleDimensionRow.rule_set_id == rule_set_id))
                session.execute(delete(RiskRuleSetRow).where(RiskRuleSetRow.rule_set_id == rule_set_id))
                session.add(
                    RiskRuleSetRow(
                        rule_set_id=rule_set_id,
                        version=data["version"],
                        name=data["name"],
                        source_workbook=data.get("source_workbook"),
                        source_main_sheet=data.get("source_main_sheet"),
                        source_summary_sheet=data.get("source_summary_sheet"),
                        scope=data.get("scope"),
                        usage_notes=data.get("usage_notes", []),
                        active=True,
                        display_order=1,
                    )
                )
                session.flush()
                for display_order, dimension in enumerate(data["dimensions"], start=1):
                    session.add(
                        RiskRuleDimensionRow(
                            dimension_id=f"{rule_set_id}:{dimension['dimension_code']}",
                            rule_set_id=rule_set_id,
                            dimension_code=dimension["dimension_code"],
                            dimension=dimension["dimension"],
                            rule_type=dimension["rule_type"],
                            description=dimension.get("description"),
                            display_order=display_order,
                        )
                    )
                for display_order, rule in enumerate(data["rules"], start=1):
                    session.add(RiskRuleRow(**rule, rule_set_id=rule_set_id, display_order=display_order))
                session.commit()

    def _normalize_data(self, data: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(data)
        normalized.setdefault("rule_set_id", "hk_ipo_prospectus_risk_framework_v1")
        normalized.setdefault("version", "v1.0")
        normalized.setdefault("name", "港股IPO招股书风险识别框架")
        normalized.setdefault("usage_notes", [])
        normalized.setdefault("dimensions", [])
        normalized.setdefault("rules", [])
        normalized.setdefault("totals", {})
        normalized["rules"] = [
            {**rule, "enabled": rule.get("enabled", True)} for rule in normalized["rules"]
        ]
        return normalized

    def _recalculate(self, data: dict[str, Any]) -> dict[str, Any]:
        rules = data.get("rules", [])
        category_counts: OrderedDict[tuple[str, str], int] = OrderedDict()
        for rule in rules:
            key = (rule["dimension_code"], rule["category"])
            category_counts[key] = category_counts.get(key, 0) + 1

        dimensions_by_code = {
            dimension.get("dimension_code"): dict(dimension)
            for dimension in data.get("dimensions", [])
            if dimension.get("dimension_code")
        }
        for rule in rules:
            dimensions_by_code.setdefault(
                rule["dimension_code"],
                {
                    "dimension_code": rule["dimension_code"],
                    "dimension": rule["dimension"],
                    "rule_type": rule["rule_type"],
                },
            )

        dimensions = []
        for code, dimension in dimensions_by_code.items():
            categories = [
                {"name": category, "rule_count": count}
                for (dimension_code, category), count in category_counts.items()
                if dimension_code == code
            ]
            dimensions.append(
                {
                    **dimension,
                    "category_count": len(categories),
                    "rule_count": sum(item["rule_count"] for item in categories),
                    "categories": categories,
                }
            )
        dimensions.sort(key=lambda item: item["dimension_code"])
        data["dimensions"] = dimensions
        data["totals"] = {
            "dimension_count": len(dimensions),
            "category_count": len(category_counts),
            "rule_count": len(rules),
        }
        return data

    def _next_rule_id(self, rules: list[dict[str, Any]]) -> str:
        max_num = 0
        for rule in rules:
            rule_id = rule.get("rule_id", "")
            if rule_id.startswith("HKIPO-"):
                try:
                    max_num = max(max_num, int(rule_id.split("-", 1)[1]))
                except ValueError:
                    continue
        return f"HKIPO-{max_num + 1:03d}"

    def _totals(self, rule_set_id: str) -> dict[str, int]:
        with self.session_factory() as session:
            rules = [
                _rule_to_dict(row)
                for row in session.scalars(
                    select(RiskRuleRow).where(RiskRuleRow.rule_set_id == rule_set_id)
                ).all()
            ]
        return self._recalculate({"dimensions": [], "rules": rules})["totals"]

    def _ensure_dimension(self, rule_set_id: str, rule: RiskRule) -> None:
        dimension_id = f"{rule_set_id}:{rule.dimension_code}"
        with self.session_factory() as session:
            if session.get(RiskRuleDimensionRow, dimension_id):
                return
            max_order = max(
                [
                    row.display_order
                    for row in session.scalars(
                        select(RiskRuleDimensionRow).where(RiskRuleDimensionRow.rule_set_id == rule_set_id)
                    ).all()
                ],
                default=0,
            )
            session.add(
                RiskRuleDimensionRow(
                    dimension_id=dimension_id,
                    rule_set_id=rule_set_id,
                    dimension_code=rule.dimension_code,
                    dimension=rule.dimension,
                    rule_type=rule.rule_type,
                    description=None,
                    display_order=max_order + 1,
                )
            )
            session.commit()


def _rule_to_dict(row: RiskRuleRow) -> dict[str, Any]:
    return {
        "rule_id": row.rule_id,
        "dimension_code": row.dimension_code,
        "dimension": row.dimension,
        "rule_type": row.rule_type,
        "category": row.category,
        "risk_item": row.risk_item,
        "description": row.description,
        "trigger_condition": row.trigger_condition,
        "calculation_or_method": row.calculation_or_method,
        "source_sections": row.source_sections,
        "enabled": row.enabled,
    }


def _dimension_to_dict(row: RiskRuleDimensionRow) -> dict[str, Any]:
    return {
        "dimension_code": row.dimension_code,
        "dimension": row.dimension,
        "rule_type": row.rule_type,
        "description": row.description,
    }


def get_default_repository() -> RiskRuleRepository:
    return RiskRuleRepository()
