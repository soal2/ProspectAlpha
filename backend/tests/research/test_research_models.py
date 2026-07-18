from datetime import datetime, timezone

import pytest

from riskagent.research import (
    ConfidenceAssessment,
    ConfidenceComponent,
    CoverageTask,
    ExternalSearchPolicy,
    FrozenArtifactError,
    GateResult,
    HypothesisStatus,
    PlannedQuery,
    RedTeamDecision,
    ResearchArtifact,
    ResearchCompletionStatus,
    ResearchState,
    ReviewState,
    RiskHypothesis,
    SourceCard,
    SourceTier,
)


def make_hypothesis(**overrides):
    payload = {
        "title": "境外子公司未披露诉讼",
        "statement": "发行人境外子公司存在未披露的重大诉讼",
        "dimension": "法务监管",
        "origin": "anomaly",
    }
    payload.update(overrides)
    return RiskHypothesis(**payload)


class TestEnums:
    def test_hypothesis_status_values(self):
        assert {s.value for s in HypothesisStatus} == {
            "discovered",
            "investigating",
            "partially_supported",
            "verified",
            "contested",
            "refuted",
            "inconclusive",
        }

    def test_red_team_decision_values(self):
        assert {d.value for d in RedTeamDecision} == {
            "verified",
            "material_unresolved",
            "request_supplement",
            "downgraded",
            "refuted",
            "escalate_human",
        }

    def test_completion_status_values(self):
        assert {c.value for c in ResearchCompletionStatus} == {
            "not_started",
            "running",
            "complete",
            "external_research_incomplete",
            "failed",
        }

    def test_review_state_values(self):
        assert {r.value for r in ReviewState} == {
            "draft",
            "auto_completed",
            "review_required",
            "human_revised",
        }

    def test_policy_values_and_default(self):
        assert {p.value for p in ExternalSearchPolicy} == {
            "disabled",
            "public_only",
            "approved_sensitive",
        }
        assert ResearchArtifact().policy is ExternalSearchPolicy.public_only

    def test_source_tier_values(self):
        assert {t.value for t in SourceTier} == {"tier1", "tier2", "tier3", "unknown"}


class TestRiskHypothesis:
    def test_rule_ids_optional(self):
        hyp = make_hypothesis()
        assert hyp.rule_ids == []
        assert hyp.status is HypothesisStatus.discovered

    def test_unknown_dimension_rejected(self):
        with pytest.raises(ValueError):
            make_hypothesis(dimension="星座运势")

    def test_coverage_task_dimension_validated(self):
        with pytest.raises(ValueError):
            CoverageTask(dimension="not-a-dimension")


class TestFrozenArtifact:
    def test_freeze_returns_immutable_snapshot(self):
        working = ResearchArtifact()
        working.hypotheses.append(make_hypothesis())
        snapshot = working.freeze()
        assert snapshot.frozen is True
        assert snapshot.frozen_at is not None
        with pytest.raises(FrozenArtifactError):
            snapshot.completion_status = ResearchCompletionStatus.complete
        with pytest.raises(FrozenArtifactError):
            snapshot.notes = ["x"]

    def test_original_stays_mutable_after_freeze(self):
        working = ResearchArtifact()
        working.freeze()
        working.notes = ["still mutable"]
        assert working.notes == ["still mutable"]
        assert working.frozen is False

    def test_frozen_snapshot_is_deep_copy(self):
        working = ResearchArtifact()
        hyp = make_hypothesis()
        working.hypotheses.append(hyp)
        snapshot = working.freeze()
        hyp.title = "changed after freeze"
        assert snapshot.hypotheses[0].title == "境外子公司未披露诉讼"

    def test_next_working_version_monotonic(self):
        v1 = ResearchArtifact()
        frozen1 = v1.freeze()
        v2 = frozen1.next_working()
        assert v2.version == 2
        assert v2.based_on_version == 1
        assert v2.frozen is False
        assert v2.frozen_at is None
        assert v2.artifact_id == v1.artifact_id
        frozen2 = v2.freeze()
        v3 = frozen2.next_working()
        assert v3.version == 3
        assert v3.based_on_version == 2

    def test_frozen_roundtrip_stays_frozen(self):
        snapshot = ResearchArtifact().freeze()
        restored = ResearchArtifact.model_validate(snapshot.model_dump(mode="json"))
        assert restored.frozen is True
        with pytest.raises(FrozenArtifactError):
            restored.notes = ["nope"]


class TestRoundTrip:
    def _full_state(self) -> ResearchState:
        hyp = make_hypothesis(
            rule_ids=["HKIPO-001"],
            importance="high",
            time_sensitive=True,
            gate=GateResult(passed=True, tier1_count=1, refutation_search_done=True),
            confidence=ConfidenceAssessment(
                level="medium",
                components=[
                    ConfidenceComponent(
                        name="source_authority", score=0.9, reason="tier1 监管来源"
                    )
                ],
                llm_self_reported=0.99,
            ),
        )
        card = SourceCard(
            url="https://www.hkexnews.hk/doc.htm",
            origin="fetched_page",
            title="联交所公告",
            tier=SourceTier.tier1,
            published_at="2025-11-01",
        )
        artifact = ResearchArtifact(
            completion_status=ResearchCompletionStatus.running,
            plan=[
                CoverageTask(
                    dimension="财务",
                    queries=[
                        PlannedQuery(text="示例 财务造假", canonical_form="示例|财务造假")
                    ],
                )
            ],
            hypotheses=[hyp],
            evidence=[card],
        )
        return ResearchState(
            artifact_id=artifact.artifact_id,
            analysis_depth="deep",
            versions=[artifact.freeze(datetime(2026, 1, 1, tzinfo=timezone.utc))],
            working=artifact.next_working(),
        )

    def test_json_roundtrip(self):
        state = self._full_state()
        restored = ResearchState.model_validate_json(state.model_dump_json())
        assert restored == state

    def test_python_dump_roundtrip(self):
        state = self._full_state()
        restored = ResearchState.model_validate(state.model_dump(mode="json"))
        assert restored == state

    def test_completion_status_property(self):
        state = self._full_state()
        assert state.completion_status is ResearchCompletionStatus.running
        state.working = None
        assert state.completion_status is ResearchCompletionStatus.running
        state.versions = []
        assert state.completion_status is ResearchCompletionStatus.not_started
