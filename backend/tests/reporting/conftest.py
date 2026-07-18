"""Shared fixtures for reporting tests.

Reporting tests never touch the live research pipeline; they construct a
frozen :class:`ResearchArtifact` and a list of :class:`RedTeamVerdict`
instances directly.  No LLM, no Bocha, no network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

import pytest

from riskagent.research.models import (
    ConfidenceAssessment,
    ConfidenceComponent,
    CoverageTask,
    ExternalSearchPolicy,
    GateResult,
    HypothesisStatus,
    InjectedContext,
    RedTeamDecision,
    RedTeamVerdict,
    ResearchArtifact,
    ResearchCompletionStatus,
    RiskHypothesis,
    SourceCard,
    SourceTier,
    utc_now,
)


def _utc(minutes_offset: int = 0) -> datetime:
    return utc_now() + timedelta(minutes=minutes_offset)


def make_hypothesis(
    *,
    hypothesis_id: str,
    dimension: str = "财务",
    status: HypothesisStatus = HypothesisStatus.investigating,
    importance: str = "medium",
    rule_ids: Iterable[str] = (),
    with_counter_evidence: bool = False,
    agent_name: str = "financial_agent",
    confidence_level: str | None = None,
) -> RiskHypothesis:
    supporting = ["src_a", "src_b"]
    counter = ["src_c"] if with_counter_evidence else []
    confidence = None
    if confidence_level is not None:
        confidence = ConfidenceAssessment(
            level=confidence_level,  # type: ignore[arg-type]
            components=[
                ConfidenceComponent(
                    name="source_authority", score=0.8, reason="tier1 present"
                ),
                ConfidenceComponent(
                    name="counter_evidence",
                    score=0.5 if with_counter_evidence else 1.0,
                    reason="counter-evidence present" if with_counter_evidence else "no counter",
                ),
            ],
        )
    return RiskHypothesis(
        hypothesis_id=hypothesis_id,
        title=f"假设 {hypothesis_id}",
        statement=f"{hypothesis_id} 风险声明",
        dimension=dimension,
        agent_name=agent_name,
        origin="anomaly",
        rule_ids=list(rule_ids),
        importance=importance,  # type: ignore[arg-type]
        status=status,
        supporting_evidence_ids=supporting,
        counter_evidence_ids=counter,
        gate=GateResult(
            passed=status == HypothesisStatus.verified,
            tier1_count=1 if status == HypothesisStatus.verified else 0,
            independent_tier2_clusters=1 if status == HypothesisStatus.verified else 0,
            refutation_search_done=True,
            blocking=[] if status != HypothesisStatus.contested else ["conflict"],
        ),
        confidence=confidence,
    )


def make_evidence(
    *,
    evidence_id: str,
    tier: SourceTier = SourceTier.tier1,
    url: str | None = None,
    selection: str = "selected",
    accessible: bool = True,
    is_post_prospectus_update: bool = False,
) -> SourceCard:
    return SourceCard(
        evidence_id=evidence_id,
        url=url or f"https://example.com/{evidence_id}.html",
        title=f"证据 {evidence_id}",
        site_name="example.com",
        origin="fetched_page",
        snippet=f"{evidence_id} 摘要",
        extract=f"{evidence_id} 原文引用",
        tier=tier,
        tier_reason=f"tier={tier.value}",
        accessible=accessible,
        published_at="2024-01-01",
        event_date="2023-12-01",
        is_post_prospectus_update=is_post_prospectus_update,
        selection=selection,  # type: ignore[arg-type]
    )


def make_verdict(
    *,
    hypothesis_id: str,
    decision: RedTeamDecision,
    minutes_after: int = 0,
    reviewer: str = "zhou_shenyan_redteam_agent",
) -> RedTeamVerdict:
    return RedTeamVerdict(
        hypothesis_id=hypothesis_id,
        decision=decision,
        rationale=f"{decision.value} review for {hypothesis_id}",
        counter_evidence_ids=[],
        reviewer=reviewer,
        created_at=_utc(minutes_after),
    )


def make_frozen_artifact(
    *,
    artifact_id: str = "art_test",
    version: int = 1,
    hypotheses: Iterable[RiskHypothesis] = (),
    evidence: Iterable[SourceCard] = (),
    completion_status: ResearchCompletionStatus = ResearchCompletionStatus.complete,
    plan_dimensions: Iterable[str] = (),
) -> ResearchArtifact:
    plan = [
        CoverageTask(dimension=dim, status="done") for dim in plan_dimensions
    ] or [CoverageTask(dimension="财务", status="done")]
    artifact = ResearchArtifact(
        artifact_id=artifact_id,
        version=version,
        based_on_version=None if version == 1 else version - 1,
        completion_status=completion_status,
        policy=ExternalSearchPolicy.public_only,
        plan=plan,
        hypotheses=list(hypotheses),
        evidence=list(evidence),
    )
    return artifact.freeze(now=_utc(0))


@pytest.fixture
def frozen_artifact_factory():
    return make_frozen_artifact


@pytest.fixture
def hypothesis_factory():
    return make_hypothesis


@pytest.fixture
def evidence_factory():
    return make_evidence


@pytest.fixture
def verdict_factory():
    return make_verdict
