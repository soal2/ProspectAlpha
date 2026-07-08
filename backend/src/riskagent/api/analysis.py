"""Analysis run routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from riskagent.analysis_runs import (
    AnalysisRun,
    AnalysisRunCreate,
    AnalysisRunInputPatch,
    AnalysisTrace,
    AnalysisRunRepository,
    ReportDraft,
    RunEvidence,
    get_default_analysis_repository,
)


router = APIRouter(prefix="/analysis-runs", tags=["analysis-runs"])


def _repo() -> AnalysisRunRepository:
    return get_default_analysis_repository()


@router.post("", response_model=AnalysisRun, status_code=status.HTTP_201_CREATED)
def create_analysis_run(payload: AnalysisRunCreate) -> AnalysisRun:
    try:
        return _repo().create_draft(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("", response_model=list[AnalysisRun])
def list_analysis_runs() -> list[AnalysisRun]:
    return _repo().list_runs()


@router.get("/{run_id}", response_model=AnalysisRun)
def get_analysis_run(run_id: str) -> AnalysisRun:
    try:
        return _repo().get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc


@router.patch("/{run_id}/inputs", response_model=AnalysisRun)
def update_analysis_inputs(run_id: str, payload: AnalysisRunInputPatch) -> AnalysisRun:
    try:
        return _repo().update_inputs(run_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{run_id}/start", response_model=AnalysisRun)
def start_analysis_run(run_id: str) -> AnalysisRun:
    try:
        return _repo().start(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/{run_id}/events")
def get_analysis_events(run_id: str, after: int = 0) -> dict[str, object]:
    try:
        return {"run_id": run_id, "events": _repo().events(run_id, after)}
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc


@router.get("/{run_id}/evidence", response_model=list[RunEvidence])
def get_analysis_evidence(run_id: str) -> list[RunEvidence]:
    try:
        return _repo().evidence(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc


@router.get("/{run_id}/findings")
def get_analysis_findings(run_id: str) -> dict[str, object]:
    try:
        run = _repo().get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
    return {"run_id": run.run_id, "findings": run.findings}


@router.get("/{run_id}/report", response_model=ReportDraft)
def get_analysis_report(run_id: str) -> ReportDraft:
    try:
        return _repo().report(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found") from exc


@router.get("/{run_id}/trace", response_model=AnalysisTrace)
def get_analysis_trace(run_id: str) -> AnalysisTrace:
    try:
        return _repo().trace(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
