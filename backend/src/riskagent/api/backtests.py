"""Offline IPO risk backtest endpoints."""

import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from riskagent.backtesting.service import (
    BacktestCreate,
    BacktestCandidatePreview,
    BacktestJob,
    CandidateConfirmation,
    BacktestSummary,
    MarketImportRequest,
    MarketSnapshotOption,
    service,
)

router = APIRouter(prefix="/backtests", tags=["backtests"])


@router.post("/market-data/import")
def import_market_data(payload: MarketImportRequest) -> dict:
    try:
        return service.import_market_data(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/market-data/upload")
async def upload_market_data(
    security: UploadFile = File(...), company: UploadFile = File(...), prices: UploadFile = File(...)
) -> dict:
    upload_dir = Path(__file__).resolve().parents[3] / "data" / "market_uploads" / uuid.uuid4().hex
    upload_dir.mkdir(parents=True, exist_ok=False)
    paths = {}
    for key, upload in {"security": security, "company": company, "prices": prices}.items():
        if not upload.filename or not upload.filename.lower().endswith(".csv"):
            raise HTTPException(status_code=400, detail=f"{key} 必须是 CSV 文件")
        path = upload_dir / f"{key}.csv"
        path.write_bytes(await upload.read())
        paths[key] = str(path)
    try:
        return service.import_market_data(MarketImportRequest(security_csv=paths["security"], company_csv=paths["company"], prices_csv=paths["prices"]))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/market-data/snapshots", response_model=list[MarketSnapshotOption])
def list_market_snapshots() -> list[MarketSnapshotOption]:
    return service.list_market_snapshots()


@router.get("/market-data/{snapshot_id}/candidates", response_model=list[BacktestCandidatePreview])
def preview_market_candidates(snapshot_id: str) -> list[BacktestCandidatePreview]:
    try:
        return service.preview_candidates(snapshot_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("", response_model=BacktestSummary)
def create_backtest(payload: BacktestCreate, background_tasks: BackgroundTasks) -> BacktestSummary:
    try:
        result = service.create_backtest(payload)
        if result.jobId:
            background_tasks.add_task(service.run_job, result.jobId)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=list[BacktestSummary])
def list_backtests() -> list[BacktestSummary]:
    return service.list_backtests()


@router.get("/{backtest_id}")
def get_backtest(backtest_id: str) -> dict:
    try:
        return service.detail(backtest_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Backtest not found") from exc


@router.get("/jobs/{job_id}", response_model=BacktestJob)
def get_backtest_job(job_id: str) -> BacktestJob:
    try:
        return service.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Backtest job not found") from exc


@router.post("/{backtest_id}/candidates", response_model=BacktestSummary)
def confirm_candidates(backtest_id: str, payload: CandidateConfirmation) -> BacktestSummary:
    try:
        return service.confirm_candidates(backtest_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{backtest_id}/start", response_model=BacktestJob)
def start_backtest(backtest_id: str, background_tasks: BackgroundTasks) -> BacktestJob:
    try:
        job = service.start_backtest(backtest_id)
        background_tasks.add_task(service.run_job, job.id)
        return job
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{backtest_id}/reproduce", response_model=BacktestJob)
def reproduce_backtest(backtest_id: str, background_tasks: BackgroundTasks) -> BacktestJob:
    try:
        job = service.reproduce(backtest_id)
        background_tasks.add_task(service.run_job, job.id)
        return job
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/market-data/{snapshot_id}/publish", response_model=MarketSnapshotOption)
def publish_market_snapshot(snapshot_id: str) -> MarketSnapshotOption:
    try:
        return service.publish_market_snapshot(snapshot_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Market snapshot not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{backtest_id}/artifact")
def download_backtest_artifact(backtest_id: str, download: bool = False):
    try:
        detail = service.detail(backtest_id)
        path = detail.get("artifact")
        if not path:
            raise ValueError("审计报告尚未生成")
        return FileResponse(
            path,
            media_type="text/html; charset=utf-8",
            filename=f"{backtest_id}-audit.html",
            content_disposition_type="attachment" if download else "inline",
            headers={
                "X-Content-Type-Options": "nosniff",
            },
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Backtest not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
