"""Tests for reporting/mutations.py: idempotency, optimistic concurrency,
original-bundle immutability."""

from __future__ import annotations

from pathlib import Path

import pytest

from riskagent.reporting.models import ActorRef
from riskagent.reporting.mutations import (
    AnnotationRequest,
    MutationError,
    MutationService,
    ResolutionRequest,
    RevisionRequest,
)
from riskagent.reporting.service import BuildRequest, make_service
from riskagent.reporting.store import make_db_store
from riskagent.research.models import HypothesisStatus


_CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "report_templates"


def _clean(hyp):
    hyp.supporting_evidence_ids = []
    hyp.counter_evidence_ids = []
    return hyp


def _build_bundle(store, artifact, run_id="run_mut"):
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


# ---------------------------------------------------------------------------
# Annotation
# ---------------------------------------------------------------------------


def test_annotation_persists_and_returns_receipt(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(store, artifact)

    service = MutationService(store)
    req = AnnotationRequest(
        target_kind="hypothesis",
        target_id="hyp_v",
        body="需要补充反证",
        actor=_actor(),
        idempotency_key="ann-1",
    )
    receipt = service.add_annotation(bundle.run_id, bundle.report_version, req)
    assert receipt.run_id == bundle.run_id
    assert receipt.mutation_kind == "annotation"
    assert receipt.target_report_version == bundle.report_version


def test_annotation_idempotent_replay_returns_same_receipt(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(store, artifact)

    service = MutationService(store)
    req = AnnotationRequest(
        target_kind="hypothesis",
        target_id="hyp_v",
        body="同一条批注",
        actor=_actor(),
        idempotency_key="ann-replay",
    )
    r1 = service.add_annotation(bundle.run_id, bundle.report_version, req)
    r2 = service.add_annotation(bundle.run_id, bundle.report_version, req)
    assert r1.receipt_id == r2.receipt_id


def test_annotation_payload_drift_raises(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(store, artifact)

    service = MutationService(store)
    req1 = AnnotationRequest(
        target_kind="hypothesis",
        target_id="hyp_v",
        body="原始批注",
        actor=_actor(),
        idempotency_key="ann-drift",
    )
    service.add_annotation(bundle.run_id, bundle.report_version, req1)

    req2 = req1.model_copy(update={"body": "不同内容"})
    with pytest.raises(MutationError) as exc_info:
        service.add_annotation(bundle.run_id, bundle.report_version, req2)
    assert exc_info.value.reason_code == "payload_drift"


def test_annotation_missing_key_rejected(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(store, artifact)

    service = MutationService(store)
    req = AnnotationRequest(
        target_kind="hypothesis",
        target_id="hyp_v",
        body="missing key",
        actor=_actor(),
        idempotency_key="",
    )
    with pytest.raises(MutationError) as exc_info:
        service.add_annotation(bundle.run_id, bundle.report_version, req)
    assert exc_info.value.reason_code == "missing_idempotency_key"


def test_annotation_named_actor_without_id_rejected(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(store, artifact)

    service = MutationService(store)
    req = AnnotationRequest(
        target_kind="hypothesis",
        target_id="hyp_v",
        body="missing actor id",
        actor=ActorRef(actor_kind="named", actor_id=""),
        idempotency_key="ann-actor",
    )
    with pytest.raises(MutationError) as exc_info:
        service.add_annotation(bundle.run_id, bundle.report_version, req)
    assert exc_info.value.reason_code == "missing_actor"


def test_annotation_missing_bundle_rejected(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    service = MutationService(store)
    req = AnnotationRequest(
        target_kind="hypothesis",
        target_id="hyp_v",
        body="no bundle",
        actor=_actor(),
        idempotency_key="ann-missing",
    )
    with pytest.raises(MutationError) as exc_info:
        service.add_annotation("run_none", 1, req)
    assert exc_info.value.reason_code == "bundle_not_found"


# ---------------------------------------------------------------------------
# Resolution (latest-version gate)
# ---------------------------------------------------------------------------


def test_resolution_persists_against_latest_version(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(store, artifact)

    service = MutationService(store)
    req = ResolutionRequest(
        target_kind="hypothesis",
        target_id="hyp_v",
        action="downgrade",
        reason="证据不足",
        actor=_actor(),
        idempotency_key="res-1",
    )
    receipt = service.add_resolution(bundle.run_id, bundle.report_version, req)
    assert receipt.mutation_kind == "resolution"


def test_resolution_rejected_for_stale_version(
    frozen_artifact_factory, hypothesis_factory
):
    """Resolution must target the latest report_version."""

    store = make_db_store()
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(store, artifact)

    # Build a second bundle version
    v2 = bundle.model_copy(update={"report_version": 2, "frozen": False})
    v2.frozen = True
    store.save_bundle(v2, artifact_snapshot=artifact)

    service = MutationService(store)
    req = ResolutionRequest(
        target_kind="hypothesis",
        target_id="hyp_v",
        action="accept",
        reason="ok",
        actor=_actor(),
        idempotency_key="res-stale",
    )
    with pytest.raises(MutationError) as exc_info:
        service.add_resolution(bundle.run_id, 1, req)
    assert exc_info.value.reason_code == "stale_report_version"


def test_resolution_idempotent_replay(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(store, artifact)

    service = MutationService(store)
    req = ResolutionRequest(
        target_kind="hypothesis",
        target_id="hyp_v",
        action="dismiss",
        reason="不在范围内",
        actor=_actor(),
        idempotency_key="res-replay",
    )
    r1 = service.add_resolution(bundle.run_id, bundle.report_version, req)
    r2 = service.add_resolution(bundle.run_id, bundle.report_version, req)
    assert r1.receipt_id == r2.receipt_id


# ---------------------------------------------------------------------------
# Revision (new bundle version)
# ---------------------------------------------------------------------------


def test_revision_produces_new_version_and_leaves_original_untouched(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(store, artifact)

    service = MutationService(store)
    req = RevisionRequest(
        summary="补充反证后修订",
        diff_evidence_ids_added=["src_new"],
        actor=_actor(),
        idempotency_key="rev-1",
    )
    revision, receipt, new_bundle = service.create_revision(
        bundle.run_id, bundle.report_version, req, artifact_snapshot=artifact
    )
    assert revision.based_on_report_version == 1
    assert revision.new_report_version == 2
    assert new_bundle.report_version == 2
    assert new_bundle.review_state == "human_revised"
    # Original is untouched
    original = store.get_bundle(bundle.run_id, 1)
    assert original.review_state == bundle.review_state
    assert original.report_version == 1


def test_revision_idempotent_replay_returns_existing_version(
    frozen_artifact_factory, hypothesis_factory
):
    """Replaying the same revision request does not produce v3."""

    store = make_db_store()
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(store, artifact)

    service = MutationService(store)
    req = RevisionRequest(
        summary="第一次修订",
        actor=_actor(),
        idempotency_key="rev-replay",
    )
    rev1, rec1, b1 = service.create_revision(
        bundle.run_id, bundle.report_version, req, artifact_snapshot=artifact
    )
    rev2, rec2, b2 = service.create_revision(
        bundle.run_id, bundle.report_version, req, artifact_snapshot=artifact
    )
    assert rec1.receipt_id == rec2.receipt_id
    assert b1.report_version == b2.report_version == 2


def test_revision_rejected_for_stale_version(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(store, artifact)

    service = MutationService(store)
    req = RevisionRequest(
        summary="stale revision",
        actor=_actor(),
        idempotency_key="rev-stale",
    )
    # First revision produces v2.
    service.create_revision(
        bundle.run_id, bundle.report_version, req, artifact_snapshot=artifact
    )
    # Revising v1 again with a different key should fail (latest is now v2).
    req2 = req.model_copy(update={"idempotency_key": "rev-stale-2"})
    with pytest.raises(MutationError) as exc_info:
        service.create_revision(
            bundle.run_id, 1, req2, artifact_snapshot=artifact
        )
    assert exc_info.value.reason_code == "stale_report_version"


# ---------------------------------------------------------------------------
# Original bundle never mutated by side-table mutations
# ---------------------------------------------------------------------------


def test_annotation_does_not_modify_bundle_row(
    frozen_artifact_factory, hypothesis_factory
):
    """An annotation is a side-table row; the bundle JSON is byte-for-byte
    identical before and after."""

    store = make_db_store()
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    artifact = frozen_artifact_factory(hypotheses=[hyp])
    bundle = _build_bundle(store, artifact)

    before = store.get_bundle(bundle.run_id, bundle.report_version).model_dump(
        mode="json"
    )

    service = MutationService(store)
    req = AnnotationRequest(
        target_kind="hypothesis",
        target_id="hyp_v",
        body="side-channel commentary",
        actor=_actor(),
        idempotency_key="ann-immut",
    )
    service.add_annotation(bundle.run_id, bundle.report_version, req)

    after = store.get_bundle(bundle.run_id, bundle.report_version).model_dump(
        mode="json"
    )
    assert before == after
