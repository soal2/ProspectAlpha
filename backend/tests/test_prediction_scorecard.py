"""Interface tests for the prediction scorecard and its registry.

Numeric equality with the pre-move scorecard is pinned in
``test_prediction_scorecard_regression.py``. This file checks the explainable
breakdown, the analysis/backtest wiring, and the slot for a future model.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from riskagent.analysis_runs import _overall_risk_level
from riskagent.backtesting import service as backtesting_service
from riskagent.backtesting.service import BacktestService
from riskagent.database import Base
from riskagent.models import RiskScoreSnapshotRow
from riskagent.prediction import (
    EVIDENCE_FACTORS,
    HIDDEN_FIRST_SCORING_ID,
    MODEL_SCORER_REGISTERED,
    SEVERITY_FACTORS,
    WEIGHT_PROFILE,
    ScoreResult,
    UnknownScorerError,
    calculate_hidden_first_score,
    get_scorer,
    hidden_first_scorecard,
    register_model_scorer,
    registered_scoring_ids,
    score_bucket,
    score_run,
    unregister_scorer,
)
from tests.test_prediction_scorecard_regression import TIANYUE_02631_FINDINGS


class _Dump:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        assert mode == "json"
        return self._payload


class _ConstantScorer:
    scoring_id = "test-constant-v1"

    def score(self, findings, *, features=None) -> ScoreResult:
        return ScoreResult(
            scoring_id=self.scoring_id,
            score=12.0,
            risk_level="very_low",
            breakdown={"constant": 12.0},
            limitations=[],
            weight_profile={},
            components=(),
            rule_contributions=(),
        )


def test_legacy_alias_is_the_prediction_scorecard() -> None:
    assert backtesting_service.calculate_hidden_first_score is calculate_hidden_first_score
    assert backtesting_service._score_bucket is score_bucket
    assert backtesting_service.SCORECARD_VERSION == HIDDEN_FIRST_SCORING_ID
    assert backtesting_service.WEIGHT_PROFILE == WEIGHT_PROFILE
    assert backtesting_service.SEVERITY_FACTORS == SEVERITY_FACTORS
    assert backtesting_service.EVIDENCE_FACTORS == EVIDENCE_FACTORS
    assert get_scorer(HIDDEN_FIRST_SCORING_ID) is hidden_first_scorecard()
    assert MODEL_SCORER_REGISTERED is False
    assert registered_scoring_ids() == (HIDDEN_FIRST_SCORING_ID,)


def test_tianyue_breakdown_explains_published_score() -> None:
    features = {"industry_profile": {"code": "SEMICONDUCTOR"}, "text_signal_snapshot": {"vague": 1}}
    explained = hidden_first_scorecard().score(TIANYUE_02631_FINDINGS, features=features)
    plain = hidden_first_scorecard().score(TIANYUE_02631_FINDINGS)

    assert explained == plain
    assert explained.as_legacy_dict() == calculate_hidden_first_score(TIANYUE_02631_FINDINGS)
    assert explained.score == 44.29
    assert explained.risk_level == "medium"
    assert explained.scoring_id == HIDDEN_FIRST_SCORING_ID
    assert {item.component_id: item.contribution for item in explained.components} == {
        "D1": 10.0,
        "D2": 16.7,
        "D3": 17.59,
        "EXT": 0.0,
    }

    by_title = {item.title: item for item in explained.rule_contributions}
    assert by_title["ASP 连续下滑"].component_id == "D1"
    assert by_title["ASP 连续下滑"].included_in_top3 is True
    assert by_title["ASP 连续下滑"].contribution == WEIGHT_PROFILE["D1"] / 3
    assert by_title["产能利用率骤降"].contribution == WEIGHT_PROFILE["D1"] / 3
    assert by_title["供应商集中"].contribution == WEIGHT_PROFILE["D2"] * SEVERITY_FACTORS["high"] / 3
    assert (
        by_title["人才依赖"].contribution
        == WEIGHT_PROFILE["D2"] * SEVERITY_FACTORS["medium"] * EVIDENCE_FACTORS["direct"] / 3
    )
    vague = by_title["模糊措辞密度"]
    d3_factor = SEVERITY_FACTORS["medium"] * EVIDENCE_FACTORS["inferential"]
    assert vague.component_id == "D3"
    assert vague.factor == d3_factor
    assert vague.included_in_top3 is True
    assert vague.contribution == WEIGHT_PROFILE["D3"] * d3_factor / 3
    narrative = by_title["新兴应用叙事 vs 车市价格战"]
    assert narrative.included_in_top3 is False
    assert narrative.contribution == 0.0
    assert narrative.factor == SEVERITY_FACTORS["low"] * EVIDENCE_FACTORS["inferential"]
    tariff = by_title["美对华半导体关税/301"]
    assert tariff.component_id == "EXT"
    assert tariff.factor == 0.0
    assert tariff.contribution == 0.0
    assert "外部共振" in tariff.reason
    counted_d3 = [
        item.contribution
        for item in explained.rule_contributions
        if item.component_id == "D3" and item.included_in_top3
    ]
    assert len(counted_d3) == 3
    assert round(sum(counted_d3), 2) == 17.59


def test_top3_tie_keeps_earliest_findings() -> None:
    findings = [
        {
            "finding_id": f"finding_{index}",
            "title": f"文本 {index}",
            "dimension_code": "D3",
            "severity": "high",
            "evidence_grade": "direct",
            "evidence_ids": [f"ev_{index}"],
        }
        for index in range(8)
    ]
    result = score_run(findings, scoring_id=HIDDEN_FIRST_SCORING_ID)
    included = [item.index for item in result.rule_contributions if item.included_in_top3]
    excluded = [item.index for item in result.rule_contributions if not item.included_in_top3]
    assert included == [0, 1, 2]
    assert excluded == [3, 4, 5, 6, 7]
    assert result.rule_contributions[0].evidence_ids == ("ev_0",)
    assert result.rule_contributions[0].contribution == WEIGHT_PROFILE["D3"] / 3
    assert result.breakdown["D3"] == 35.0
    assert result.score == 35.0


def test_analysis_pipeline_uses_prediction_scorecard() -> None:
    level = _overall_risk_level([_Dump(item) for item in TIANYUE_02631_FINDINGS])
    assert level == "medium"
    assert _overall_risk_level([]) == "unrated"


def test_backtest_service_persists_legacy_breakdown_and_returns_rules() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = BacktestService(session_factory=factory, initialize=False)
    result = service.score(
        run_id="tianyue",
        wind_code="2631",
        findings=TIANYUE_02631_FINDINGS,
        industry_profile={"code": "SEMICONDUCTOR", "name": "半导体"},
        text_signal_snapshot={"vague_density": 1},
    )

    assert result["score"] == 44.29
    assert result["risk_level"] == "medium"
    assert result["scorecard_version"] == HIDDEN_FIRST_SCORING_ID
    assert {item["title"] for item in result["rule_contributions"]} == {
        item["title"] for item in TIANYUE_02631_FINDINGS
    }
    legacy = calculate_hidden_first_score(TIANYUE_02631_FINDINGS)
    assert {key: result[key] for key in legacy} == legacy
    with factory() as session:
        row = session.get(RiskScoreSnapshotRow, "score_tianyue")
    assert row is not None
    assert row.total_score == 44.29
    assert row.risk_level == "medium"
    assert row.breakdown == legacy["breakdown"]
    assert set(row.breakdown) == {
        "D1",
        "D2",
        "D3",
        "EXT",
        "baseline_d1",
        "without_d2",
        "without_d3",
        "without_ext",
    }


def test_future_model_registers_under_a_new_scoring_id() -> None:
    scorer = _ConstantScorer()
    register_model_scorer(scorer)
    try:
        assert score_run([], scoring_id=scorer.scoring_id).score == 12.0
        assert score_run(TIANYUE_02631_FINDINGS, scoring_id=HIDDEN_FIRST_SCORING_ID).score == 44.29
        assert HIDDEN_FIRST_SCORING_ID in registered_scoring_ids()
        assert scorer.scoring_id in registered_scoring_ids()
    finally:
        unregister_scorer(scorer.scoring_id)
    assert registered_scoring_ids() == (HIDDEN_FIRST_SCORING_ID,)


def test_model_cannot_replace_hidden_first_id_and_unknown_id_is_rejected() -> None:
    class _Replacement:
        scoring_id = HIDDEN_FIRST_SCORING_ID

        def score(self, findings, *, features=None):
            raise AssertionError("hidden-first 不应被替换")

    try:
        register_model_scorer(_Replacement())
    except ValueError as exc:
        assert "ipo-risk-v2-hidden-first" in str(exc)
    else:
        raise AssertionError("占用已冻结评分 id 时应拒绝")

    try:
        get_scorer("ipo-risk-ml-v1")
    except UnknownScorerError as exc:
        assert "ipo-risk-ml-v1" in str(exc)
    else:
        raise AssertionError("未注册的模型评分 id 应拒绝")
