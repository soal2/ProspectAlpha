"""Canonical risk-level vocabulary and score thresholds.

The report rating and the frozen backtest scorecard MUST derive their risk
level from the same threshold table (frozen-score-first policy), otherwise the
delivered report and the backtest sample pool contradict each other.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class RiskLevel(str, Enum):
    """Five-level risk rating for IPO risk assessment."""

    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


# Backward-compatibility mapping: old 3-level → new 5-level
LEGACY_RISK_LEVEL_MAP: dict[str, RiskLevel] = {
    "high": RiskLevel.VERY_HIGH,
    "mid": RiskLevel.MEDIUM,
    "low": RiskLevel.LOW,
    "very_high": RiskLevel.VERY_HIGH,
    "very_low": RiskLevel.VERY_LOW,
    "medium": RiskLevel.MEDIUM,
}


# Score → risk level thresholds (inclusive lower bound)
SCORE_THRESHOLDS: list[tuple[float, RiskLevel]] = [
    (80.0, RiskLevel.VERY_HIGH),
    (60.0, RiskLevel.HIGH),
    (40.0, RiskLevel.MEDIUM),
    (20.0, RiskLevel.LOW),
    (0.0, RiskLevel.VERY_LOW),
]


def map_legacy_risk_level(legacy: str) -> RiskLevel:
    """Map a legacy risk level string to the new 5-level RiskLevel enum."""
    return LEGACY_RISK_LEVEL_MAP.get(legacy.strip().lower(), RiskLevel.MEDIUM)


def score_to_risk_level(score: float) -> RiskLevel:
    """Map a 0-100 risk score to a 5-level RiskLevel via threshold table."""
    clamped = max(0.0, min(100.0, score))
    for threshold, level in SCORE_THRESHOLDS:
        if clamped >= threshold:
            return level
    return RiskLevel.VERY_LOW


def frozen_risk_level_from_events(events: Any) -> str | None:
    """Read the risk level frozen by the backtest scorecard from run events.

    Returns the most recent ``risk_score_frozen`` payload's ``risk_level``, or
    None when no score was frozen (missing stock code, scorecard failure, ...).
    """
    for event in reversed(list(events or [])):
        if not isinstance(event, dict):
            continue
        if event.get("event_type") != "risk_score_frozen":
            continue
        payload = event.get("payload")
        if isinstance(payload, dict) and payload.get("risk_level"):
            return str(payload["risk_level"])
    return None
