"""Scorer protocol and the explainable score returned to evidence views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class ComponentContribution:
    """One scorecard component, such as D1 or EXT."""

    component_id: str
    label: str
    weight: float
    factors: tuple[float, ...]
    top_factors: tuple[float, ...]
    contribution: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "label": self.label,
            "weight": self.weight,
            "factors": list(self.factors),
            "top_factors": list(self.top_factors),
            "contribution": self.contribution,
        }


@dataclass(frozen=True)
class RuleContribution:
    """How one rule hit or finding entered the total score."""

    index: int
    finding_id: str | None
    rule_id: str | None
    title: str | None
    origin: str
    component_id: str
    dimension_code: str
    severity: str
    evidence_grade: str
    hypothesis_status: str
    evidence_ids: tuple[str, ...]
    factor: float
    included_in_top3: bool
    contribution: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "finding_id": self.finding_id,
            "rule_id": self.rule_id,
            "title": self.title,
            "origin": self.origin,
            "component_id": self.component_id,
            "dimension_code": self.dimension_code,
            "severity": self.severity,
            "evidence_grade": self.evidence_grade,
            "hypothesis_status": self.hypothesis_status,
            "evidence_ids": list(self.evidence_ids),
            "factor": self.factor,
            "included_in_top3": self.included_in_top3,
            "contribution": self.contribution,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ScoreResult:
    """Total score, risk level, and the contribution breakdown for one run."""

    scoring_id: str
    score: float
    risk_level: str
    breakdown: dict[str, float]
    limitations: list[str]
    weight_profile: dict[str, float]
    components: tuple[ComponentContribution, ...] = ()
    rule_contributions: tuple[RuleContribution, ...] = ()

    def as_legacy_dict(self) -> dict[str, Any]:
        """Historical ``calculate_hidden_first_score`` payload.

        Numeric fields match the pre-move scorecard. Per-rule rows are omitted
        so existing snapshot writers and regression pins keep the same keys.
        """

        return {
            "score": self.score,
            "risk_level": self.risk_level,
            "breakdown": dict(self.breakdown),
            "limitations": list(self.limitations),
            "scorecard_version": self.scoring_id,
            "weight_profile": dict(self.weight_profile),
        }

    def as_dict(self) -> dict[str, Any]:
        """Legacy payload plus the component and rule rows used for evidence display."""

        payload = self.as_legacy_dict()
        payload["components"] = [item.as_dict() for item in self.components]
        payload["rule_contributions"] = [item.as_dict() for item in self.rule_contributions]
        return payload


@runtime_checkable
class RiskScorer(Protocol):
    """Pluggable scorer, selected by ``scoring_id``.

    ``findings`` are the admitted rule hits and other findings for one run.
    ``features`` is an optional run-level feature map. The hidden-first rule
    scorecard ignores it; a future model may read it under a new scoring id.
    """

    scoring_id: str

    def score(
        self,
        findings: Sequence[Mapping[str, Any]],
        *,
        features: Mapping[str, Any] | None = None,
    ) -> ScoreResult:
        """Return the total score, risk level, and contribution breakdown."""
