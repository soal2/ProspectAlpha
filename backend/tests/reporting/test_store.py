"""Tests for reporting/store.py: DbReportStore + FileReportStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from riskagent.reporting.models import (
    ActorRef,
    ReportAnnotation,
    ResearchTask,
)
from riskagent.reporting.service import BuildRequest, make_service
from riskagent.reporting.store import (
    BundleNotFound,
    DbReportStore,
    FileReportStore,
    StoreError,
    make_db_store,
)
from riskagent.research.models import HypothesisStatus, ResearchCompletionStatus


_CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "report_templates"


def _clean(hyp):
    hyp.supporting_evidence_ids = []
    hyp.counter_evidence_ids = []
    return hyp


def _build_bundle(artifact, run_id="run_store", version=None):
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
    if version is not None and version != bundle.report_version:
        bundle = bundle.model_copy(
            update={"report_version": version, "frozen": False}
        )
        bundle.frozen = True
    return bundle


def _versioned_copy(bundle, version):
    """Produce a frozen copy of ``bundle`` at a different ``report_version``.

    Uses ``model_copy`` to avoid the FrozenBundleError raised by __setattr__.
    """

    copy = bundle.model_copy(update={"report_version": version, "frozen": False})
    copy.frozen = True
    return copy


def _make_artifact(frozen_artifact_factory, hypothesis_factory):
    hyp = _clean(
        hypothesis_factory(
            hypothesis_id="hyp_v",
            status=HypothesisStatus.verified,
            confidence_level="high",
        )
    )
    return frozen_artifact_factory(hypotheses=[hyp])


# ---------------------------------------------------------------------------
# DbReportStore
# ---------------------------------------------------------------------------


def test_db_store_round_trip(frozen_artifact_factory, hypothesis_factory):
    store = make_db_store()
    artifact = _make_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(artifact)
    store.save_bundle(bundle, artifact_snapshot=artifact)

    fetched = store.get_bundle(bundle.run_id, bundle.report_version)
    assert fetched.report_version == bundle.report_version
    assert fetched.run_id == bundle.run_id
    assert fetched.verdict_snapshot_hash == bundle.verdict_snapshot_hash


def test_db_store_latest_returns_highest_version(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    artifact = _make_artifact(frozen_artifact_factory, hypothesis_factory)
    base = _build_bundle(artifact)
    # Save in arbitrary order; latest should still return the highest version.
    store.save_bundle(_versioned_copy(base, 3), artifact_snapshot=artifact)
    store.save_bundle(_versioned_copy(base, 1), artifact_snapshot=artifact)

    latest = store.latest_bundle(base.run_id)
    assert latest is not None
    assert latest.report_version == 3


def test_db_store_insert_only_rejects_duplicate_version(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    artifact = _make_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(artifact)
    store.save_bundle(bundle, artifact_snapshot=artifact)

    with pytest.raises(StoreError):
        store.save_bundle(bundle, artifact_snapshot=artifact)


def test_db_store_get_missing_raises(frozen_artifact_factory):
    store = make_db_store()
    with pytest.raises(BundleNotFound):
        store.get_bundle("missing_run", 1)


def test_db_store_latest_returns_none_for_empty_run():
    store = make_db_store()
    assert store.latest_bundle("nonexistent") is None


def test_db_store_persists_artifact_snapshot(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    artifact = _make_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(artifact)
    store.save_bundle(bundle, artifact_snapshot=artifact)

    snap = store.get_artifact_snapshot(artifact.artifact_id, artifact.version)
    assert snap.artifact_id == artifact.artifact_id
    assert snap.version == artifact.version
    # Snapshot is content-addressed; saving a NEW bundle version against the
    # same artifact snapshot is a no-op for the snapshot side-table.
    store.save_bundle(_versioned_copy(bundle, 2), artifact_snapshot=artifact)
    # The snapshot table still has exactly one row for this artifact/version
    # (verified indirectly: no StoreError, no content drift).


def test_db_store_rejects_unfrozen_bundle(frozen_artifact_factory, hypothesis_factory):
    store = make_db_store()
    artifact = _make_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(artifact)
    # Produce an unfrozen copy via model_copy (frozen bundle blocks __setattr__).
    unfrozen = bundle.model_copy(update={"frozen": False})
    with pytest.raises(StoreError):
        store.save_bundle(unfrozen, artifact_snapshot=artifact)


def test_db_store_artifact_snapshot_drift_detected(
    frozen_artifact_factory, hypothesis_factory
):
    """Saving the same version with different content raises StoreError."""

    store = make_db_store()
    artifact = _make_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(artifact)
    store.save_bundle(bundle, artifact_snapshot=artifact)

    # Build a second artifact with same version but mutated hypothesis list
    artifact2 = frozen_artifact_factory(
        artifact_id=artifact.artifact_id,
        version=artifact.version,
        hypotheses=[],
        completion_status=ResearchCompletionStatus.complete,
    )
    bundle2 = _versioned_copy(_build_bundle(artifact2), bundle.report_version + 1)
    with pytest.raises(StoreError, match="content drift"):
        store.save_bundle(bundle2, artifact_snapshot=artifact2)


def test_db_store_list_bundles_returns_in_version_order(
    frozen_artifact_factory, hypothesis_factory
):
    store = make_db_store()
    artifact = _make_artifact(frozen_artifact_factory, hypothesis_factory)
    base = _build_bundle(artifact)

    for v in (1, 2, 3):
        store.save_bundle(_versioned_copy(base, v), artifact_snapshot=artifact)

    bundles = store.list_bundles(base.run_id)
    assert [b.report_version for b in bundles] == [1, 2, 3]


# ---------------------------------------------------------------------------
# DbReportStore: research task side-table
# ---------------------------------------------------------------------------


def test_db_store_save_and_update_research_task():
    store = make_db_store()
    task = ResearchTask(
        run_id="run_x",
        target_artifact_version=1,
        trigger_kind="human_request_supplement",
        focus_hypothesis_id="hyp_a",
        query="补充搜索: 应收账款",
        actor=ActorRef(actor_kind="anonymous"),
        idempotency_key="rt-001",
    )
    store.save_research_task(task)
    fetched = store.get_research_task(task.task_id)
    assert fetched.status == "pending"

    fetched.status = "running"
    store.save_research_task(fetched)
    fetched2 = store.get_research_task(task.task_id)
    assert fetched2.status == "running"


def test_db_store_list_research_tasks_filter_by_status():
    store = make_db_store()
    for i, status in enumerate(("pending", "running", "completed")):
        task = ResearchTask(
            run_id="run_l",
            target_artifact_version=1,
            trigger_kind="human_request_supplement",
            query=f"q{i}",
            status=status,  # type: ignore[arg-type]
            actor=ActorRef(),
            idempotency_key=f"key-{i}",
        )
        store.save_research_task(task)
    pending = store.list_research_tasks("run_l", status="pending")
    assert len(pending) == 1
    all_tasks = store.list_research_tasks("run_l")
    assert len(all_tasks) == 3


# ---------------------------------------------------------------------------
# FileReportStore
# ---------------------------------------------------------------------------


def test_file_store_round_trip(tmp_path, frozen_artifact_factory, hypothesis_factory):
    store = FileReportStore(tmp_path)
    artifact = _make_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(artifact)
    store.save_bundle(bundle, artifact_snapshot=artifact)

    fetched = store.get_bundle(bundle.run_id, bundle.report_version)
    assert fetched.report_version == bundle.report_version
    assert fetched.verdict_snapshot_hash == bundle.verdict_snapshot_hash


def test_file_store_writes_yaml_files(tmp_path, frozen_artifact_factory, hypothesis_factory):
    store = FileReportStore(tmp_path)
    artifact = _make_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(artifact)
    store.save_bundle(bundle, artifact_snapshot=artifact)

    bundle_file = tmp_path / "report_v2" / bundle.run_id / "bundle_v1.yaml"
    assert bundle_file.exists()
    artifact_file = tmp_path / "report_v2" / f"artifact_{artifact.artifact_id}_v{artifact.version}.yaml"
    assert artifact_file.exists()


def test_file_store_insert_only_rejects_duplicate(
    tmp_path, frozen_artifact_factory, hypothesis_factory
):
    store = FileReportStore(tmp_path)
    artifact = _make_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(artifact)
    store.save_bundle(bundle, artifact_snapshot=artifact)
    with pytest.raises(StoreError):
        store.save_bundle(bundle, artifact_snapshot=artifact)


def test_file_store_latest(tmp_path, frozen_artifact_factory, hypothesis_factory):
    store = FileReportStore(tmp_path)
    artifact = _make_artifact(frozen_artifact_factory, hypothesis_factory)
    base = _build_bundle(artifact)
    for v in (1, 2):
        store.save_bundle(_versioned_copy(base, v), artifact_snapshot=artifact)
    latest = store.latest_bundle(base.run_id)
    assert latest is not None
    assert latest.report_version == 2


def test_file_store_get_missing_raises(tmp_path):
    store = FileReportStore(tmp_path)
    with pytest.raises(BundleNotFound):
        store.get_bundle("nope", 1)


def test_file_store_get_artifact_snapshot(tmp_path, frozen_artifact_factory, hypothesis_factory):
    store = FileReportStore(tmp_path)
    artifact = _make_artifact(frozen_artifact_factory, hypothesis_factory)
    bundle = _build_bundle(artifact)
    store.save_bundle(bundle, artifact_snapshot=artifact)

    snap = store.get_artifact_snapshot(artifact.artifact_id, artifact.version)
    assert snap.artifact_id == artifact.artifact_id


# ---------------------------------------------------------------------------
# DbReportStore: annotation side-table
# ---------------------------------------------------------------------------


def test_db_store_save_annotation():
    store = make_db_store()
    ann = ReportAnnotation(
        run_id="run_a",
        report_version=1,
        target_kind="hypothesis",
        target_id="hyp_v",
        body="需要补充反证",
        actor=ActorRef(actor_kind="named", actor_id="analyst_zhou"),
        idempotency_key="ann-001",
    )
    store.save_annotation(ann)
    # Idempotent on (run, kind, key) UNIQUE constraint
    with pytest.raises(StoreError):
        store.save_annotation(ann)
