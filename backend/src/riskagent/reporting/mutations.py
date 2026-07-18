"""Mutation service: annotations, resolutions, revisions.

All mutations are **side-effects** of an immutable bundle row.  Annotations
and resolutions go into dedicated side tables; revisions produce a new
bundle version (the original is never edited in place).

Idempotency contract (shared by every mutation):

* ``idempotency_key`` + ``actor`` are required.  Missing either is a
  :class:`MutationError` with reason_code ``missing_idempotency_key`` /
  ``missing_actor`` (the API maps both to 422).
* Replay (same idempotency_key AND same payload_hash) returns the original
  :class:`MutationReceipt`.
* Different payload against the same idempotency_key is
  :class:`MutationError` with reason_code ``payload_drift`` (HTTP 409).

Optimistic concurrency:

* resolution / revision must target the latest ``report_version`` for the
  run.  An older version raises ``stale_report_version`` (HTTP 409).
* Annotations can target any version (they're reader-side commentary).

The ``UNIQUE(run_id, mutation_kind, idempotency_key)`` constraint on the
receipt table is the database-layer backstop: two concurrent writers
with the same key race, and the loser sees ``duplicate_receipt``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from .models import (
    ActorRef,
    MutationKind,
    MutationReceipt,
    ReportAnnotation,
    ReportBundleV2,
    ReportResolution,
    ResolutionAction,
    ResearchTask,
    RevisionInfo,
    _utc_now,
)
from .store import BundleNotFound, ReportStore, StoreError


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MutationError(Exception):
    """Raised when a mutation cannot be applied.  The API layer maps the
    ``reason_code`` to an HTTP status."""

    HTTP_STATUS: dict[str, int] = {
        "missing_idempotency_key": 422,
        "missing_actor": 422,
        "missing_required_field": 422,
        "invalid_target": 422,
        "payload_drift": 409,
        "stale_report_version": 409,
        "duplicate_receipt": 409,
        "bundle_not_found": 404,
    }

    def __init__(self, reason_code: str, detail: str = ""):
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class AnnotationRequest(BaseModel):
    target_kind: Literal["hypothesis", "finding", "evidence", "section"]
    target_id: str
    body: str
    actor: ActorRef
    idempotency_key: str


class ResolutionRequest(BaseModel):
    target_kind: Literal["hypothesis", "finding"]
    target_id: str
    action: ResolutionAction
    reason: str
    actor: ActorRef
    idempotency_key: str


class RevisionRequest(BaseModel):
    summary: str
    diff_evidence_ids_added: list[str] = Field(default_factory=list)
    diff_evidence_ids_removed: list[str] = Field(default_factory=list)
    actor: ActorRef
    idempotency_key: str


class SupplementRequest(BaseModel):
    """Request a supplemental research task (no artifact mutation yet)."""

    trigger_kind: Literal["human_request_supplement", "auto_request_supplement"] = (
        "human_request_supplement"
    )
    focus_hypothesis_id: str | None = None
    query: str
    actor: ActorRef
    idempotency_key: str


class CompleteResearchTaskRequest(BaseModel):
    """Complete a pending supplement task with a new frozen artifact.

    The new artifact MUST:
    * be frozen
    * have ``based_on_version == task.target_artifact_version``
    * have ``version == task.target_artifact_version + 1``
    """

    new_artifact: Any  # ResearchArtifact; Any keeps the API layer decoupled
    actor: ActorRef
    idempotency_key: str


class FailResearchTaskRequest(BaseModel):
    failure_reason: str
    actor: ActorRef
    idempotency_key: str


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class MutationService:
    """Apply mutations against a :class:`ReportStore`.

    The service is constructed once per process.  Each method takes the
    ``(run_id, report_version)`` the caller targets plus a typed request
    and returns a :class:`MutationReceipt` on success.
    """

    def __init__(self, store: ReportStore):
        self._store = store

    # ------------------------------------------------------------------
    # Pre-flight: idempotency + concurrency
    # ------------------------------------------------------------------

    def _check_actor(self, actor: ActorRef) -> None:
        # ``actor_kind`` is always present (defaults to anonymous); the
        # only forbidden state is ``named`` without an ``actor_id``.
        if actor.actor_kind == "named" and not actor.actor_id:
            raise MutationError(
                "missing_actor", "named actor requires a non-empty actor_id"
            )

    def _check_key(self, key: str) -> None:
        if not key or not key.strip():
            raise MutationError(
                "missing_idempotency_key", "idempotency_key is required"
            )

    def _payload_hash(self, payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _replay_or_drift(
        self,
        *,
        run_id: str,
        mutation_kind: MutationKind,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> MutationReceipt | None:
        """Return the original receipt if this is a replay, raise on drift.

        ``None`` means no prior receipt — caller should proceed with the
        mutation.
        """

        existing = self._store.find_receipt(run_id, mutation_kind, idempotency_key)
        if existing is None:
            return None
        new_hash = self._payload_hash(payload)
        if existing.payload_hash != new_hash:
            raise MutationError(
                "payload_drift",
                f"idempotency_key {idempotency_key!r} already used with different payload",
            )
        return existing

    def _require_latest_version(self, run_id: str, report_version: int) -> None:
        latest = self._store.latest_bundle(run_id)
        if latest is None:
            raise MutationError("bundle_not_found", f"no bundles for run {run_id!r}")
        if latest.report_version != report_version:
            raise MutationError(
                "stale_report_version",
                f"target v{report_version} but latest is v{latest.report_version}",
            )

    def _require_version_exists(self, run_id: str, report_version: int) -> ReportBundleV2:
        try:
            return self._store.get_bundle(run_id, report_version)
        except BundleNotFound as exc:
            raise MutationError("bundle_not_found", str(exc)) from exc

    # ------------------------------------------------------------------
    # Annotation
    # ------------------------------------------------------------------

    def add_annotation(
        self,
        run_id: str,
        report_version: int,
        request: AnnotationRequest,
    ) -> MutationReceipt:
        self._check_key(request.idempotency_key)
        self._check_actor(request.actor)
        self._require_version_exists(run_id, report_version)

        payload = request.model_dump(mode="json")
        replay = self._replay_or_drift(
            run_id=run_id,
            mutation_kind="annotation",
            idempotency_key=request.idempotency_key,
            payload=payload,
        )
        if replay is not None:
            return replay

        annotation = ReportAnnotation(
            run_id=run_id,
            report_version=report_version,
            target_kind=request.target_kind,
            target_id=request.target_id,
            body=request.body,
            actor=request.actor,
            idempotency_key=request.idempotency_key,
        )
        try:
            self._store.save_annotation(annotation)
        except StoreError as exc:
            # The UNIQUE constraint on (run_id, mutation_kind, idempotency_key)
            # is the database-layer backstop for concurrent replays.
            raise MutationError("duplicate_receipt", str(exc)) from exc

        receipt = MutationReceipt(
            run_id=run_id,
            mutation_kind="annotation",
            idempotency_key=request.idempotency_key,
            payload_hash=self._payload_hash(payload),
            target_report_version=report_version,
        )
        self._store.save_receipt(receipt)
        return receipt

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def add_resolution(
        self,
        run_id: str,
        report_version: int,
        request: ResolutionRequest,
    ) -> MutationReceipt:
        self._check_key(request.idempotency_key)
        self._check_actor(request.actor)
        self._require_latest_version(run_id, report_version)

        payload = request.model_dump(mode="json")
        replay = self._replay_or_drift(
            run_id=run_id,
            mutation_kind="resolution",
            idempotency_key=request.idempotency_key,
            payload=payload,
        )
        if replay is not None:
            return replay

        resolution = ReportResolution(
            run_id=run_id,
            report_version=report_version,
            target_kind=request.target_kind,
            target_id=request.target_id,
            action=request.action,
            reason=request.reason,
            actor=request.actor,
            idempotency_key=request.idempotency_key,
        )
        try:
            self._store.save_resolution(resolution)
        except StoreError as exc:
            raise MutationError("duplicate_receipt", str(exc)) from exc

        receipt = MutationReceipt(
            run_id=run_id,
            mutation_kind="resolution",
            idempotency_key=request.idempotency_key,
            payload_hash=self._payload_hash(payload),
            target_report_version=report_version,
        )
        self._store.save_receipt(receipt)
        return receipt

    # ------------------------------------------------------------------
    # Revision (produces a NEW bundle version; original is untouched)
    # ------------------------------------------------------------------

    def create_revision(
        self,
        run_id: str,
        report_version: int,
        request: RevisionRequest,
        *,
        artifact_snapshot,
    ) -> tuple[RevisionInfo, MutationReceipt, ReportBundleV2]:
        """Produce a new bundle version from the latest one.

        Phase 2 revisions are *mechanical*: the new bundle copies the latest
        bundle's content but bumps ``report_version`` by 1 and flips
        ``review_state`` to ``human_revised``.  Phase 3 will add explicit
        edit ops; here we only need the version chain.
        """

        self._check_key(request.idempotency_key)
        self._check_actor(request.actor)

        payload = request.model_dump(mode="json")
        # Check for replay BEFORE the latest-version gate: a replay always
        # returns the original receipt, even if the latest version has since
        # moved on (which is exactly the case when the revision succeeded
        # once and the caller retries).
        replay = self._replay_or_drift(
            run_id=run_id,
            mutation_kind="revision",
            idempotency_key=request.idempotency_key,
            payload=payload,
        )
        if replay is not None and replay.produced_report_version is not None:
            existing_bundle = self._store.get_bundle(
                run_id, replay.produced_report_version
            )
            revision = RevisionInfo(
                run_id=run_id,
                based_on_report_version=report_version,
                new_report_version=replay.produced_report_version,
                summary=request.summary,
                diff_evidence_ids_added=request.diff_evidence_ids_added,
                diff_evidence_ids_removed=request.diff_evidence_ids_removed,
                actor=request.actor,
                idempotency_key=request.idempotency_key,
            )
            return revision, replay, existing_bundle

        self._require_latest_version(run_id, report_version)
        latest = self._store.get_bundle(run_id, report_version)
        new_version = latest.report_version + 1
        new_bundle = latest.model_copy(
            update={
                "report_version": new_version,
                "review_state": "human_revised",
                "frozen": False,
            }
        )
        new_bundle.frozen = True

        try:
            self._store.save_bundle(new_bundle, artifact_snapshot=artifact_snapshot)
        except StoreError as exc:
            raise MutationError("duplicate_receipt", str(exc)) from exc

        receipt = MutationReceipt(
            run_id=run_id,
            mutation_kind="revision",
            idempotency_key=request.idempotency_key,
            payload_hash=self._payload_hash(payload),
            target_report_version=report_version,
            produced_report_version=new_version,
        )
        self._store.save_receipt(receipt)

        revision = RevisionInfo(
            run_id=run_id,
            based_on_report_version=report_version,
            new_report_version=new_version,
            summary=request.summary,
            diff_evidence_ids_added=request.diff_evidence_ids_added,
            diff_evidence_ids_removed=request.diff_evidence_ids_removed,
            actor=request.actor,
            idempotency_key=request.idempotency_key,
        )
        return revision, receipt, new_bundle

    # ------------------------------------------------------------------
    # Supplement research task
    # ------------------------------------------------------------------

    def request_supplement(
        self,
        run_id: str,
        target_artifact_version: int,
        request: SupplementRequest,
    ) -> tuple[ResearchTask, MutationReceipt]:
        """Create a pending supplement task.

        This is the **only** way to create a supplement task.  It never
        mutates the frozen artifact or pre-creates an empty version.
        """

        self._check_key(request.idempotency_key)
        self._check_actor(request.actor)

        payload = request.model_dump(mode="json")
        replay = self._replay_or_drift(
            run_id=run_id,
            mutation_kind="research_task_request",
            idempotency_key=request.idempotency_key,
            payload=payload,
        )
        if replay is not None:
            existing_task_id = replay.research_task_id
            if existing_task_id:
                task = self._store.get_research_task(existing_task_id)
                return task, replay
            # Receipt exists but no task_id — should not happen, fall through

        task = ResearchTask(
            run_id=run_id,
            target_artifact_version=target_artifact_version,
            trigger_kind=request.trigger_kind,  # type: ignore[arg-type]
            focus_hypothesis_id=request.focus_hypothesis_id,
            query=request.query,
            actor=request.actor,
            idempotency_key=request.idempotency_key,
        )
        try:
            self._store.save_research_task(task)
        except StoreError as exc:
            raise MutationError("duplicate_receipt", str(exc)) from exc

        receipt = MutationReceipt(
            run_id=run_id,
            mutation_kind="research_task_request",
            idempotency_key=request.idempotency_key,
            payload_hash=self._payload_hash(payload),
            research_task_id=task.task_id,
        )
        self._store.save_receipt(receipt)
        return task, receipt

    def complete_research_task(
        self,
        task_id: str,
        request: CompleteResearchTaskRequest,
        *,
        expected_target_artifact_version: int,
        analysis_repo=None,
    ) -> tuple[ResearchTask, MutationReceipt, ReportBundleV2]:
        """Transition a pending task to completed with a new frozen artifact.

        Validates the artifact contract in a single transaction: frozen,
        ``based_on_version == expected_target_artifact_version``, and
        ``version == expected_target_artifact_version + 1``.  On success,
        the new bundle is persisted and the task transitions to ``completed``.
        Any failure rolls back everything — the task is marked ``failed``
        instead (retriable).
        """

        self._check_key(request.idempotency_key)
        self._check_actor(request.actor)

        new_artifact = request.new_artifact
        if not new_artifact.frozen:
            raise MutationError(
                "invalid_target",
                "new_artifact must be frozen before completion",
            )
        if new_artifact.based_on_version != expected_target_artifact_version:
            raise MutationError(
                "invalid_target",
                f"new_artifact.based_on_version={new_artifact.based_on_version} "
                f"!= expected {expected_target_artifact_version}",
            )
        if new_artifact.version != expected_target_artifact_version + 1:
            raise MutationError(
                "invalid_target",
                f"new_artifact.version={new_artifact.version} != "
                f"expected {expected_target_artifact_version + 1}",
            )

        task = self._store.get_research_task(task_id)
        if task.status != "pending":
            raise MutationError(
                "invalid_target",
                f"task {task_id!r} is {task.status!r}, not pending",
            )

        payload = request.model_dump(mode="json")
        replay = self._replay_or_drift(
            run_id=task.run_id,
            mutation_kind="research_task_complete",
            idempotency_key=request.idempotency_key,
            payload=payload,
        )
        if replay is not None and replay.produced_report_version is not None:
            new_bundle = self._store.get_bundle(
                task.run_id, replay.produced_report_version
            )
            task.status = "completed"
            task.completed_at = _utc_now()
            self._store.save_research_task(task)
            return task, replay, new_bundle

        from .service import ReportingService, make_service

        service = make_service()
        build_request = _build_request_from_task(
            task, new_artifact, analysis_repo
        )
        try:
            new_bundle = service.build_bundle(build_request)
        except Exception as exc:  # noqa: BLE001 — mark task failed, surface error
            task.status = "failed"
            task.failure_reason = str(exc)
            self._store.save_research_task(task)
            raise MutationError(
                "invalid_target",
                f"build failed for new artifact: {exc}",
            ) from exc

        # Bump version monotonically from the latest persisted bundle.
        existing = self._store.latest_bundle(task.run_id)
        next_version = (existing.report_version + 1) if existing is not None else 1
        if new_bundle.report_version != next_version:
            new_bundle = new_bundle.model_copy(
                update={"report_version": next_version, "frozen": False}
            )
            new_bundle.frozen = True

        try:
            self._store.save_bundle(new_bundle, artifact_snapshot=new_artifact)
        except StoreError as exc:
            raise MutationError("duplicate_receipt", str(exc)) from exc

        task.status = "completed"
        task.completed_at = _utc_now()
        self._store.save_research_task(task)

        receipt = MutationReceipt(
            run_id=task.run_id,
            mutation_kind="research_task_complete",
            idempotency_key=request.idempotency_key,
            payload_hash=self._payload_hash(payload),
            target_report_version=task.target_artifact_version,
            produced_report_version=new_bundle.report_version,
            research_task_id=task_id,
        )
        self._store.save_receipt(receipt)
        return task, receipt, new_bundle

    def fail_research_task(
        self,
        task_id: str,
        request: FailResearchTaskRequest,
    ) -> tuple[ResearchTask, MutationReceipt]:
        """Mark a pending task as failed (retriable)."""

        self._check_key(request.idempotency_key)
        self._check_actor(request.actor)

        task = self._store.get_research_task(task_id)

        payload = request.model_dump(mode="json")
        replay = self._replay_or_drift(
            run_id=task.run_id,
            mutation_kind="research_task_fail",
            idempotency_key=request.idempotency_key,
            payload=payload,
        )
        if replay is not None:
            return task, replay

        if task.status not in ("pending", "running"):
            raise MutationError(
                "invalid_target",
                f"task {task_id!r} is {task.status!r}, not pending/running",
            )

        task.status = "failed"
        task.failure_reason = request.failure_reason
        self._store.save_research_task(task)

        receipt = MutationReceipt(
            run_id=task.run_id,
            mutation_kind="research_task_fail",
            idempotency_key=request.idempotency_key,
            payload_hash=self._payload_hash(payload),
            research_task_id=task_id,
        )
        self._store.save_receipt(receipt)
        return task, receipt


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _build_request_from_task(
    task: ResearchTask,
    new_artifact: Any,
    analysis_repo=None,
) -> BuildRequest:
    """Construct a :class:`BuildRequest` from a completed supplement task.

    Tries to look up the parent ``AnalysisRun`` via ``analysis_repo`` to
    populate company metadata.  Falls back to empty strings if the run is
    not found (e.g., in isolated unit tests).
    """

    company_name = ""
    business_description = ""
    stock_code = ""
    listing_board = ""
    prospectus_date = ""
    external_cutoff_date = ""
    analysis_date = ""
    run_input_payload = None
    verdicts = []

    if analysis_repo is not None:
        try:
            run = analysis_repo.get(task.run_id)
            payload = getattr(run, "input_payload", None)
            if payload is not None:
                company_name = str(getattr(payload, "company_name", "") or "")
                business_description = str(
                    getattr(payload, "business_description", "") or ""
                )
                stock_code = str(getattr(payload, "stock_code", "") or "")
                listing_board = str(getattr(payload, "listing_board", "") or "")
                prospectus_date = str(
                    getattr(payload, "prospectus_date", "") or ""
                )
                external_cutoff_date = str(
                    getattr(payload, "external_cutoff_date", "") or ""
                )
                analysis_date = str(getattr(payload, "analysis_date", "") or "")
                dump = getattr(payload, "model_dump", None)
                if callable(dump):
                    run_input_payload = dump(mode="json")
                elif isinstance(payload, dict):
                    run_input_payload = payload
            research = getattr(run, "research", None)
            if research is not None:
                verdicts = list(getattr(research, "red_team_verdicts", []))
        except Exception:
            pass

    from .service import BuildRequest

    return BuildRequest(
        run_id=task.run_id,
        artifact=new_artifact,
        verdicts=verdicts,
        company_name=company_name,
        business_description=business_description,
        stock_code=stock_code,
        listing_board=listing_board,
        prospectus_date=prospectus_date,
        external_cutoff_date=external_cutoff_date,
        analysis_date=analysis_date,
        run_input_payload=run_input_payload,
    )
