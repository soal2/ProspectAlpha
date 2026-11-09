"""Hidden-first rule scorecard (scoring id ``ipo-risk-v2-hidden-first``).

This is the deterministic score used by the analysis pipeline and by
backtesting. Confidence is ignored on purpose. A future model must use a new
scoring id; it must not replace this one.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from riskagent.common.risk_levels import score_to_risk_level
from riskagent.prediction.protocol import (
    ComponentContribution,
    RuleContribution,
    ScoreResult,
)
from riskagent.prediction.registry import UnknownScorerError, get_scorer, register_scorer

HIDDEN_FIRST_SCORING_ID = "ipo-risk-v2-hidden-first"
SCORECARD_VERSION = HIDDEN_FIRST_SCORING_ID
WEIGHT_PROFILE: dict[str, float] = {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0}
SEVERITY_FACTORS: dict[str, float] = {"high": 1.0, "medium": 0.67, "low": 0.33}
EVIDENCE_FACTORS: dict[str, float] = {
    "direct": 1.0,
    "inferential": 0.75,
    "lead": 0.5,
    "contradicted": 0.0,
}
COMPONENT_LABELS: dict[str, str] = {
    "D1": "标准化财务指标",
    "D2": "非标隐性风险",
    "D3": "文本粉饰度",
    "EXT": "外部共振",
}
EXT_ABSENT_LIMITATION = "未取得上市前独立外部共振证据；EXT 贡献为 0，不按中性值填补。"


def score_bucket(item: Mapping[str, Any]) -> str:
    """Map a finding onto D1, D2, D3, or EXT.

    Text-signal findings always land in D3. Financial aliases (FIN, CASHFLOW,
    ACCOUNT) land in D1. Anything else that is not EXT lands in D2.
    """

    dimension = str(item.get("dimension_code") or "").upper()
    origin = str(item.get("origin") or "rule")
    if dimension == "EXT":
        return "EXT"
    if dimension == "D3" or origin == "text_signal":
        return "D3"
    if dimension == "D1" or any(term in dimension for term in ("FIN", "CASHFLOW", "ACCOUNT")):
        return "D1"
    return "D2"


def _optional_text(item: Mapping[str, Any], key: str) -> str | None:
    value = item.get(key)
    if value is None or value == "":
        return None
    return str(value)


def _evidence_ids(item: Mapping[str, Any]) -> tuple[str, ...]:
    raw = item.get("evidence_ids")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(value) for value in raw if value is not None and str(value) != "")


def _factor(item: Mapping[str, Any], bucket: str) -> tuple[float, str]:
    """Return the finding's factor and a display reason. Confidence is not read."""

    if str(item.get("hypothesis_status") or "") == "refuted":
        return 0.0, "假设已反驳，系数记为 0，不参与加总。"
    if bucket == "EXT":
        status = str(item.get("hypothesis_status") or "")
        if status == "corroborated" and not item.get("counter_evidence_ids"):
            return 1.0, "外部共振已印证且无反证，系数 1.0。"
        if status == "disputed" or (
            item.get("external_evidence_ids") and item.get("counter_evidence_ids")
        ):
            return 0.5, "外部证据存在争议或与反证并存，系数 0.5。"
        return 0.0, "未形成上市前外部共振，系数 0，不按中性值填补。"
    severity = str(item.get("severity") or "").lower()
    grade = str(item.get("evidence_grade") or "direct")
    severity_factor = SEVERITY_FACTORS.get(severity, 0.0)
    evidence_factor = EVIDENCE_FACTORS.get(grade, 0.0)
    return (
        severity_factor * evidence_factor,
        f"严重度 {severity or '缺失'}（{severity_factor}）× 证据等级 {grade}（{evidence_factor}）。",
    )


class HiddenFirstScorecard:
    """Explainable v2 scorecard. ``features`` is accepted and ignored."""

    scoring_id = HIDDEN_FIRST_SCORING_ID

    def score(
        self,
        findings: Sequence[Mapping[str, Any]],
        *,
        features: Mapping[str, Any] | None = None,
    ) -> ScoreResult:
        # Run-level features are part of the scorer contract for a future model.
        # This rule card does not read them, so they cannot move the v2 numbers.
        if features is not None and not isinstance(features, Mapping):
            raise TypeError("features 必须是映射或 None")
        raw: dict[str, list[float]] = {key: [] for key in WEIGHT_PROFILE}
        pending: list[dict[str, Any]] = []
        for index, item in enumerate(findings):
            bucket = score_bucket(item)
            factor, reason = _factor(item, bucket)
            raw[bucket].append(factor)
            pending.append(
                {
                    "index": index,
                    "bucket": bucket,
                    "factor": factor,
                    "reason": reason,
                    "item": item,
                }
            )
        contributions = {
            bucket: round(
                weight * min(1.0, sum(sorted(raw[bucket], reverse=True)[:3]) / 3),
                2,
            )
            for bucket, weight in WEIGHT_PROFILE.items()
        }
        included: dict[str, set[int]] = {}
        for bucket in WEIGHT_PROFILE:
            ranked = sorted(
                (row for row in pending if row["bucket"] == bucket),
                key=lambda row: (-row["factor"], row["index"]),
            )
            included[bucket] = {row["index"] for row in ranked[:3]}
        rules: list[RuleContribution] = []
        for row in pending:
            bucket = str(row["bucket"])
            item = row["item"]
            factor = float(row["factor"])
            in_top3 = row["index"] in included[bucket]
            rules.append(
                RuleContribution(
                    index=row["index"],
                    finding_id=_optional_text(item, "finding_id"),
                    rule_id=_optional_text(item, "rule_id"),
                    title=_optional_text(item, "title"),
                    origin=str(item.get("origin") or "rule"),
                    component_id=bucket,
                    dimension_code=str(item.get("dimension_code") or ""),
                    severity=str(item.get("severity") or ""),
                    evidence_grade=str(item.get("evidence_grade") or ""),
                    hypothesis_status=str(item.get("hypothesis_status") or ""),
                    evidence_ids=_evidence_ids(item),
                    factor=factor,
                    included_in_top3=in_top3,
                    contribution=(WEIGHT_PROFILE[bucket] * factor / 3) if in_top3 else 0.0,
                    reason=str(row["reason"]),
                )
            )
        components = tuple(
            ComponentContribution(
                component_id=bucket,
                label=COMPONENT_LABELS[bucket],
                weight=weight,
                factors=tuple(raw[bucket]),
                top_factors=tuple(sorted(raw[bucket], reverse=True)[:3]),
                contribution=contributions[bucket],
            )
            for bucket, weight in WEIGHT_PROFILE.items()
        )
        baseline_d1 = round(
            100 * min(1.0, sum(sorted(raw["D1"], reverse=True)[:3]) / 3),
            2,
        )
        total = round(sum(contributions.values()), 2)
        limitations = [EXT_ABSENT_LIMITATION] if contributions["EXT"] == 0 else []
        breakdown = {
            **contributions,
            "baseline_d1": baseline_d1,
            "without_d2": round(total - contributions["D2"], 2),
            "without_d3": round(total - contributions["D3"], 2),
            "without_ext": round(total - contributions["EXT"], 2),
        }
        return ScoreResult(
            scoring_id=self.scoring_id,
            score=total,
            risk_level=score_to_risk_level(total).value,
            breakdown=breakdown,
            limitations=limitations,
            weight_profile=dict(WEIGHT_PROFILE),
            components=components,
            rule_contributions=tuple(rules),
        )


_SINGLETON: HiddenFirstScorecard | None = None


def hidden_first_scorecard() -> HiddenFirstScorecard:
    """Return the process-wide hidden-first scorecard and keep it registered."""

    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = HiddenFirstScorecard()
    try:
        current = get_scorer(HIDDEN_FIRST_SCORING_ID)
    except UnknownScorerError:
        register_scorer(_SINGLETON)
        return _SINGLETON
    if current is not _SINGLETON:
        raise RuntimeError("hidden-first 评分 id 被其他评分器占用")
    return _SINGLETON


def calculate_hidden_first_score(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure v2 scorecard calculation; confidence is intentionally ignored."""

    return hidden_first_scorecard().score(findings).as_legacy_dict()
