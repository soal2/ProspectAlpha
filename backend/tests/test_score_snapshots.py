"""Append-only score snapshots.

The findings below are a sanitized synthetic fixture. They are not a real
prospectus, and they are not the reconstructed 44.29 example.
"""

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from riskagent.backtesting.service import BacktestService
from riskagent.database import Base
from riskagent.models import (
    BacktestCandidateSampleRow,
    BacktestCandidateSetRow,
    BacktestRunRow,
    RiskScoreSnapshotRow,
)
from riskagent.prediction.scorecard import WEIGHT_PROFILE, calculate_hidden_first_score


def _service():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return BacktestService(session_factory=factory, initialize=False), factory


def _finding(title: str) -> dict:
    return {
        "dimension_code": "D1",
        "origin": "rule",
        "severity": "high",
        "evidence_grade": "direct",
        "title": title,
    }


def test_synthetic_scores_are_append_only_and_replay_pins_the_first_version() -> None:
    service, factory = _service()
    first_findings = [_finding("合成样本甲")]
    first = service.score(
        run_id="synthetic_run",
        wind_code="9999.HK",
        findings=first_findings,
        analysis_as_of_date="2024-06-30",
        input_hash="input-a",
        evidence_hash="evidence-a",
    )
    again = service.score(
        run_id="synthetic_run",
        wind_code="9999.HK",
        findings=first_findings,
        analysis_as_of_date="2024-06-30",
        input_hash="input-a",
        evidence_hash="evidence-a",
    )
    assert again["score_id"] == first["score_id"]
    assert again["score"] == calculate_hidden_first_score(first_findings)["score"]
    assert WEIGHT_PROFILE == {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0}

    second = service.score(
        run_id="synthetic_run",
        wind_code="9999.HK",
        findings=[_finding("合成样本乙")],
        analysis_as_of_date="2024-06-30",
        input_hash="input-b",
        evidence_hash="evidence-b",
    )
    assert second["score_id"] != first["score_id"]

    with factory() as session:
        session.add(
            RiskScoreSnapshotRow(
                score_id="score_synthetic_run",
                run_id="synthetic_run",
                wind_code="9999.HK",
                scorecard_version="legacy",
                total_score=1.0,
                risk_level="low",
                breakdown={},
                limitations=[],
                analysis_as_of_date="2020-01-01",
                input_hash="old",
                evidence_hash="old",
                content_hash="legacy-row",
                created_at="2020-01-01T00:00:00+00:00",
            )
        )
        session.add(
            BacktestRunRow(
                backtest_id="bt_src",
                snapshot_id="mkt",
                name="合成回测",
                status="completed",
                metrics={},
                protocol={"horizon": 5},
                candidate_set_id="cs_src",
                created_at="2024-07-01T00:00:00+00:00",
            )
        )
        session.add(
            BacktestCandidateSetRow(
                candidate_set_id="cs_src",
                backtest_id="bt_src",
                snapshot_id="mkt",
                status="confirmed",
                created_at="2024-07-01T00:00:00+00:00",
            )
        )
        session.add(
            BacktestCandidateSampleRow(
                id="cs_src:9999.HK",
                candidate_set_id="cs_src",
                wind_code="9999.HK",
                listing_date="2024-07-02",
                label_id="label",
                score_id=first["score_id"],
                source_run_id="synthetic_run",
                inclusion_status="included",
            )
        )
        session.commit()

    service.reproduce("bt_src")
    with factory() as session:
        legacy = session.get(RiskScoreSnapshotRow, "score_synthetic_run")
        assert legacy is not None
        assert legacy.total_score == 1.0
        versions = session.scalars(
            select(RiskScoreSnapshotRow).where(RiskScoreSnapshotRow.run_id == "synthetic_run")
        ).all()
        assert len(versions) == 3
        pinned = session.scalars(
            select(BacktestCandidateSampleRow).where(BacktestCandidateSampleRow.score_id == first["score_id"])
        ).all()
        replay = next(item for item in pinned if item.candidate_set_id != "cs_src")
        assert replay.pinned_score["total_score"] == first["score"]
        assert replay.pinned_score["content_hash"] == first["content_hash"]
        assert session.scalar(select(func.count()).select_from(RiskScoreSnapshotRow)) == 3
