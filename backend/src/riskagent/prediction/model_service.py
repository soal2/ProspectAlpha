"""Future model scorer boundary.

No model is trained, loaded, or registered. The analysis pipeline and
backtests keep using ``ipo-risk-v2-hidden-first``.

To add a model later:

1. Implement ``RiskScorer`` in a separate module. ``score`` receives the same
   admitted findings plus an optional ``features`` mapping (industry profile,
   text-signal snapshot, or any other run-level features the model needs).
2. Choose a new ``scoring_id``. Do not reuse ``ipo-risk-v2-hidden-first``;
   stored snapshots stay comparable only when that id keeps this rule card.
3. Return ``ScoreResult`` with a total score, a risk level, and per-component
   plus per-rule contributions the evidence view can render.
4. Call ``register_model_scorer`` at process startup, before a run asks for
   the new id. ``score_run(..., scoring_id=...)`` then dispatches to it.
"""

from __future__ import annotations

from riskagent.prediction.protocol import RiskScorer
from riskagent.prediction.registry import register_scorer
from riskagent.prediction.scorecard import HIDDEN_FIRST_SCORING_ID

# Explicit: this package does not ship a learned scorer.
MODEL_SCORER_REGISTERED = False


def register_model_scorer(scorer: RiskScorer) -> RiskScorer:
    """Register an additional scorer. This does not train or load a model."""

    scoring_id = str(getattr(scorer, "scoring_id", "") or "").strip()
    if not scoring_id:
        raise ValueError("模型评分器缺少 scoring_id")
    if scoring_id == HIDDEN_FIRST_SCORING_ID:
        raise ValueError(
            "不能用模型占用已冻结的评分 id ipo-risk-v2-hidden-first；请使用新的 scoring id。"
        )
    return register_scorer(scorer)
