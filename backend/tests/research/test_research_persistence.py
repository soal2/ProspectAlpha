"""Persistence: AnalysisRun.research field + ResearchStore + old-run compat."""

from __future__ import annotations

from riskagent.analysis_runs import AnalysisRun
from riskagent.research.models import (
    CoverageTask,
    HypothesisStatus,
    PlannedQuery,
    ResearchArtifact,
    ResearchCompletionStatus,
    ResearchState,
    RiskHypothesis,
    SourceCard,
)
from riskagent.research.store import ResearchStore


def _minimal_run_payload(**overrides) -> dict:
    payload = {
        "run_id": "run_legacy_001",
        "company_name": "歌礼制药",
        "status": "completed",
        "current_stage": "completed",
        "created_at": "2026-01-01T00:00:00+00:00",
        "data_cutoff_date": "2018-07-01",
        "rule_snapshot": {"rule_set_id": "hk_ipo_v1", "version": "v1.0"},
        "input_summary": {"document_text_chars": 0, "structured_data_keys": []},
    }
    payload.update(overrides)
    return payload


class TestOldRunCompat:
    def test_legacy_payload_without_research_loads_as_none(self):
        run = AnalysisRun.model_validate(_minimal_run_payload())
        assert run.research is None
        # Legacy fields still present and untouched.
        assert run.company_name == "歌礼制药"
        assert run.status == "completed"
        assert run.data_cutoff_date == "2018-07-01"

    def test_legacy_status_is_plain_str_not_research_enum(self):
        run = AnalysisRun.model_validate(_minimal_run_payload(status="completed"))
        assert isinstance(run.status, str)
        # No research completion enum value ever leaks into run.status.
        assert run.status not in {c.value for c in ResearchCompletionStatus}

    def test_run_dump_roundtrip_without_research(self):
        run = AnalysisRun.model_validate(_minimal_run_payload())
        dumped = run.model_dump()
        assert dumped["research"] is None
        restored = AnalysisRun.model_validate(dumped)
        assert restored.research is None


class TestResearchRoundTrip:
    def _run_with_research(self) -> AnalysisRun:
        artifact = ResearchArtifact(
            completion_status=ResearchCompletionStatus.running,
            plan=[
                CoverageTask(
                    dimension="财务",
                    queries=[PlannedQuery(text="歌礼制药 现金流", canonical_form="歌礼制药|现金流")],
                )
            ],
            hypotheses=[
                RiskHypothesis(
                    title="核心产品单一依赖",
                    statement="收入高度依赖单一在研产品",
                    dimension="业务",
                    origin="anomaly",
                )
            ],
            evidence=[
                SourceCard(
                    url="https://www.hkexnews.hk/doc.htm",
                    origin="fetched_page",
                    title="招股书",
                )
            ],
        )
        state = ResearchState(
            artifact_id=artifact.artifact_id,
            analysis_depth="deep",
            working=artifact,
        )
        return AnalysisRun.model_validate(_minimal_run_payload(research=state.model_dump(mode="json")))

    def test_research_survives_payload_roundtrip(self):
        run = self._run_with_research()
        assert run.research is not None
        dumped = run.model_dump()
        restored = AnalysisRun.model_validate(dumped)
        assert restored.research is not None
        assert restored.research.analysis_depth == "deep"
        assert restored.research.working.plan[0].dimension == "财务"
        assert restored.research.working.hypotheses[0].title == "核心产品单一依赖"

    def test_research_not_excluded_field_is_in_dump(self):
        run = self._run_with_research()
        # Unlike RunEvidence (exclude=True), research rides the payload.
        assert "research" in run.model_dump()
        assert "evidence" not in run.model_dump()  # evidence is exclude=True


class TestResearchStore:
    def _fresh_state(self) -> ResearchState:
        artifact = ResearchArtifact(completion_status=ResearchCompletionStatus.running)
        return ResearchState(artifact_id=artifact.artifact_id, working=artifact)

    def test_store_emits_and_persists(self):
        events: list[tuple] = []
        persist_calls = {"n": 0}
        state = self._fresh_state()

        def emit(event_type, actor, title, summary, **extra):
            events.append((event_type, actor, title))

        def persist():
            persist_calls["n"] += 1

        store = ResearchStore(state, emit=emit, persist=persist)
        hyp = RiskHypothesis(
            title="t", statement="s", dimension="财务", origin="anomaly"
        )
        store.add_hypothesis(hyp)
        assert state.working.get_hypothesis(hyp.hypothesis_id) is not None
        assert events[-1][0] == ResearchStore.EVENT_HYPOTHESIS_CREATED
        assert persist_calls["n"] == 1

    def test_store_freeze_advances_version(self):
        state = self._fresh_state()
        store = ResearchStore(state)
        frozen = store.freeze_working(
            completion_status=ResearchCompletionStatus.complete, stage="hook_b"
        )
        assert frozen.frozen is True
        assert state.versions == [frozen]
        assert state.working is not None
        assert state.working.version == frozen.version + 1
        assert state.working.based_on_version == frozen.version

    def test_store_transition_records_history(self):
        state = self._fresh_state()
        hyp = RiskHypothesis(title="t", statement="s", dimension="财务", origin="anomaly")
        state.working.hypotheses.append(hyp)
        store = ResearchStore(state)
        store.transition(
            hyp.hypothesis_id,
            HypothesisStatus.investigating,
            actor="engine",
            reason="external investigation started",
        )
        assert state.working.get_hypothesis(hyp.hypothesis_id).status is HypothesisStatus.investigating
        assert len(state.transitions) == 1
        assert state.transitions[0].to_status is HypothesisStatus.investigating
