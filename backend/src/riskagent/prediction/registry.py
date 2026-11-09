"""Scoring-id registry. The hidden-first scorecard is the only built-in entry."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from riskagent.prediction.protocol import RiskScorer, ScoreResult

_REGISTRY: dict[str, RiskScorer] = {}


class UnknownScorerError(KeyError):
    """Raised when a caller asks for a scoring id that was never registered."""


def register_scorer(scorer: RiskScorer) -> RiskScorer:
    """Register ``scorer`` under ``scorer.scoring_id``.

    Registering the same object again is a no-op. A second, different scorer
    for an id that is already taken is rejected so historical snapshots cannot
    silently change implementation.
    """

    scoring_id = str(getattr(scorer, "scoring_id", "") or "").strip()
    if not scoring_id:
        raise ValueError("评分器缺少 scoring_id")
    if not callable(getattr(scorer, "score", None)):
        raise TypeError("评分器必须实现 score(findings, *, features=None)")
    existing = _REGISTRY.get(scoring_id)
    if existing is not None:
        if existing is scorer:
            return existing
        raise ValueError(f"评分 id 已注册: {scoring_id}")
    _REGISTRY[scoring_id] = scorer
    return scorer


def unregister_scorer(scoring_id: str) -> None:
    """Drop a scorer. Intended for tests that register a temporary id."""

    _REGISTRY.pop(scoring_id, None)


def registered_scoring_ids() -> tuple[str, ...]:
    return tuple(_REGISTRY)


def get_scorer(scoring_id: str) -> RiskScorer:
    try:
        return _REGISTRY[scoring_id]
    except KeyError as exc:
        known = ", ".join(registered_scoring_ids()) or "（无）"
        raise UnknownScorerError(f"未注册评分器 {scoring_id}。当前已注册: {known}") from exc


def score_run(
    findings: Sequence[Mapping[str, Any]],
    *,
    scoring_id: str,
    features: Mapping[str, Any] | None = None,
) -> ScoreResult:
    """Score one run with the scorer registered for ``scoring_id``."""

    return get_scorer(scoring_id).score(findings, features=features)
