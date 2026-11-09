"""Risk prediction: the explainable scorecard and a slot for a future model.

Backtesting persists score snapshots and evaluates them. The analysis pipeline
asks this package for the risk level it delivers. Both call
``HiddenFirstScorecard``.
"""

from riskagent.prediction.model_service import MODEL_SCORER_REGISTERED, register_model_scorer
from riskagent.prediction.protocol import (
    ComponentContribution,
    RiskScorer,
    RuleContribution,
    ScoreResult,
)
from riskagent.prediction.registry import (
    UnknownScorerError,
    get_scorer,
    register_scorer,
    registered_scoring_ids,
    score_run,
    unregister_scorer,
)
from riskagent.prediction.scorecard import (
    EVIDENCE_FACTORS,
    HIDDEN_FIRST_SCORING_ID,
    SCORECARD_VERSION,
    SEVERITY_FACTORS,
    WEIGHT_PROFILE,
    HiddenFirstScorecard,
    calculate_hidden_first_score,
    hidden_first_scorecard,
    score_bucket,
)

hidden_first_scorecard()

__all__ = [
    "EVIDENCE_FACTORS",
    "HIDDEN_FIRST_SCORING_ID",
    "MODEL_SCORER_REGISTERED",
    "SCORECARD_VERSION",
    "SEVERITY_FACTORS",
    "WEIGHT_PROFILE",
    "ComponentContribution",
    "HiddenFirstScorecard",
    "RiskScorer",
    "RuleContribution",
    "ScoreResult",
    "UnknownScorerError",
    "calculate_hidden_first_score",
    "get_scorer",
    "hidden_first_scorecard",
    "register_model_scorer",
    "register_scorer",
    "registered_scoring_ids",
    "score_bucket",
    "score_run",
    "unregister_scorer",
]
