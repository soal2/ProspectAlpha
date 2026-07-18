"""SQLAlchemy ORM tables for the V2 reporting layer.

Six tables, all derived from the project-wide :class:`riskagent.database.Base`.
The reporting layer never edits the legacy ``AnalysisRun`` payload: bundles
are INSERT-only rows here, and the only mutable side-table is
:class:`ResearchTaskRow` (pending → running → completed/failed).

Concurrency model:

* ``UNIQUE(run_id, report_version)`` on the bundle row makes a
  last-writer-wins build a hard 422 at the database layer.
* ``UNIQUE(run_id, mutation_kind, idempotency_key)`` plus the stored
  ``payload_hash`` implement idempotent mutations: replay returns the
  original receipt; a different payload against the same key is a 409.
* ``ResearchTaskRow`` carries its own ``status`` column that the
  ``complete_research_task`` path transitions in a single transaction.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from riskagent.database import Base


class ReportBundleV2Row(Base):
    """INSERT-only.  Each row is one immutable bundle version."""

    __tablename__ = "report_bundles_v2"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "report_version",
            name="uq_report_bundles_v2_run_version",
        ),
    )

    bundle_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    report_version: Mapped[int] = mapped_column(Integer, nullable=False)
    research_artifact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    research_artifact_version: Mapped[int] = mapped_column(Integer, nullable=False)
    verdict_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    brief_template_id: Mapped[str] = mapped_column(String(128), nullable=False)
    brief_template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    brief_template_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    deep_template_id: Mapped[str] = mapped_column(String(128), nullable=False)
    deep_template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    deep_template_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    industry_detected: Mapped[str] = mapped_column(String(32), nullable=False)
    review_state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON dump of ReportBundleV2
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ResearchArtifactSnapshotRow(Base):
    """Frozen-point-in-time artifact snapshot attached to a bundle.

    Two bundles built against the same artifact version share the same
    snapshot row (keyed by artifact_id + version).  The renderer never
    reads from here directly; this row exists so audit consumers can
    reconstruct the artifact without replaying the pipeline.
    """

    __tablename__ = "research_artifact_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "artifact_id",
            "artifact_version",
            name="uq_research_artifact_snapshots_id_version",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    artifact_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON dump of ResearchArtifact
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ReportAnnotationRow(Base):
    """Reader annotations on a bundle (no edits to the bundle itself)."""

    __tablename__ = "report_annotations"

    annotation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    report_version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ReportResolutionRow(Base):
    """Accept/downgrade/dismiss resolutions against specific targets."""

    __tablename__ = "report_resolutions"

    resolution_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    report_version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ResearchTaskRow(Base):
    """The only mutable side-table.  Lifecycle: pending → running →
    completed/failed."""

    __tablename__ = "research_tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    target_artifact_version: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    focus_hypothesis_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="anonymous")
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "target_artifact_version": self.target_artifact_version,
            "trigger_kind": self.trigger_kind,
            "focus_hypothesis_id": self.focus_hypothesis_id,
            "query": self.query,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "actor_kind": self.actor_kind,
            "actor_id": self.actor_id,
            "idempotency_key": self.idempotency_key,
        }


class MutationReceiptRow(Base):
    """Idempotency receipts.  Replay returns the original receipt."""

    __tablename__ = "report_mutation_receipts"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "mutation_kind",
            "idempotency_key",
            name="uq_report_mutation_receipts_run_kind_key",
        ),
    )

    receipt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    mutation_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_report_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    produced_report_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    research_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


__all__ = [
    "ReportBundleV2Row",
    "ResearchArtifactSnapshotRow",
    "ReportAnnotationRow",
    "ReportResolutionRow",
    "ResearchTaskRow",
    "MutationReceiptRow",
]
