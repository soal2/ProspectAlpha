"""Risk-rule repository and agent-facing selection helpers."""

from __future__ import annotations

import copy
import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = BACKEND_ROOT / "configs"
DEFAULT_RULES_PATH = CONFIG_ROOT / "risk_rules.yaml"
BOOTSTRAP_JSON_PATH = CONFIG_ROOT / "hk_ipo_risk_rules_v1.json"


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
    def __init__(self, path: Path | str | None = None) -> None:
        configured = path or os.getenv("RISK_RULES_PATH") or DEFAULT_RULES_PATH
        self.path = Path(configured)

    def ensure_store(self) -> None:
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if BOOTSTRAP_JSON_PATH.exists():
            data = json.loads(BOOTSTRAP_JSON_PATH.read_text(encoding="utf-8"))
        else:
            data = {
                "rule_set_id": "hk_ipo_prospectus_risk_framework_v1",
                "version": "v1.0",
                "name": "港股IPO招股书风险识别框架",
                "totals": {"dimension_count": 0, "category_count": 0, "rule_count": 0},
                "dimensions": [],
                "rules": [],
            }
        self._write_data(data)

    def list_rule_sets(self) -> list[dict[str, Any]]:
        rule_set = self.load()
        return [
            {
                "rule_set_id": rule_set.rule_set_id,
                "version": rule_set.version,
                "name": rule_set.name,
                "totals": rule_set.totals,
                "active": True,
            }
        ]

    def load(self) -> RiskRuleSet:
        self.ensure_store()
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        data = self._normalize_data(data)
        return RiskRuleSet.model_validate(data)

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.load().model_dump())

    def get_rule(self, rule_id: str) -> RiskRule:
        for rule in self.load().rules:
            if rule.rule_id == rule_id:
                return rule
        raise KeyError(rule_id)

    def create_rule(self, payload: RiskRuleCreate) -> RiskRule:
        data = self.load().model_dump()
        rule = RiskRule(rule_id=self._next_rule_id(data["rules"]), **payload.model_dump())
        data["rules"].append(rule.model_dump())
        self._write_data(self._recalculate(data))
        return rule

    def update_rule(self, rule_id: str, payload: RiskRuleUpdate) -> RiskRule:
        data = self.load().model_dump()
        patch = payload.model_dump(exclude_unset=True)
        for idx, existing in enumerate(data["rules"]):
            if existing["rule_id"] == rule_id:
                updated = RiskRule(**{**existing, **patch})
                data["rules"][idx] = updated.model_dump()
                self._write_data(self._recalculate(data))
                return updated
        raise KeyError(rule_id)

    def delete_rule(self, rule_id: str) -> None:
        data = self.load().model_dump()
        original_count = len(data["rules"])
        data["rules"] = [rule for rule in data["rules"] if rule["rule_id"] != rule_id]
        if len(data["rules"]) == original_count:
            raise KeyError(rule_id)
        self._write_data(self._recalculate(data))

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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = self._recalculate(self._normalize_data(data))
        self.path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120),
            encoding="utf-8",
        )

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


def get_default_repository() -> RiskRuleRepository:
    return RiskRuleRepository()
