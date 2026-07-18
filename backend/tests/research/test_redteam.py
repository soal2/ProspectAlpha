"""Red-team reviewer tests: selection, parsing strictness, decision mapping."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from riskagent.research import (
    ConfidenceAssessment,
    ConfidenceComponent,
    CoverageTask,
    GateResult,
    HypothesisStatus,
    InjectedContext,
    RedTeamDecision,
    RedTeamVerdict,
    ResearchArtifact,
    ResearchState,
    ReviewState,
    RiskHypothesis,
    SourceCard,
    SourceTier,
)
from riskagent.research.redteam import (
    RedTeamParseError,
    RedTeamReviewer,
    parse_verdict_payload,
    select_for_review,
    target_status_for,
)
from riskagent.research.store import ResearchStore


S = HypothesisStatus
D = RedTeamDecision


PASSING_GATE = GateResult(passed=True, tier1_count=1, refutation_search_done=True)


def _state(*hypotheses: RiskHypothesis, evidence=None) -> ResearchState:
    artifact = ResearchArtifact(
        hypotheses=list(hypotheses),
        evidence=list(evidence or []),
    )
    return ResearchState(artifact_id=artifact.artifact_id, working=artifact)


def _store(state: ResearchState) -> ResearchStore:
    return ResearchStore(state)


def _hyp(**overrides) -> RiskHypothesis:
    payload = {
        "title": "t",
        "statement": "s",
        "dimension": "财务",
        "origin": "anomaly",
    }
    payload.update(overrides)
    return RiskHypothesis(**payload)


def _card(evidence_id: str, tier: SourceTier = SourceTier.tier1) -> SourceCard:
    return SourceCard(
        evidence_id=evidence_id,
        url=f"https://www.hkexnews.hk/{evidence_id}.htm",
        title="公告",
        origin="fetched_page",
        tier=tier,
        accessible=True,
    )


def _injected() -> InjectedContext:
    return InjectedContext(run_id="run_test", data_cutoff_date="2025-12-31")


def _component(name: str, score: float) -> ConfidenceComponent:
    return ConfidenceComponent(name=name, score=score, reason="...")


# ----------------------------------------------------------------------
# selection


class TestSelection:
    def test_high_importance_always_selected(self):
        hyp = _hyp(importance="high")
        selected = select_for_review([hyp])
        assert len(selected) == 1
        assert selected[0][0] is hyp
        assert selected[0][1] is None  # no conditional trigger

    def test_low_importance_never_selected(self):
        hyp = _hyp(importance="low")
        assert select_for_review([hyp]) == []

    def test_medium_no_trigger_skipped(self):
        hyp = _hyp(importance="medium")
        assert select_for_review([hyp]) == []

    def test_medium_conflict_counter_evidence_triggers(self):
        hyp = _hyp(
            importance="medium",
            confidence=ConfidenceAssessment(
                level="medium",
                components=[_component("counter_evidence", 0.2)],
            ),
        )
        selected = select_for_review([hyp])
        assert len(selected) == 1
        assert selected[0][1].reason == "source_conflict"

    def test_medium_agent_disagreement_triggers(self):
        hyp = _hyp(
            importance="medium",
            confidence=ConfidenceAssessment(
                level="medium",
                components=[_component("agent_disagreement", 0.3)],
            ),
        )
        selected = select_for_review([hyp])
        assert selected[0][1].reason == "agent_disagreement"

    def test_medium_consistency_triggers(self):
        hyp = _hyp(
            importance="medium",
            confidence=ConfidenceAssessment(
                level="medium",
                components=[_component("consistency", 0.35)],
            ),
        )
        selected = select_for_review([hyp])
        assert selected[0][1].reason == "material_conflict"

    def test_medium_residual_uncertainty_triggers(self):
        hyp = _hyp(
            importance="medium",
            residual_uncertainty="因果链中关键供货商关系未明",
        )
        selected = select_for_review([hyp])
        assert selected[0][1].reason == "weak_causal_chain"


# ----------------------------------------------------------------------
# verdict parsing


class TestParseVerdict:
    def test_valid_payload(self):
        raw = {
            "decision": "verified",
            "rationale": "复核通过，证据扎实",
            "counter_evidence_ids": [],
            "supplement_query": "",
        }
        decision, rationale, ids, supplement = parse_verdict_payload(
            raw,
            hypothesis_id="hyp_a",
            valid_evidence_ids=set(),
        )
        assert decision is D.verified
        assert "复核" in rationale
        assert ids == []
        assert supplement == ""

    def test_unknown_decision_rejected(self):
        raw = {"decision": "我觉得没问题", "rationale": "..."}
        with pytest.raises(RedTeamParseError, match="unknown"):
            parse_verdict_payload(
                raw,
                hypothesis_id="hyp_a",
                valid_evidence_ids=set(),
            )

    def test_missing_rationale_rejected(self):
        raw = {"decision": "verified"}
        with pytest.raises(RedTeamParseError, match="rationale"):
            parse_verdict_payload(
                raw,
                hypothesis_id="hyp_a",
                valid_evidence_ids=set(),
            )

    def test_non_dict_rejected(self):
        with pytest.raises(RedTeamParseError):
            parse_verdict_payload(
                ["decision", "verified"],  # type: ignore[arg-type]
                hypothesis_id="hyp_a",
                valid_evidence_ids=set(),
            )

    def test_counter_evidence_ids_filtered_to_known(self):
        raw = {
            "decision": "material_unresolved",
            "rationale": "存在反证",
            "counter_evidence_ids": ["ev_1", "ev_2", "ev_missing", ""],
        }
        _decision, _rationale, ids, _supplement = parse_verdict_payload(
            raw,
            hypothesis_id="hyp_a",
            valid_evidence_ids={"ev_1", "ev_2"},
        )
        assert ids == ["ev_1", "ev_2"]

    def test_counter_evidence_non_list_rejected(self):
        raw = {
            "decision": "verified",
            "rationale": "...",
            "counter_evidence_ids": "ev_1",
        }
        with pytest.raises(RedTeamParseError, match="counter_evidence"):
            parse_verdict_payload(
                raw,
                hypothesis_id="hyp_a",
                valid_evidence_ids={"ev_1"},
            )


# ----------------------------------------------------------------------
# decision -> status table


class TestDecisionMapping:
    @pytest.mark.parametrize(
        "decision, expected",
        [
            (D.verified, S.verified),
            (D.material_unresolved, S.contested),
            (D.request_supplement, S.investigating),
            (D.downgraded, S.partially_supported),
            (D.refuted, S.refuted),
            (D.escalate_human, S.contested),
        ],
    )
    def test_target_status(self, decision, expected):
        assert target_status_for(decision) is expected


# ----------------------------------------------------------------------
# reviewer end-to-end (LLM mocked)


def _responder(payload: dict[str, Any] | None):
    """Build an async responder that always returns ``payload``."""

    async def _call(_system: str, _user: str):
        return payload

    return _call


def _sequence_responder(*payloads):
    """Return payloads in order, then repeat the last."""

    iterator = iter(payloads)

    async def _call(_system: str, _user: str):
        try:
            return next(iterator)
        except StopIteration:
            return payloads[-1] if payloads else None

    return _call


class TestReviewer:
    def test_verified_decision_transitions_high_importance(self):
        # Engine has already moved hyp to partially_supported with a passing gate.
        hyp = _hyp(
            importance="high",
            status=S.partially_supported,
            gate=PASSING_GATE,
            supporting_evidence_ids=["ev_1"],
        )
        card = _card("ev_1")
        state = _state(hyp, evidence=[card])
        store = _store(state)
        reviewer = RedTeamReviewer(
            llm_call=_responder(
                {
                    "decision": "verified",
                    "rationale": "tier1 证据充分，反证未发现",
                    "counter_evidence_ids": [],
                }
            ),
            store=store,
        )
        outcomes = asyncio.run(reviewer.review(injected=_injected()))
        assert len(outcomes) == 1
        assert outcomes[0].transitioned_to is S.verified
        assert hyp.status is S.verified
        # Verdict recorded with default reviewer agent
        assert len(state.red_team_verdicts) == 1
        assert state.red_team_verdicts[0].decision is D.verified
        assert (
            state.red_team_verdicts[0].reviewer == "zhou_shenyan_redteam_agent"
        )

    def test_material_unresolved_marks_review_required(self):
        hyp = _hyp(importance="high", status=S.investigating)
        state = _state(hyp)
        store = _store(state)
        reviewer = RedTeamReviewer(
            llm_call=_responder(
                {
                    "decision": "material_unresolved",
                    "rationale": "存在未解决反证",
                    "counter_evidence_ids": [],
                }
            ),
            store=store,
        )
        outcomes = asyncio.run(reviewer.review(injected=_injected()))
        assert outcomes[0].transitioned_to is S.contested
        assert hyp.review_state is ReviewState.review_required

    def test_request_supplement_creates_coverage_task(self):
        # Hypothesis is already investigating; ``request_supplement`` maps
        # to investigating so the transition is a no-op, but the supplement
        # task must still be created so the engine can pick it up on resume.
        hyp = _hyp(importance="high", status=S.investigating)
        state = _state(hyp)
        store = _store(state)
        reviewer = RedTeamReviewer(
            llm_call=_responder(
                {
                    "decision": "request_supplement",
                    "rationale": "需要补充检索",
                    "counter_evidence_ids": [],
                    "supplement_query": "公司 重大诉讼 2025",
                }
            ),
            store=store,
        )
        outcomes = asyncio.run(reviewer.review(injected=_injected()))
        assert outcomes[0].transitioned_to is None  # already investigating
        supplement_tasks = [
            task
            for task in state.working.plan
            if hyp.hypothesis_id in task.triggered_hypothesis_ids
        ]
        assert len(supplement_tasks) == 1
        assert supplement_tasks[0].priority == "high"
        assert supplement_tasks[0].queries[0].text == "公司 重大诉讼 2025"

    def test_request_supplement_from_contested_transitions_to_investigating(self):
        # From ``contested`` the ``investigating`` transition is legal.
        hyp = _hyp(importance="high", status=S.contested)
        state = _state(hyp)
        store = _store(state)
        reviewer = RedTeamReviewer(
            llm_call=_responder(
                {
                    "decision": "request_supplement",
                    "rationale": "需要补充检索",
                    "counter_evidence_ids": [],
                    "supplement_query": "公司 重大诉讼 2025",
                }
            ),
            store=store,
        )
        outcomes = asyncio.run(reviewer.review(injected=_injected()))
        assert outcomes[0].transitioned_to is S.investigating
        assert hyp.status is S.investigating

    def test_downgraded_decision(self):
        hyp = _hyp(importance="high", status=S.investigating)
        state = _state(hyp)
        store = _store(state)
        reviewer = RedTeamReviewer(
            llm_call=_responder(
                {
                    "decision": "downgraded",
                    "rationale": "证据强度不足以下降一档",
                    "counter_evidence_ids": [],
                }
            ),
            store=store,
        )
        outcomes = asyncio.run(reviewer.review(injected=_injected()))
        assert outcomes[0].transitioned_to is S.partially_supported

    def test_refuted_decision_terminates(self):
        hyp = _hyp(importance="high", status=S.investigating)
        state = _state(hyp)
        store = _store(state)
        reviewer = RedTeamReviewer(
            llm_call=_responder(
                {
                    "decision": "refuted",
                    "rationale": "反证确凿",
                    "counter_evidence_ids": ["ev_1"],
                }
            ),
            store=store,
        )
        outcomes = asyncio.run(reviewer.review(injected=_injected()))
        assert outcomes[0].transitioned_to is S.refuted
        assert hyp.status is S.refuted

    def test_escalate_human_on_invalid_response(self):
        hyp = _hyp(importance="high", status=S.investigating)
        state = _state(hyp)
        store = _store(state)
        reviewer = RedTeamReviewer(
            llm_call=_responder({"decision": "我认为应该通过", "rationale": "自由文本"}),
            store=store,
        )
        outcomes = asyncio.run(reviewer.review(injected=_injected()))
        assert outcomes[0].verdict.decision is D.escalate_human
        assert outcomes[0].transitioned_to is S.contested
        assert hyp.review_state is ReviewState.review_required

    def test_recovers_when_first_call_fails_then_succeeds(self):
        hyp = _hyp(importance="high", status=S.investigating)
        state = _state(hyp)
        store = _store(state)
        reviewer = RedTeamReviewer(
            llm_call=_sequence_responder(
                None,  # first call infra failure
                {
                    "decision": "refuted",
                    "rationale": "第二次响应通过",
                    "counter_evidence_ids": [],
                },
            ),
            store=store,
        )
        outcomes = asyncio.run(reviewer.review(injected=_injected()))
        assert outcomes[0].verdict.decision is D.refuted
        assert hyp.status is S.refuted

    def test_invalid_counter_evidence_ids_dropped(self):
        hyp = _hyp(importance="high", status=S.investigating)
        state = _state(hyp)
        store = _store(state)
        reviewer = RedTeamReviewer(
            llm_call=_responder(
                {
                    "decision": "material_unresolved",
                    "rationale": "存在反证",
                    "counter_evidence_ids": ["ev_missing", "ev_also_missing"],
                }
            ),
            store=store,
        )
        outcomes = asyncio.run(reviewer.review(injected=_injected()))
        # Decision preserved but bogus evidence IDs stripped
        assert outcomes[0].verdict.counter_evidence_ids == []
        assert outcomes[0].verdict.decision is D.material_unresolved

    def test_only_selected_hypotheses_reviewed(self):
        high = _hyp(importance="high", status=S.investigating)
        low = _hyp(importance="low", status=S.investigating)
        state = _state(high, low)
        store = _store(state)
        reviewer = RedTeamReviewer(
            llm_call=_responder(
                {
                    "decision": "refuted",
                    "rationale": "...",
                    "counter_evidence_ids": [],
                }
            ),
            store=store,
        )
        outcomes = asyncio.run(reviewer.review(injected=_injected()))
        assert len(outcomes) == 1
        assert outcomes[0].verdict.hypothesis_id == high.hypothesis_id
        # Low-importance hyp untouched
        assert low.status is S.investigating

    def test_no_working_artifact_returns_empty(self):
        state = ResearchState(working=None)
        store = _store(state)
        reviewer = RedTeamReviewer(
            llm_call=_responder(None),
            store=store,
        )
        outcomes = asyncio.run(reviewer.review(injected=_injected()))
        assert outcomes == []
