"""Tests for reporting/mutations.py: research task lifecycle.

Covers: request_supplement (pending-only creation, idempotent replay),
complete_research_task (artifact contract validation, version monotonicity,
no pseudo-version on failure), and fail_research_task (retriable failure,
rejection of already-completed tasks).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from riskagent.reporting.models import ActorRef, ResearchTaskStatus
from riskagent.reporting.mutations import (
    CompleteResearchTaskRequest,
    FailResearchTaskRequest,
    MutationError,
    MutationService,
    SupplementRequest,
    RevisionRequest,
)
from riskagent.reporting.service import BuildRequest, make_service
from riskagent.reporting.store import make_db_store
from riskagent.research.models import (
    HypothesisStatus,
    ResearchArtifact,
    ResearchCompletionStatus,
    RiskHypothesis,
)


_CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "report_templates"


def _clean(hyp):
    hyp.supporting_evidence_ids = []
    hyp.counter_evidence_ids = []
    return hyp


def _build_bundle(store, artifact, run_id="run_task"):
    service = make_service(_CONFIG_DIR)
    request = BuildRequest(
        run_id=run_id,
        artifact=artifact,
        company_name="示例公司",
        stock_code="01234.HK",
        external_cutoff_date="2024-06-30",
        analysis_date="2024-07-15",
    )
    bundle = service.build_bundle(request)
    store.save_bundle(bundle, artifact_snapshot=artifact)
    return bundle


def _actor():
    return ActorRef(actor_kind="named", actor_id="analyst_zhou")


def _make_completed_artifact(frozen_artifact_factory, hypothesis_factory) -> ResearchArtifact:
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    return frozen_artifact_factory(
        hypotheses=[hyp],
        completion_status=ResearchCompletionStatus.complete,
    )


# ---------------------------------------------------------------------------
# request_supplement
# ---------------------------------------------------------------------------


def test_request_supplement_creates_pending_task(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    artifact = _make_completed_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(store, artifact, run_id="run_sup1")

    service = MutationService(store)
    req = SupplementRequest(
        focus_hypothesis_id="hyp_v",
        query="补充行业竞争信息",
        actor=_actor(),
        idempotency_key="sup-1",
    )
    task, receipt = service.request_supplement(
        bundle.run_id, bundle.report_version, req
    )
    assert task.run_id == bundle.run_id
    assert task.target_artifact_version == bundle.report_version
    assert task.status == "pending"
    assert task.focus_hypothesis_id == "hyp_v"
    assert task.query == "补充行业竞争信息"
    assert receipt.mutation_kind == "research_task_request"
    assert receipt.research_task_id == task.task_id


def test_request_supplement_idempotent_replay(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    artifact = _make_completed_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(store, artifact, run_id="run_sup2")

    service = MutationService(store)
    req = SupplementRequest(
        focus_hypothesis_id="hyp_v",
        query="same query",
        actor=_actor(),
        idempotency_key="sup-replay",
    )
    t1, r1 = service.request_supplement(bundle.run_id, bundle.report_version, req)
    t2, r2 = service.request_supplement(bundle.run_id, bundle.report_version, req)
    assert r1.receipt_id == r2.receipt_id
    assert t1.task_id == t2.task_id


def test_request_supplement_payload_drift_raises(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    artifact = _make_completed_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(store, artifact, run_id="run_sup3")

    service = MutationService(store)
    req1 = SupplementRequest(
        focus_hypothesis_id="hyp_v",
        query="original query",
        actor=_actor(),
        idempotency_key="sup-drift",
    )
    service.request_supplement(bundle.run_id, bundle.report_version, req1)

    req2 = SupplementRequest(
        focus_hypothesis_id="hyp_v",
        query="different query",
        actor=_actor(),
        idempotency_key="sup-drift",
    )
    with pytest.raises(MutationError) as exc_info:
        service.request_supplement(bundle.run_id, bundle.report_version, req2)
    assert exc_info.value.reason_code == "payload_drift"


def test_request_supplement_missing_key_rejected(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    artifact = _make_completed_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(store, artifact, run_id="run_sup4")

    service = MutationService(store)
    req = SupplementRequest(
        focus_hypothesis_id="hyp_v",
        query="query",
        actor=_actor(),
        idempotency_key="",
    )
    with pytest.raises(MutationError) as exc_info:
        service.request_supplement(bundle.run_id, bundle.report_version, req)
    assert exc_info.value.reason_code == "missing_idempotency_key"


# ---------------------------------------------------------------------------
# complete_research_task
# ---------------------------------------------------------------------------


def test_complete_research_task_validates_frozen_artifact(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    artifact = _make_completed_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(store, artifact, run_id="run_comp1")

    service = MutationService(store)
    # Create an unfrozen artifact for the completion request.
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v2",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    unfrozen = ResearchArtifact(
        artifact_id="art_unfrozen",
        version=1,
        based_on_version=None,
        completion_status=ResearchCompletionStatus.complete,
        policy=artifact.policy,
        plan=artifact.plan,
        hypotheses=[hyp],
        evidence=artifact.evidence,
    )
    # Do NOT freeze — frozen=False

    req = SupplementRequest(
        focus_hypothesis_id="hyp_v",
        query="query",
        actor=_actor(),
        idempotency_key="sup-comp1",
    )
    task, _ = service.request_supplement(bundle.run_id, bundle.report_version, req)

    complete_req = CompleteResearchTaskRequest(
        new_artifact=unfrozen,
        actor=_actor(),
        idempotency_key="comp-unfrozen",
    )
    with pytest.raises(MutationError) as exc_info:
        service.complete_research_task(
            task.task_id, complete_req,
            expected_target_artifact_version=bundle.report_version,
        )
    assert exc_info.value.reason_code == "invalid_target"


def test_complete_research_task_validates_version_chain(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    artifact = _make_completed_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(store, artifact, run_id="run_comp2")

    service = MutationService(store)
    req = SupplementRequest(
        focus_hypothesis_id="hyp_v",
        query="query",
        actor=_actor(),
        idempotency_key="sup-comp2",
    )
    task, _ = service.request_supplement(bundle.run_id, bundle.report_version, req)

    # Build a new frozen artifact with wrong based_on_version and version.
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v2",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    bad_artifact = ResearchArtifact(
        artifact_id="art_bad",
        version=99,  # wrong version
        based_on_version=99,  # wrong based_on_version
        completion_status=ResearchCompletionStatus.complete,
        policy=artifact.policy,
        plan=artifact.plan,
        hypotheses=[hyp],
        evidence=artifact.evidence,
    ).freeze(now=artifact.frozen_at)

    complete_req = CompleteResearchTaskRequest(
        new_artifact=bad_artifact,
        actor=_actor(),
        idempotency_key="comp-bad-version",
    )
    # based_on_version mismatch
    with pytest.raises(MutationError) as exc_info:
        service.complete_research_task(
            task.task_id, complete_req,
            expected_target_artifact_version=bundle.report_version,
        )
    assert exc_info.value.reason_code == "invalid_target"

    # version != target + 1
    bad_artifact2 = ResearchArtifact(
        artifact_id="art_bad2",
        version=bundle.report_version + 5,  # wrong version
        based_on_version=bundle.report_version,  # correct based_on
        completion_status=ResearchCompletionStatus.complete,
        policy=artifact.policy,
        plan=artifact.plan,
        hypotheses=[hyp],
        evidence=artifact.evidence,
    ).freeze(now=artifact.frozen_at)
    complete_req2 = CompleteResearchTaskRequest(
        new_artifact=bad_artifact2,
        actor=_actor(),
        idempotency_key="comp-bad-version2",
    )
    with pytest.raises(MutationError) as exc_info:
        service.complete_research_task(
            task.task_id, complete_req2,
            expected_target_artifact_version=bundle.report_version,
        )
    assert exc_info.value.reason_code == "invalid_target"


def test_complete_research_task_rejects_non_pending(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    artifact = _make_completed_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(store, artifact, run_id="run_comp3")

    service = MutationService(store)
    req = SupplementRequest(
        focus_hypothesis_id="hyp_v",
        query="query",
        actor=_actor(),
        idempotency_key="sup-comp3",
    )
    task, _ = service.request_supplement(bundle.run_id, bundle.report_version, req)

    # Manually transition task to "completed" to simulate an already-completed task.
    task.status = "completed"
    store.save_research_task(task)

    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v2",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    new_artifact = ResearchArtifact(
        artifact_id="art_comp3",
        version=bundle.report_version + 1,
        based_on_version=bundle.report_version,
        completion_status=ResearchCompletionStatus.complete,
        policy=artifact.policy,
        plan=artifact.plan,
        hypotheses=[hyp],
        evidence=artifact.evidence,
    ).freeze(now=artifact.frozen_at)

    complete_req = CompleteResearchTaskRequest(
        new_artifact=new_artifact,
        actor=_actor(),
        idempotency_key="comp-non-pending",
    )
    with pytest.raises(MutationError) as exc_info:
        service.complete_research_task(
            task.task_id, complete_req,
            expected_target_artifact_version=bundle.report_version,
        )
    assert exc_info.value.reason_code == "invalid_target"


def test_complete_research_task_builds_new_bundle_version(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    artifact = _make_completed_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(store, artifact, run_id="run_comp4")

    service = MutationService(store)
    req = SupplementRequest(
        focus_hypothesis_id="hyp_v",
        query="query",
        actor=_actor(),
        idempotency_key="sup-comp4",
    )
    task, _ = service.request_supplement(bundle.run_id, bundle.report_version, req)

    # Build a new frozen artifact with correct version chain.
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v2",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    new_artifact = ResearchArtifact(
        artifact_id="art_comp4",
        version=bundle.report_version + 1,
        based_on_version=bundle.report_version,
        completion_status=ResearchCompletionStatus.complete,
        policy=artifact.policy,
        plan=artifact.plan,
        hypotheses=[hyp],
        evidence=artifact.evidence,
    ).freeze(now=artifact.frozen_at)

    complete_req = CompleteResearchTaskRequest(
        new_artifact=new_artifact,
        actor=_actor(),
        idempotency_key="comp4",
    )
    completed_task, receipt, new_bundle = service.complete_research_task(
        task.task_id, complete_req,
        expected_target_artifact_version=bundle.report_version,
    )
    assert completed_task.status == "completed"
    assert completed_task.completed_at is not None
    assert new_bundle.report_version == bundle.report_version + 1
    # The new bundle is built from the new artifact
    assert new_bundle.research_artifact_id == "art_comp4"
    assert new_bundle.research_artifact_version == bundle.report_version + 1
    assert receipt.mutation_kind == "research_task_complete"
    assert receipt.produced_report_version == bundle.report_version + 1
    assert receipt.research_task_id == task.task_id


def test_complete_research_task_version_monotonic_from_latest(
    frozen_artifact_factory, hypothesis_factory
):
    """Even if new_artifact claims a version, the store bumps monotonically."""
    store = make_db_store()
    artifact = _make_completed_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(store, artifact, run_id="run_comp5")

    # Add a manual revision to make the latest version v2.
    rev_req = RevisionRequest(
        summary="manual rev",
        diff_evidence_ids_added=[],
        diff_evidence_ids_removed=[],
        actor=_actor(),
        idempotency_key="manual-rev-5",
    )
    service = MutationService(store)
    service.create_revision(bundle.run_id, bundle.report_version, rev_req, artifact_snapshot=artifact)

    # Now request supplement at v2.
    sup_req = SupplementRequest(
        focus_hypothesis_id="hyp_v",
        query="query",
        actor=_actor(),
        idempotency_key="sup-comp5",
    )
    task, _ = service.request_supplement(bundle.run_id, 2, sup_req)

    # New artifact claims version 3 (correct per contract: target+1).
    # The store should produce bundle version 3 (already the latest is v2).
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v2",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    new_artifact = ResearchArtifact(
        artifact_id="art_comp5",
        version=3,  # correct per contract: target(2) + 1
        based_on_version=2,
        completion_status=ResearchCompletionStatus.complete,
        policy=artifact.policy,
        plan=artifact.plan,
        hypotheses=[hyp],
        evidence=artifact.evidence,
    ).freeze(now=artifact.frozen_at)

    complete_req = CompleteResearchTaskRequest(
        new_artifact=new_artifact,
        actor=_actor(),
        idempotency_key="comp5",
    )
    _, _, new_bundle = service.complete_research_task(
        task.task_id, complete_req,
        expected_target_artifact_version=2,
    )
    assert new_bundle.report_version == 3


def test_complete_research_task_fails_task_on_build_error(
    frozen_artifact_factory, hypothesis_factory
):
    """If bundle build fails inside complete_research_task, the task is marked failed."""
    store = make_db_store()
    artifact = _make_completed_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(store, artifact, run_id="run_comp6")

    service = MutationService(store)
    req = SupplementRequest(
        focus_hypothesis_id="hyp_v",
        query="query",
        actor=_actor(),
        idempotency_key="sup-comp6",
    )
    task, _ = service.request_supplement(bundle.run_id, bundle.report_version, req)

    # Pass an artifact with completion_status=failed so build_bundle rejects it.
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v2",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    bad_artifact = ResearchArtifact(
        artifact_id="art_failed",
        version=bundle.report_version + 1,
        based_on_version=bundle.report_version,
        completion_status=ResearchCompletionStatus.failed,
        policy=artifact.policy,
        plan=artifact.plan,
        hypotheses=[hyp],
        evidence=artifact.evidence,
    ).freeze(now=artifact.frozen_at)

    complete_req = CompleteResearchTaskRequest(
        new_artifact=bad_artifact,
        actor=_actor(),
        idempotency_key="comp6-fail",
    )
    with pytest.raises(MutationError) as exc_info:
        service.complete_research_task(
            task.task_id, complete_req,
            expected_target_artifact_version=bundle.report_version,
        )
    assert exc_info.value.reason_code == "invalid_target"

    # Task should be marked failed.
    updated_task = store.get_research_task(task.task_id)
    assert updated_task.status == "failed"
    assert "failed" in updated_task.failure_reason.lower()


# ---------------------------------------------------------------------------
# fail_research_task
# ---------------------------------------------------------------------------


def test_fail_research_task_marks_pending_as_failed(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    artifact = _make_completed_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(store, artifact, run_id="run_fail1")

    service = MutationService(store)
    req = SupplementRequest(
        focus_hypothesis_id="hyp_v",
        query="query",
        actor=_actor(),
        idempotency_key="sup-fail1",
    )
    task, _ = service.request_supplement(bundle.run_id, bundle.report_version, req)

    fail_req = FailResearchTaskRequest(
        failure_reason="manual abort",
        actor=_actor(),
        idempotency_key="fail1",
    )
    failed_task, receipt = service.fail_research_task(task.task_id, fail_req)
    assert failed_task.status == "failed"
    assert failed_task.failure_reason == "manual abort"
    assert receipt.mutation_kind == "research_task_fail"


def test_fail_research_task_marks_running_as_failed(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    artifact = _make_completed_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(store, artifact, run_id="run_fail2")

    service = MutationService(store)
    req = SupplementRequest(
        focus_hypothesis_id="hyp_v",
        query="query",
        actor=_actor(),
        idempotency_key="sup-fail2",
    )
    task, _ = service.request_supplement(bundle.run_id, bundle.report_version, req)

    # Simulate a running task.
    task.status = "running"
    store.save_research_task(task)

    fail_req = FailResearchTaskRequest(
        failure_reason="timeout",
        actor=_actor(),
        idempotency_key="fail2",
    )
    failed_task, _ = service.fail_research_task(task.task_id, fail_req)
    assert failed_task.status == "failed"
    assert failed_task.failure_reason == "timeout"


def test_fail_research_task_rejects_already_completed(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    artifact = _make_completed_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(store, artifact, run_id="run_fail3")

    service = MutationService(store)
    req = SupplementRequest(
        focus_hypothesis_id="hyp_v",
        query="query",
        actor=_actor(),
        idempotency_key="sup-fail3",
    )
    task, _ = service.request_supplement(bundle.run_id, bundle.report_version, req)

    # Manually mark as completed.
    task.status = "completed"
    store.save_research_task(task)

    fail_req = FailResearchTaskRequest(
        failure_reason="too late",
        actor=_actor(),
        idempotency_key="fail3",
    )
    with pytest.raises(MutationError) as exc_info:
        service.fail_research_task(task.task_id, fail_req)
    assert exc_info.value.reason_code == "invalid_target"


def test_fail_research_task_idempotent_replay(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    artifact = _make_completed_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(store, artifact, run_id="run_fail4")

    service = MutationService(store)
    req = SupplementRequest(
        focus_hypothesis_id="hyp_v",
        query="query",
        actor=_actor(),
        idempotency_key="sup-fail4",
    )
    task, _ = service.request_supplement(bundle.run_id, bundle.report_version, req)

    fail_req = FailResearchTaskRequest(
        failure_reason="retry",
        actor=_actor(),
        idempotency_key="fail4",
    )
    r1 = service.fail_research_task(task.task_id, fail_req)
    r2 = service.fail_research_task(task.task_id, fail_req)
    assert r1[1].receipt_id == r2[1].receipt_id
