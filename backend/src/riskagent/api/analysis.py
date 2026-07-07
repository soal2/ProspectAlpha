"""Analysis run routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from riskagent.analysis_runs import (
    AnalysisRun,
    AnalysisRunCreate,
    AnalysisRunRepository,
    get_default_analysis_repository,
)


router = APIRouter(prefix="/analysis-runs", tags=["analysis-runs"])


def _repo() -> AnalysisRunRepository:
    return get_default_analysis_repository()


@router.post("", response_model=AnalysisRun, status_code=status.HTTP_201_CREATED)
def create_analysis_run(payload: AnalysisRunCreate) -> AnalysisRun:
    return _repo().create_and_run(payload)


@router.get("", response_model=list[AnalysisRun])
def list_analysis_runs() -> list[AnalysisRun]:
    return _repo().list_runs()


@router.get("/{run_id}", response_model=AnalysisRun)
def get_analysis_run(run_id: str) -> AnalysisRun:
    try:
        return _repo().get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc


@router.get("/{run_id}/findings")
def get_analysis_findings(run_id: str) -> dict[str, object]:
    try:
        run = _repo().get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
    return {"run_id": run.run_id, "findings": run.findings}
