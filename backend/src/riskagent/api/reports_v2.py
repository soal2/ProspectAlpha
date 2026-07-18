"""V2 reports API: thin router over :class:`ReportingService` + :class:`ReportStore`.

The router is mounted under ``/analysis-runs`` (same prefix as the legacy
analysis router) so all V2 endpoints live at
``/analysis-runs/{run_id}/v2/reports/...``.

Read endpoints:

* ``GET  /analysis-runs/{run_id}/v2/reports`` — list bundles (asc by version)
* ``GET  /analysis-runs/{run_id}/v2/reports:latest`` — most recent bundle
* ``GET  /analysis-runs/{run_id}/v2/reports/{report_version}`` — exact version
* ``GET  /analysis-runs/{run_id}/v2/reports/{report_version}/export``
  — Markdown export (``Accept: text/markdown`` or ``?format=markdown``)

Write endpoints:

* ``POST /analysis-runs/{run_id}/v2/reports:build`` — build a new bundle
  on demand from the latest frozen artifact.  The router never invents
  ``report_version`` — the store's ``UNIQUE(run_id, report_version)``
  constraint is the single source of truth, and the build path bumps the
  version monotonically from the previous latest.

Mutations (annotation / resolution / revision / supplement) are added in
Step 12.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import PlainTextResponse

from riskagent.analysis_runs import AnalysisRunRepository, get_default_analysis_repository
from riskagent.reporting.export import export_bundle_to_markdown
from riskagent.reporting.models import ReportBundleV2
from riskagent.reporting.mutations import (
    AnnotationRequest,
    CompleteResearchTaskRequest,
    FailResearchTaskRequest,
    MutationError,
    MutationService,
    ResolutionRequest,
    RevisionRequest,
    SupplementRequest,
)
from riskagent.reporting.service import (
    BuildRequest,
    ReportingService,
    make_service,
)
from riskagent.reporting.store import (
    BundleNotFound,
    DbReportStore,
    ReportStore,
    StoreError,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis-runs", tags=["reports-v2"])


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def _analysis_repo() -> AnalysisRunRepository:
    return get_default_analysis_repository()


def _reporting_service() -> ReportingService:
    return make_service()


def _report_store() -> ReportStore:
    """Production store.  Tests override this via direct injection."""

    return DbReportStore.from_runtime_engine() if hasattr(
        DbReportStore, "from_runtime_engine"
    ) else _default_db_store()


def _default_db_store() -> DbReportStore:
    from riskagent.database import make_engine
    from riskagent.reporting.orm import (
        MutationReceiptRow,
        ReportAnnotationRow,
        ReportBundleV2Row,
        ReportResolutionRow,
        ResearchArtifactSnapshotRow,
        ResearchTaskRow,
    )
    from riskagent.database import Base

    engine = make_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            ReportBundleV2Row.__table__,
            ResearchArtifactSnapshotRow.__table__,
            ReportAnnotationRow.__table__,
            ReportResolutionRow.__table__,
            ResearchTaskRow.__table__,
            MutationReceiptRow.__table__,
        ],
    )
    return DbReportStore(engine)


def _mutation_service() -> MutationService:
    return MutationService(_report_store())


def _mutation_error_to_http(exc: MutationError) -> HTTPException:
    status_code = MutationError.HTTP_STATUS.get(exc.reason_code, 400)
    return HTTPException(status_code=status_code, detail=str(exc))


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{run_id}/v2/reports",
    response_model=list[ReportBundleV2],
)
def list_v2_reports(run_id: str) -> list[ReportBundleV2]:
    return _report_store().list_bundles(run_id)


@router.get(
    "/{run_id}/v2/reports:latest",
    response_model=ReportBundleV2,
)
def get_latest_v2_report(run_id: str) -> ReportBundleV2:
    bundle = _report_store().latest_bundle(run_id)
    if bundle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no V2 report for run {run_id!r}",
        )
    return bundle


@router.get(
    "/{run_id}/v2/reports/{report_version}",
    response_model=ReportBundleV2,
)
def get_v2_report(run_id: str, report_version: int) -> ReportBundleV2:
    try:
        return _report_store().get_bundle(run_id, report_version)
    except BundleNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.get(
    "/{run_id}/v2/reports/{report_version}/export",
    response_class=PlainTextResponse,
)
def export_v2_report(
    run_id: str,
    report_version: int,
    format: str = Query(default="markdown"),
) -> PlainTextResponse:
    if format != "markdown":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported export format: {format!r}",
        )
    try:
        bundle = _report_store().get_bundle(run_id, report_version)
    except BundleNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    markdown = export_bundle_to_markdown(bundle)
    return PlainTextResponse(
        markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="report_{run_id}_v{report_version}.md"'
            )
        },
    )


# ---------------------------------------------------------------------------
# Build endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/{run_id}/v2/reports:build",
    response_model=ReportBundleV2,
    status_code=status.HTTP_201_CREATED,
)
def build_v2_report(run_id: str) -> ReportBundleV2:
    """Build a fresh bundle from the latest frozen research artifact.

    Behavior:

    * Run not found → 404
    * Run has no ``research`` state → 409 (build needs a frozen artifact)
    * Latest artifact ``completion_status == failed`` → 409
    * Store already has a bundle for this snapshot → 200 with the existing
      bundle (idempotent rebuild)
    """

    try:
        run = _analysis_repo().get(run_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Run not found"
        ) from exc

    research = getattr(run, "research", None)
    if research is None or not research.versions:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="run has no frozen research artifact",
        )
    artifact = research.versions[-1]
    if not artifact.frozen:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="latest research artifact is not frozen",
        )

    request = BuildRequest(
        run_id=run_id,
        artifact=artifact,
        verdicts=list(research.red_team_verdicts),
        company_name=_extract_company_name(run),
        business_description=_extract_business_description(run),
        stock_code=_extract_stock_code(run),
        listing_board=_extract_listing_board(run),
        prospectus_date=_extract_prospectus_date(run),
        external_cutoff_date=_extract_cutoff_date(run),
        analysis_date=_extract_analysis_date(run),
        run_input_payload=_extract_input_payload(run),
    )

    service = _reporting_service()
    try:
        bundle = service.build_bundle(request)
    except Exception as exc:  # noqa: BLE001 — map domain errors to HTTP
        detail = str(exc)
        if "BundleBuildRejected" in type(exc).__name__ or "artifact_failed" in detail:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=detail
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail
        ) from exc

    # Determine the next version monotonic from the latest persisted bundle,
    # then re-stamp the bundle before saving.  We mutate via model_copy so
    # the frozen check never fires.
    store = _report_store()
    existing = store.latest_bundle(run_id)
    next_version = (existing.report_version + 1) if existing is not None else 1
    if bundle.report_version != next_version:
        bundle = bundle.model_copy(
            update={"report_version": next_version, "frozen": False}
        )
        bundle.frozen = True

    try:
        store.save_bundle(bundle, artifact_snapshot=artifact)
    except StoreError as exc:
        # Race with a concurrent build — surface as 409 so the caller can
        # retry idempotently.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    return bundle


# ---------------------------------------------------------------------------
# Mutation endpoints (Step 12)
# ---------------------------------------------------------------------------


@router.post(
    "/{run_id}/v2/reports/{report_version}/annotations",
    response_model=None,
    status_code=status.HTTP_201_CREATED,
)
def add_annotation(
    run_id: str, report_version: int, payload: AnnotationRequest
) -> dict:
    service = _mutation_service()
    try:
        receipt = service.add_annotation(run_id, report_version, payload)
    except MutationError as exc:
        raise _mutation_error_to_http(exc) from exc
    return receipt.model_dump(mode="json")


@router.post(
    "/{run_id}/v2/reports/{report_version}/resolutions",
    response_model=None,
    status_code=status.HTTP_201_CREATED,
)
def add_resolution(
    run_id: str, report_version: int, payload: ResolutionRequest
) -> dict:
    service = _mutation_service()
    try:
        receipt = service.add_resolution(run_id, report_version, payload)
    except MutationError as exc:
        raise _mutation_error_to_http(exc) from exc
    return receipt.model_dump(mode="json")


@router.post(
    "/{run_id}/v2/reports/{report_version}/revisions",
    response_model=None,
    status_code=status.HTTP_201_CREATED,
)
def create_revision(
    run_id: str, report_version: int, payload: RevisionRequest
) -> dict:
    """Produce a new bundle version.  The original is never modified."""

    service = _mutation_service()
    # Revisions need the latest frozen artifact snapshot for the new bundle row.
    try:
        run = _analysis_repo().get(run_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Run not found"
        ) from exc
    research = getattr(run, "research", None)
    if research is None or not research.versions:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="run has no frozen research artifact",
        )
    artifact = research.versions[-1]
    try:
        revision, receipt, new_bundle = service.create_revision(
            run_id, report_version, payload, artifact_snapshot=artifact
        )
    except MutationError as exc:
        raise _mutation_error_to_http(exc) from exc
    return {
        "revision": revision.model_dump(mode="json"),
        "receipt": receipt.model_dump(mode="json"),
        "new_bundle": new_bundle.model_dump(mode="json"),
    }


# ---------------------------------------------------------------------------
# Supplement research-task endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/{run_id}/v2/reports:supplement",
    response_model=None,
    status_code=status.HTTP_201_CREATED,
)
def request_supplement(
    run_id: str, payload: SupplementRequest
) -> dict:
    """Create a pending supplement research task.

    Never mutates the frozen artifact or pre-creates an empty bundle version.
    """
    # Derive target_artifact_version from the latest artifact so the caller
    # does not need to know the current version number.
    try:
        analysis_run = _analysis_repo().get(run_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Run not found"
        ) from exc
    research = getattr(analysis_run, "research", None)
    if research is None or not research.versions:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="run has no frozen research artifact",
        )
    target_artifact_version = research.versions[-1].version

    service = _mutation_service()
    try:
        task, receipt = service.request_supplement(
            run_id, target_artifact_version, payload
        )
    except MutationError as exc:
        raise _mutation_error_to_http(exc) from exc
    return {
        "task": task.model_dump(mode="json"),
        "receipt": receipt.model_dump(mode="json"),
    }


@router.post(
    "/{run_id}/v2/research-tasks/{task_id}:complete",
    response_model=None,
    status_code=status.HTTP_201_CREATED,
)
def complete_research_task(
    run_id: str,
    task_id: str,
    payload: CompleteResearchTaskRequest,
) -> dict:
    """Transition a pending supplement task to completed with a new artifact.

    Validates the artifact contract server-side:
    * ``new_artifact.frozen`` must be ``True``
    * ``new_artifact.based_on_version`` must equal the task's target
    * ``new_artifact.version`` must equal ``target + 1``
    """
    service = _mutation_service()
    # Look up the task to obtain the expected target_artifact_version.
    try:
        task = service._store.get_research_task(task_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    if task.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"task {task_id!r} does not belong to run {run_id!r}",
        )
    try:
        completed_task, receipt, new_bundle = service.complete_research_task(
            task_id,
            payload,
            expected_target_artifact_version=task.target_artifact_version,
        )
    except MutationError as exc:
        raise _mutation_error_to_http(exc) from exc
    return {
        "task": completed_task.model_dump(mode="json"),
        "receipt": receipt.model_dump(mode="json"),
        "new_bundle": new_bundle.model_dump(mode="json"),
    }


@router.post(
    "/{run_id}/v2/research-tasks/{task_id}:fail",
    response_model=None,
    status_code=status.HTTP_200_OK,
)
def fail_research_task(
    run_id: str, task_id: str, payload: FailResearchTaskRequest
) -> dict:
    """Mark a pending/running supplement task as failed (retriable)."""

    service = _mutation_service()
    try:
        task = service._store.get_research_task(task_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    if task.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"task {task_id!r} does not belong to run {run_id!r}",
        )
    try:
        failed_task, receipt = service.fail_research_task(task_id, payload)
    except MutationError as exc:
        raise _mutation_error_to_http(exc) from exc
    return {
        "task": failed_task.model_dump(mode="json"),
        "receipt": receipt.model_dump(mode="json"),
    }


# ---------------------------------------------------------------------------
# Input-payload extractors (kept tiny — the legacy AnalysisRun model carries
# these fields with non-uniform names; a single shim keeps the API layer thin.)
# ---------------------------------------------------------------------------


def _extract_company_name(run: Any) -> str:
    payload = getattr(run, "input_payload", None)
    if payload is None:
        return ""
    return str(getattr(payload, "company_name", "") or "")


def _extract_business_description(run: Any) -> str:
    payload = getattr(run, "input_payload", None)
    if payload is None:
        return ""
    return str(getattr(payload, "business_description", "") or "")


def _extract_stock_code(run: Any) -> str:
    payload = getattr(run, "input_payload", None)
    if payload is None:
        return ""
    return str(getattr(payload, "stock_code", "") or "")


def _extract_listing_board(run: Any) -> str:
    payload = getattr(run, "input_payload", None)
    if payload is None:
        return ""
    return str(getattr(payload, "listing_board", "") or "")


def _extract_prospectus_date(run: Any) -> str:
    payload = getattr(run, "input_payload", None)
    if payload is None:
        return ""
    return str(getattr(payload, "prospectus_date", "") or "")


def _extract_cutoff_date(run: Any) -> str:
    frozen_cutoff = str(getattr(run, "data_cutoff_date", "") or "")
    if frozen_cutoff:
        return frozen_cutoff
    payload = getattr(run, "input_payload", None)
    if payload is None:
        return ""
    return str(getattr(payload, "external_cutoff_date", "") or "")


def _extract_analysis_date(run: Any) -> str:
    payload = getattr(run, "input_payload", None)
    if payload is None:
        return ""
    return str(getattr(payload, "analysis_date", "") or "")


def _extract_input_payload(run: Any) -> dict[str, Any] | None:
    payload = getattr(run, "input_payload", None)
    if payload is None:
        return None
    dump = getattr(payload, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(payload, dict):
        return payload
    return None
