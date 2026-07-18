import pytest

from riskagent.research import (
    GateResult,
    HypothesisStatus,
    InvalidTransitionError,
    RedTeamDecision,
    RedTeamVerdict,
    ResearchArtifact,
    ResearchState,
    RiskHypothesis,
    TRANSITIONS,
    allowed_transitions,
    apply_transition,
)

S = HypothesisStatus


def make_state(*hypotheses: RiskHypothesis) -> ResearchState:
    artifact = ResearchArtifact(hypotheses=list(hypotheses))
    return ResearchState(artifact_id=artifact.artifact_id, working=artifact)


def make_hypothesis(**overrides) -> RiskHypothesis:
    payload = {
        "title": "t",
        "statement": "s",
        "dimension": "财务",
        "origin": "anomaly",
    }
    payload.update(overrides)
    return RiskHypothesis(**payload)


PASSING_GATE = GateResult(passed=True, tier1_count=1, refutation_search_done=True)


class TestTransitionTable:
    def test_table_shape(self):
        assert TRANSITIONS[S.discovered] == {S.investigating, S.inconclusive}
        assert TRANSITIONS[S.investigating] == {
            S.partially_supported,
            S.contested,
            S.refuted,
            S.inconclusive,
        }
        assert TRANSITIONS[S.partially_supported] == {
            S.verified,
            S.contested,
            S.refuted,
            S.inconclusive,
            S.investigating,
        }
        assert TRANSITIONS[S.contested] == {
            S.verified,
            S.refuted,
            S.inconclusive,
            S.investigating,
        }
        assert TRANSITIONS[S.verified] == {S.contested}
        assert TRANSITIONS[S.refuted] == frozenset()
        assert TRANSITIONS[S.inconclusive] == {S.investigating}
        assert set(TRANSITIONS) == set(S)

    def test_every_status_covered_by_helper(self):
        for status in S:
            assert allowed_transitions(status) == TRANSITIONS[status]


class TestApplyTransition:
    def test_legal_transition_mutates_and_logs(self):
        hyp = make_hypothesis()
        state = make_state(hyp)
        transition = apply_transition(
            state, hyp.hypothesis_id, S.investigating, actor="engine", reason="开始调查"
        )
        assert hyp.status is S.investigating
        assert state.transitions == [transition]
        assert transition.from_status is S.discovered
        assert transition.to_status is S.investigating
        assert transition.actor == "engine"

    def test_illegal_transition_raises_and_leaves_state(self):
        hyp = make_hypothesis()
        state = make_state(hyp)
        with pytest.raises(InvalidTransitionError):
            apply_transition(
                state, hyp.hypothesis_id, S.verified, actor="engine", reason="跳级"
            )
        assert hyp.status is S.discovered
        assert state.transitions == []

    def test_refuted_is_terminal(self):
        hyp = make_hypothesis(status=S.refuted)
        state = make_state(hyp)
        for target in S:
            with pytest.raises(InvalidTransitionError):
                apply_transition(
                    state, hyp.hypothesis_id, target, actor="engine", reason="x"
                )

    def test_unknown_hypothesis_rejected(self):
        state = make_state(make_hypothesis())
        with pytest.raises(InvalidTransitionError):
            apply_transition(state, "hyp_missing", S.investigating, actor="engine", reason="x")

    def test_transition_log_is_append_only_across_calls(self):
        hyp = make_hypothesis()
        state = make_state(hyp)
        apply_transition(state, hyp.hypothesis_id, S.investigating, actor="engine", reason="a")
        apply_transition(state, hyp.hypothesis_id, S.inconclusive, actor="system", reason="b")
        apply_transition(state, hyp.hypothesis_id, S.investigating, actor="system", reason="c")
        assert [t.to_status for t in state.transitions] == [
            S.investigating,
            S.inconclusive,
            S.investigating,
        ]
        # transitions survive serialization (persisted, recoverable)
        restored = ResearchState.model_validate(state.model_dump(mode="json"))
        assert [t.to_status for t in restored.transitions] == [
            S.investigating,
            S.inconclusive,
            S.investigating,
        ]


class TestVerifiedGuards:
    def test_verified_requires_gate_snapshot(self):
        hyp = make_hypothesis(status=S.partially_supported)
        state = make_state(hyp)
        with pytest.raises(InvalidTransitionError, match="gate"):
            apply_transition(state, hyp.hypothesis_id, S.verified, actor="engine", reason="x")

    def test_verified_requires_passing_gate(self):
        hyp = make_hypothesis(status=S.partially_supported)
        state = make_state(hyp)
        with pytest.raises(InvalidTransitionError):
            apply_transition(
                state,
                hyp.hypothesis_id,
                S.verified,
                actor="engine",
                reason="x",
                gate_snapshot=GateResult(passed=False),
            )

    def test_verified_blocked_by_unresolved_counter_evidence(self):
        hyp = make_hypothesis(status=S.partially_supported)
        state = make_state(hyp)
        blocked = GateResult(
            passed=True,
            tier1_count=1,
            refutation_search_done=True,
            blocking=["unresolved_counter_evidence"],
        )
        with pytest.raises(InvalidTransitionError, match="unresolved_counter_evidence"):
            apply_transition(
                state,
                hyp.hypothesis_id,
                S.verified,
                actor="engine",
                reason="x",
                gate_snapshot=blocked,
            )

    def test_medium_importance_verified_with_gate_only(self):
        hyp = make_hypothesis(status=S.partially_supported, importance="medium")
        state = make_state(hyp)
        transition = apply_transition(
            state,
            hyp.hypothesis_id,
            S.verified,
            actor="engine",
            reason="gate 达标",
            gate_snapshot=PASSING_GATE,
        )
        assert hyp.status is S.verified
        assert hyp.gate == PASSING_GATE
        assert transition.gate_snapshot == PASSING_GATE

    def test_high_importance_verified_requires_red_team(self):
        hyp = make_hypothesis(status=S.partially_supported, importance="high")
        state = make_state(hyp)
        with pytest.raises(InvalidTransitionError, match="red-team"):
            apply_transition(
                state,
                hyp.hypothesis_id,
                S.verified,
                actor="engine",
                reason="x",
                gate_snapshot=PASSING_GATE,
            )
        state.red_team_verdicts.append(
            RedTeamVerdict(
                hypothesis_id=hyp.hypothesis_id,
                decision=RedTeamDecision.verified,
                rationale="复核通过",
            )
        )
        apply_transition(
            state,
            hyp.hypothesis_id,
            S.verified,
            actor="red_team",
            reason="红队复核通过",
            gate_snapshot=PASSING_GATE,
        )
        assert hyp.status is S.verified

    def test_non_verified_red_team_verdict_does_not_unlock(self):
        hyp = make_hypothesis(status=S.partially_supported, importance="high")
        state = make_state(hyp)
        state.red_team_verdicts.append(
            RedTeamVerdict(
                hypothesis_id=hyp.hypothesis_id,
                decision=RedTeamDecision.material_unresolved,
                rationale="仍有未解决反证",
            )
        )
        with pytest.raises(InvalidTransitionError):
            apply_transition(
                state,
                hyp.hypothesis_id,
                S.verified,
                actor="engine",
                reason="x",
                gate_snapshot=PASSING_GATE,
            )

    def test_verified_uses_hypothesis_gate_when_snapshot_omitted(self):
        hyp = make_hypothesis(status=S.partially_supported, gate=PASSING_GATE)
        state = make_state(hyp)
        apply_transition(state, hyp.hypothesis_id, S.verified, actor="engine", reason="x")
        assert hyp.status is S.verified

    def test_red_team_downgrade_from_verified(self):
        hyp = make_hypothesis(status=S.verified, gate=PASSING_GATE)
        state = make_state(hyp)
        apply_transition(
            state, hyp.hypothesis_id, S.contested, actor="red_team", reason="新反证出现"
        )
        assert hyp.status is S.contested
