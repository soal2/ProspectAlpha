"""Dual-mode persistence for the V2 reporting layer.

Two interchangeable backends, both with the same Python API:

* :class:`DbReportStore` — SQLAlchemy.  Used in production; relies on the
  ``UNIQUE(run_id, report_version)`` and ``UNIQUE(run_id, mutation_kind,
  idempotency_key)`` constraints to arbitrate concurrency.
* :class:`FileReportStore` — writes a single YAML file per bundle to
  ``data/processed/report_v2/{run_id}/bundle_v{n}.yaml``.  Used in tests
  and as an audit-friendly on-disk mirror.  Files are write-once: a
  re-write at the same version raises ``StoreError`` rather than silently
  overwriting.

The store API is intentionally narrow — callers hand in a frozen bundle
plus its source artifact, and ask for bundles back by run_id/version.
Mutations (annotations, resolutions, research tasks, receipts) are added
in Step 12; here we only expose what build/retrieval needs.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from riskagent.research.models import ResearchArtifact

from .models import (
    ReportAnnotation,
    ReportBundleV2,
    ReportResolution,
    ResearchTask,
    MutationReceipt,
)
from .orm import (
    MutationReceiptRow,
    ReportAnnotationRow,
    ReportBundleV2Row,
    ReportResolutionRow,
    ResearchArtifactSnapshotRow,
    ResearchTaskRow,
)


class StoreError(RuntimeError):
    """Raised when a store invariant is violated (e.g. duplicate version)."""


class BundleNotFound(KeyError):
    """Raised when a bundle/run_id/version combo is absent."""


# ---------------------------------------------------------------------------
# Store protocol
# ---------------------------------------------------------------------------


class ReportStore(Protocol):
    def save_bundle(
        self,
        bundle: ReportBundleV2,
        *,
        artifact_snapshot: ResearchArtifact,
    ) -> None: ...

    def list_bundles(self, run_id: str) -> list[ReportBundleV2]: ...

    def get_bundle(self, run_id: str, report_version: int) -> ReportBundleV2: ...

    def latest_bundle(self, run_id: str) -> ReportBundleV2 | None: ...


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _bundle_to_json(bundle: ReportBundleV2) -> str:
    return json.dumps(
        bundle.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )


def _bundle_from_json(payload: str) -> ReportBundleV2:
    return ReportBundleV2.model_validate(json.loads(payload))


def _artifact_to_json(artifact: ResearchArtifact) -> str:
    return json.dumps(
        artifact.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )


def _artifact_from_json(payload: str) -> ResearchArtifact:
    return ResearchArtifact.model_validate(json.loads(payload))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# DbReportStore
# ---------------------------------------------------------------------------


class DbReportStore:
    """SQLAlchemy-backed store.  Safe for concurrent access."""

    def __init__(self, engine: Engine | str):
        if isinstance(engine, str):
            engine = create_engine(engine, future=True)
        self._engine = engine

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def create_schema(self) -> None:
        """Create the reporting tables.  Idempotent."""

        from riskagent.database import Base  # local import — cycles safe
        from . import orm  # noqa: F401 — register tables on Base

        Base.metadata.create_all(
            self._engine,
            tables=[
                ReportBundleV2Row.__table__,
                ResearchArtifactSnapshotRow.__table__,
                ReportAnnotationRow.__table__,
                ReportResolutionRow.__table__,
                ResearchTaskRow.__table__,
                MutationReceiptRow.__table__,
            ],
        )

    # ------------------------------------------------------------------
    # Bundle writes / reads
    # ------------------------------------------------------------------

    def save_bundle(
        self,
        bundle: ReportBundleV2,
        *,
        artifact_snapshot: ResearchArtifact,
    ) -> None:
        if not bundle.frozen:
            raise StoreError("refusing to persist an unfrozen bundle")
        self._upsert_artifact_snapshot(artifact_snapshot)
        row = self._bundle_to_row(bundle)
        with Session(self._engine) as session:
            existing = session.scalar(
                select(ReportBundleV2Row).where(
                    ReportBundleV2Row.run_id == bundle.run_id,
                    ReportBundleV2Row.report_version == bundle.report_version,
                )
            )
            if existing is not None:
                raise StoreError(
                    f"bundle v{bundle.report_version} already exists for run "
                    f"{bundle.run_id!r} (INSERT-only)"
                )
            session.add(row)
            try:
                session.commit()
            except Exception as exc:  # noqa: BLE001 — surface as StoreError
                session.rollback()
                raise StoreError(f"failed to insert bundle: {exc}") from exc

    def list_bundles(self, run_id: str) -> list[ReportBundleV2]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ReportBundleV2Row)
                .where(ReportBundleV2Row.run_id == run_id)
                .order_by(ReportBundleV2Row.report_version.asc())
            ).all()
            return [_bundle_from_json(row.payload) for row in rows]

    def get_bundle(self, run_id: str, report_version: int) -> ReportBundleV2:
        with Session(self._engine) as session:
            row = session.scalar(
                select(ReportBundleV2Row).where(
                    ReportBundleV2Row.run_id == run_id,
                    ReportBundleV2Row.report_version == report_version,
                )
            )
            if row is None:
                raise BundleNotFound(
                    f"no bundle for run={run_id!r} version={report_version}"
                )
            return _bundle_from_json(row.payload)

    def latest_bundle(self, run_id: str) -> ReportBundleV2 | None:
        bundles = self.list_bundles(run_id)
        return bundles[-1] if bundles else None

    # ------------------------------------------------------------------
    # Artifact snapshot (used by audit consumers)
    # ------------------------------------------------------------------

    def get_artifact_snapshot(
        self,
        artifact_id: str,
        artifact_version: int,
    ) -> ResearchArtifact:
        with Session(self._engine) as session:
            row = session.scalar(
                select(ResearchArtifactSnapshotRow).where(
                    ResearchArtifactSnapshotRow.artifact_id == artifact_id,
                    ResearchArtifactSnapshotRow.artifact_version == artifact_version,
                )
            )
            if row is None:
                raise BundleNotFound(
                    f"no snapshot for artifact={artifact_id!r} version={artifact_version}"
                )
            return _artifact_from_json(row.payload)

    def _upsert_artifact_snapshot(self, artifact: ResearchArtifact) -> None:
        payload = _artifact_to_json(artifact)
        snapshot_id = (
            f"snap_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"
        )
        with Session(self._engine) as session:
            existing = session.scalar(
                select(ResearchArtifactSnapshotRow).where(
                    ResearchArtifactSnapshotRow.artifact_id == artifact.artifact_id,
                    ResearchArtifactSnapshotRow.artifact_version == artifact.version,
                )
            )
            if existing is not None:
                # Snapshot is content-addressed; same version + same hash = no-op.
                if existing.payload == payload:
                    return
                raise StoreError(
                    f"artifact {artifact.artifact_id!r} v{artifact.version} "
                    "snapshot hash mismatch (content drift)"
                )
            session.add(
                ResearchArtifactSnapshotRow(
                    snapshot_id=snapshot_id,
                    artifact_id=artifact.artifact_id,
                    artifact_version=artifact.version,
                    payload=payload,
                )
            )
            session.commit()

    # ------------------------------------------------------------------
    # Mutation side-tables (Step 12 uses these)
    # ------------------------------------------------------------------

    def save_annotation(self, annotation: ReportAnnotation) -> None:
        with Session(self._engine) as session:
            session.add(
                ReportAnnotationRow(
                    annotation_id=annotation.annotation_id,
                    run_id=annotation.run_id,
                    report_version=annotation.report_version,
                    target_kind=annotation.target_kind,
                    target_id=annotation.target_id,
                    body=annotation.body,
                    actor_kind=annotation.actor.actor_kind,
                    actor_id=annotation.actor.actor_id,
                    idempotency_key=annotation.idempotency_key,
                )
            )
            try:
                session.commit()
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                raise StoreError(f"failed to insert annotation: {exc}") from exc

    def save_resolution(self, resolution: ReportResolution) -> None:
        with Session(self._engine) as session:
            session.add(
                ReportResolutionRow(
                    resolution_id=resolution.resolution_id,
                    run_id=resolution.run_id,
                    report_version=resolution.report_version,
                    target_kind=resolution.target_kind,
                    target_id=resolution.target_id,
                    action=resolution.action,
                    reason=resolution.reason,
                    actor_kind=resolution.actor.actor_kind,
                    actor_id=resolution.actor.actor_id,
                    idempotency_key=resolution.idempotency_key,
                )
            )
            try:
                session.commit()
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                raise StoreError(f"failed to insert resolution: {exc}") from exc

    def save_research_task(self, task: ResearchTask) -> None:
        with Session(self._engine) as session:
            existing = session.get(ResearchTaskRow, task.task_id)
            if existing is None:
                session.add(self._task_to_row(task))
            else:
                # The task table is the only side-table that allows UPDATEs.
                self._copy_task_fields(existing, task)
            try:
                session.commit()
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                raise StoreError(f"failed to save research task: {exc}") from exc

    def get_research_task(self, task_id: str) -> ResearchTask:
        with Session(self._engine) as session:
            row = session.get(ResearchTaskRow, task_id)
            if row is None:
                raise BundleNotFound(f"no research task {task_id!r}")
            return self._row_to_task(row)

    def list_research_tasks(
        self, run_id: str, *, status: str | None = None
    ) -> list[ResearchTask]:
        with Session(self._engine) as session:
            stmt = select(ResearchTaskRow).where(ResearchTaskRow.run_id == run_id)
            if status is not None:
                stmt = stmt.where(ResearchTaskRow.status == status)
            stmt = stmt.order_by(ResearchTaskRow.created_at.asc())
            rows = session.scalars(stmt).all()
            return [self._row_to_task(row) for row in rows]

    def save_receipt(self, receipt: MutationReceipt) -> None:
        with Session(self._engine) as session:
            session.add(
                MutationReceiptRow(
                    receipt_id=receipt.receipt_id,
                    run_id=receipt.run_id,
                    mutation_kind=receipt.mutation_kind,
                    idempotency_key=receipt.idempotency_key,
                    payload_hash=receipt.payload_hash,
                    target_report_version=receipt.target_report_version,
                    produced_report_version=receipt.produced_report_version,
                    research_task_id=receipt.research_task_id,
                )
            )
            try:
                session.commit()
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                raise StoreError(f"failed to insert receipt: {exc}") from exc

    def find_receipt(
        self, run_id: str, mutation_kind: str, idempotency_key: str
    ) -> MutationReceipt | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(MutationReceiptRow).where(
                    MutationReceiptRow.run_id == run_id,
                    MutationReceiptRow.mutation_kind == mutation_kind,
                    MutationReceiptRow.idempotency_key == idempotency_key,
                )
            )
            if row is None:
                return None
            return MutationReceipt(
                receipt_id=row.receipt_id,
                run_id=row.run_id,
                mutation_kind=row.mutation_kind,  # type: ignore[arg-type]
                idempotency_key=row.idempotency_key,
                payload_hash=row.payload_hash,
                target_report_version=row.target_report_version,
                produced_report_version=row.produced_report_version,
                research_task_id=row.research_task_id,
            )

    # ------------------------------------------------------------------
    # Internal: row ↔ model mappers
    # ------------------------------------------------------------------

    @staticmethod
    def _bundle_to_row(bundle: ReportBundleV2) -> ReportBundleV2Row:
        return ReportBundleV2Row(
            bundle_id=f"{bundle.run_id}_v{bundle.report_version}",
            run_id=bundle.run_id,
            report_version=bundle.report_version,
            research_artifact_id=bundle.research_artifact_id,
            research_artifact_version=bundle.research_artifact_version,
            verdict_snapshot_hash=bundle.verdict_snapshot_hash,
            brief_template_id=bundle.brief_template.template_id,
            brief_template_version=bundle.brief_template.version,
            brief_template_sha256=bundle.brief_template.content_sha256,
            deep_template_id=bundle.deep_template.template_id,
            deep_template_version=bundle.deep_template.version,
            deep_template_sha256=bundle.deep_template.content_sha256,
            industry_detected=bundle.industry_profile.detected,
            review_state=bundle.review_state,
            payload=_bundle_to_json(bundle),
        )

    @staticmethod
    def _task_to_row(task: ResearchTask) -> ResearchTaskRow:
        return ResearchTaskRow(
            task_id=task.task_id,
            run_id=task.run_id,
            target_artifact_version=task.target_artifact_version,
            trigger_kind=task.trigger_kind,
            focus_hypothesis_id=task.focus_hypothesis_id,
            query=task.query,
            status=task.status,
            failure_reason=task.failure_reason,
            actor_kind=task.actor.actor_kind,
            actor_id=task.actor.actor_id,
            idempotency_key=task.idempotency_key,
            completed_at=task.completed_at,
        )

    @staticmethod
    def _copy_task_fields(row: ResearchTaskRow, task: ResearchTask) -> None:
        row.status = task.status
        row.failure_reason = task.failure_reason
        row.completed_at = task.completed_at
        row.query = task.query

    @staticmethod
    def _row_to_task(row: ResearchTaskRow) -> ResearchTask:
        from .models import ActorRef

        return ResearchTask(
            task_id=row.task_id,
            run_id=row.run_id,
            target_artifact_version=row.target_artifact_version,
            trigger_kind=row.trigger_kind,  # type: ignore[arg-type]
            focus_hypothesis_id=row.focus_hypothesis_id,
            query=row.query,
            status=row.status,  # type: ignore[arg-type]
            failure_reason=row.failure_reason,
            actor=ActorRef(actor_kind=row.actor_kind, actor_id=row.actor_id),  # type: ignore[arg-type]
            idempotency_key=row.idempotency_key,
            created_at=row.created_at,
            completed_at=row.completed_at,
        )


# ---------------------------------------------------------------------------
# FileReportStore
# ---------------------------------------------------------------------------


class FileReportStore:
    """YAML-on-disk mirror.  Write-once per (run_id, version).

    Layout::

        <root>/report_v2/<run_id>/bundle_v1.yaml
        <root>/report_v2/<run_id>/bundle_v2.yaml
        <root>/report_v2/<run_id>/artifact_<id>_v<n>.yaml
    """

    def __init__(self, root: Path | str):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Bundle writes / reads
    # ------------------------------------------------------------------

    def save_bundle(
        self,
        bundle: ReportBundleV2,
        *,
        artifact_snapshot: ResearchArtifact,
    ) -> None:
        if not bundle.frozen:
            raise StoreError("refusing to persist an unfrozen bundle")
        run_dir = self._run_dir(bundle.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = run_dir / f"bundle_v{bundle.report_version}.yaml"
        if bundle_path.exists():
            raise StoreError(
                f"bundle v{bundle.report_version} already exists at {bundle_path}"
            )
        # Write artifact snapshot first (small, infrequent) so a failure
        # here leaves no half-written bundle file.
        self._write_artifact_snapshot(artifact_snapshot)
        bundle_path.write_text(
            yaml.safe_dump(
                bundle.model_dump(mode="json"),
                allow_unicode=True,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def list_bundles(self, run_id: str) -> list[ReportBundleV2]:
        run_dir = self._run_dir(run_id)
        if not run_dir.exists():
            return []
        paths = sorted(run_dir.glob("bundle_v*.yaml"))
        bundles: list[ReportBundleV2] = []
        for path in paths:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            bundles.append(ReportBundleV2.model_validate(payload))
        return bundles

    def get_bundle(self, run_id: str, report_version: int) -> ReportBundleV2:
        path = self._run_dir(run_id) / f"bundle_v{report_version}.yaml"
        if not path.exists():
            raise BundleNotFound(
                f"no bundle for run={run_id!r} version={report_version}"
            )
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        return ReportBundleV2.model_validate(payload)

    def latest_bundle(self, run_id: str) -> ReportBundleV2 | None:
        bundles = self.list_bundles(run_id)
        return bundles[-1] if bundles else None

    def get_artifact_snapshot(
        self,
        artifact_id: str,
        artifact_version: int,
    ) -> ResearchArtifact:
        path = self._artifact_path(artifact_id, artifact_version)
        if not path.exists():
            raise BundleNotFound(
                f"no snapshot for artifact={artifact_id!r} version={artifact_version}"
            )
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        return ResearchArtifact.model_validate(payload)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_dir(self, run_id: str) -> Path:
        return self._root / "report_v2" / run_id

    def _artifact_path(self, artifact_id: str, version: int) -> Path:
        return self._root / "report_v2" / f"artifact_{artifact_id}_v{version}.yaml"

    def _write_artifact_snapshot(self, artifact: ResearchArtifact) -> None:
        path = self._artifact_path(artifact.artifact_id, artifact.version)
        if path.exists():
            # Content-addressed: same version + same hash = no-op
            existing = yaml.safe_load(path.read_text(encoding="utf-8"))
            new_dump = artifact.model_dump(mode="json")
            if existing == new_dump:
                return
            raise StoreError(
                f"artifact {artifact.artifact_id!r} v{artifact.version} "
                "snapshot hash mismatch (content drift)"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(
                artifact.model_dump(mode="json"),
                allow_unicode=True,
                sort_keys=True,
            ),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Convenience constructor
# ---------------------------------------------------------------------------


def make_db_store(database_url: str = "sqlite:///:memory:") -> DbReportStore:
    """In-memory SQLite by default — production code passes its own URL."""

    store = DbReportStore(database_url)
    store.create_schema()
    return store
