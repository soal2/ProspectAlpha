"""Cohort metric correctness.

These cases are synthetic. A one-company replay is not a cohort evaluation.
"""

from types import SimpleNamespace

from riskagent.backtesting.service import BacktestService, _binary_auc, _grouped_ks


def _item(code: str, score: float, decline: bool):
    label = SimpleNamespace(significant_decline=decline)
    scored = SimpleNamespace(total_score=score, breakdown={"baseline_d1": score}, industry_profile={})
    candidate = SimpleNamespace(wind_code=code)
    return (label, scored, candidate)


def test_ks_is_unchanged_when_tied_scores_are_reordered() -> None:
    forward = [(0.8, True), (0.8, False), (0.2, False), (0.2, True)]
    reversed_ties = [(0.8, False), (0.8, True), (0.2, True), (0.2, False)]

    assert _grouped_ks(forward) == _grouped_ks(reversed_ties)
    assert _grouped_ks(forward)[0] is not None


def test_single_class_auc_and_ks_are_null_with_a_reason() -> None:
    auc, auc_reason = _binary_auc([0.9, 0.4], [])
    ks, ks_reason = _grouped_ks([(0.9, True), (0.4, True)])

    assert auc is None
    assert ks is None
    assert "单一类别" in auc_reason
    assert "单一类别" in ks_reason


def test_lift10_is_not_the_top20_value() -> None:
    eligible = [
        _item("0001.HK", 10, True),
        _item("0002.HK", 9, False),
        _item("0003.HK", 8, False),
        _item("0004.HK", 7, False),
        _item("0005.HK", 6, False),
        _item("0006.HK", 5, False),
        _item("0007.HK", 4, False),
        _item("0008.HK", 3, False),
        _item("0009.HK", 2, False),
        _item("0010.HK", 1, False),
    ]
    metrics = BacktestService._variant_metrics(eligible, lambda score: score.total_score)

    assert metrics["evaluation_scope"] == "cohort"
    assert metrics["lift_top10"] == 10
    assert metrics["lift_top20"] == 5
    assert metrics["auc"] is not None
    summary = BacktestService._summary(
        SimpleNamespace(
            backtest_id="bt",
            name="合成队列",
            created_at="2024-01-01T00:00:00+00:00",
            status="completed",
            candidate_set_id="cs",
            metrics=metrics,
        )
    )
    assert summary.lift10 == 10
    assert summary.liftTop20 == 5
    assert summary.evaluationScope == "cohort"


def test_one_company_replay_does_not_invent_cohort_metrics() -> None:
    metrics = BacktestService._variant_metrics([_item("0001.HK", 3, True)], lambda score: score.total_score)

    assert metrics["evaluation_scope"] == "single_company"
    assert metrics["auc"] is None
    assert metrics["ks"] is None
    assert metrics["lift_top10"] is None
    assert metrics["lift_top20"] is None
    assert "单公司回放" in metrics["auc_reason"]
