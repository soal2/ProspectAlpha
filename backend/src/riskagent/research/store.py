"""Durable writer for research state.

Every mutation of a run's :class:`ResearchState` goes through a
:class:`ResearchStore`.  The store is handed two closures by the repository:

* ``emit`` appends a durable lifecycle event (``research.*``) to the run's
  append-only event log using the same ``evt_`` id + de-dup machinery as the
  rest of the pipeline;
* ``persist`` writes the run back through the repository's lock + atomic save.

Tool-call audit is deliberately NOT emitted as durable events (one event per
call would swamp ``run.events``); it is persisted inside
``ResearchState.tool_audit`` instead.  Per-call progress stays broker-only.

The store is usable standalone (both closures default to no-ops) so the gate,
state machine, red team and engine can be unit-tested without a repository.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable, Optional, Sequence

from . import state_machine
from .models import (
    GateResult,
    HypothesisStatus,
    HypothesisTransition,
    ResearchArtifact,
    ResearchCheckpoint,
    ResearchCompletionStatus,
    RedTeamVerdict,
    RiskHypothesis,
    SourceCard,
    ToolAuditRecord,
    utc_now,
)

EmitFn = Callable[..., None]
PersistFn = Callable[[], None]


def _noop_emit(*args, **kwargs) -> None:  # pragma: no cover - trivial default
    return None


def _noop_persist() -> None:  # pragma: no cover - trivial default
    return None


class ResearchStore:
    # Durable lifecycle event types.
    EVENT_PLAN_CREATED = "research.plan.created"
    EVENT_HYPOTHESIS_CREATED = "research.hypothesis.created"
    EVENT_HYPOTHESIS_TRANSITION = "research.hypothesis.transition"
    EVENT_REDTEAM_VERDICT = "research.redteam.verdict"
    EVENT_ARTIFACT_FROZEN = "research.artifact.frozen"
    EVENT_CHECKPOINT = "research.checkpoint"
    EVENT_EXTERNAL_INCOMPLETE = "research.external_incomplete"

    def __init__(
        self,
        state,
        *,
        emit: EmitFn = _noop_emit,
        persist: PersistFn = _noop_persist,
    ) -> None:
        self.state = state
        self._emit = emit
        self._persist = persist

    # ------------------------------------------------------------------
    # plan / hypotheses / evidence / audit

    def set_plan(self, artifact: ResearchArtifact) -> ResearchArtifact:
        self.state.working = artifact
        self.state.artifact_id = artifact.artifact_id
        self._emit(
            self.EVENT_PLAN_CREATED,
            "research_engine",
            "研究计划已创建",
            f"覆盖 {len(artifact.plan)} 个基础尽调任务，完成度 {artifact.completion_status.value}。",
            payload={
                "artifact_id": artifact.artifact_id,
                "version": artifact.version,
                "coverage_tasks": len(artifact.plan),
                "completion_status": artifact.completion_status.value,
            },
        )
        self._persist()
        return artifact

    def add_hypothesis(self, hypothesis: RiskHypothesis) -> RiskHypothesis:
        working = self._require_working()
        if working.get_hypothesis(hypothesis.hypothesis_id) is None:
            working.hypotheses.append(hypothesis)
        self._emit(
            self.EVENT_HYPOTHESIS_CREATED,
            hypothesis.agent_name or "research_engine",
            f"提出风险假设：{hypothesis.title}",
            hypothesis.statement,
            payload={
                "hypothesis_id": hypothesis.hypothesis_id,
                "dimension": hypothesis.dimension,
                "origin": hypothesis.origin,
                "importance": hypothesis.importance,
                "rule_ids": list(hypothesis.rule_ids),
            },
        )
        self._persist()
        return hypothesis

    def add_evidence(self, cards: Iterable[SourceCard]) -> None:
        working = self._require_working()
        seen = {card.evidence_id for card in working.evidence}
        added = False
        for card in cards:
            if card.evidence_id in seen:
                continue
            working.evidence.append(card)
            seen.add(card.evidence_id)
            added = True
        if added:
            self._persist()

    def add_audit(self, records: Iterable[ToolAuditRecord]) -> None:
        records = list(records)
        if not records:
            return
        self.state.tool_audit.extend(records)
        self._persist()

    # ------------------------------------------------------------------
    # transitions

    def transition(
        self,
        hypothesis_id: str,
        to_status: HypothesisStatus,
        *,
        actor: str,
        reason: str,
        evidence_ids: Sequence[str] = (),
        tool_call_ids: Sequence[str] = (),
        gate_snapshot: Optional[GateResult] = None,
        now: Optional[datetime] = None,
    ) -> HypothesisTransition:
        transition = state_machine.apply_transition(
            self.state,
            hypothesis_id,
            to_status,
            actor=actor,
            reason=reason,
            evidence_ids=evidence_ids,
            tool_call_ids=tool_call_ids,
            gate_snapshot=gate_snapshot,
            now=now,
        )
        self._emit(
            self.EVENT_HYPOTHESIS_TRANSITION,
            actor,
            f"假设状态迁移：{transition.from_status.value} → {transition.to_status.value}",
            reason,
            payload={
                "transition_id": transition.transition_id,
                "hypothesis_id": hypothesis_id,
                "from_status": transition.from_status.value,
                "to_status": transition.to_status.value,
                "actor": actor,
            },
        )
        self._persist()
        return transition

    # ------------------------------------------------------------------
    # red team

    def add_verdict(self, verdict: RedTeamVerdict) -> RedTeamVerdict:
        self.state.red_team_verdicts.append(verdict)
        self._emit(
            self.EVENT_REDTEAM_VERDICT,
            verdict.reviewer,
            f"红队裁决：{verdict.decision.value}",
            verdict.rationale,
            payload={
                "verdict_id": verdict.verdict_id,
                "hypothesis_id": verdict.hypothesis_id,
                "decision": verdict.decision.value,
            },
        )
        self._persist()
        return verdict

    # ------------------------------------------------------------------
    # freezing / checkpoints

    def freeze_working(
        self,
        *,
        completion_status: ResearchCompletionStatus,
        stage: str,
        now: Optional[datetime] = None,
    ) -> ResearchArtifact:
        working = self._require_working()
        stamp = now or utc_now()
        working.completion_status = completion_status
        frozen = working.freeze(stamp)
        self.state.versions.append(frozen)
        self.state.working = frozen.next_working(stamp)
        self._emit(
            self.EVENT_ARTIFACT_FROZEN,
            "research_engine",
            f"研究版本已冻结 v{frozen.version}",
            f"阶段 {stage}，完成度 {completion_status.value}。",
            payload={
                "artifact_id": frozen.artifact_id,
                "version": frozen.version,
                "completion_status": completion_status.value,
                "hypotheses": len(frozen.hypotheses),
                "evidence": len(frozen.evidence),
            },
        )
        self._persist()
        return frozen

    def checkpoint(self, stage: str, done_task_ids: Sequence[str]) -> ResearchCheckpoint:
        cp = ResearchCheckpoint(stage=stage, done_task_ids=list(done_task_ids))
        self.state.checkpoints.append(cp)
        self._emit(
            self.EVENT_CHECKPOINT,
            "research_engine",
            f"研究检查点：{stage}",
            f"已完成 {len(cp.done_task_ids)} 个任务。",
            payload={"checkpoint_id": cp.checkpoint_id, "stage": stage},
        )
        self._persist()
        return cp

    def note_external_incomplete(self, reason: str) -> None:
        working = self.state.working
        if working is not None:
            working.notes.append(f"external_research_incomplete: {reason}")
        self._emit(
            self.EVENT_EXTERNAL_INCOMPLETE,
            "research_engine",
            "外部研究不完整",
            reason,
            payload={"reason": reason},
        )
        self._persist()

    # ------------------------------------------------------------------

    def _require_working(self) -> ResearchArtifact:
        if self.state.working is None:
            raise RuntimeError("research store has no working artifact")
        return self.state.working
