"""Analysis run routes."""

from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse

from riskagent.reporting import ensure_risk_report_html
from riskagent.analysis_runs import (
    AnalysisRun,
    AnalysisRunCreate,
    AnalysisRunInputPatch,
    AnalysisProgress,
    AnalysisTrace,
    AnalysisRunRepository,
    ExpertSelectionRequest,
    IndustryProfileConfirmation,
    ReportDraft,
    RunEvidence,
    get_default_analysis_repository,
)
from riskagent.runtime_events import get_event_broker, subscribe_redis_events
from riskagent.skill_runtime import ResearchArtifact
from riskagent.backtesting.service import service as backtest_service


router = APIRouter(prefix="/analysis-runs", tags=["analysis-runs"])


def _repo() -> AnalysisRunRepository:
    return get_default_analysis_repository()


def _api_dispatch_mode() -> str:
    return os.getenv("ANALYSIS_API_EXECUTION_MODE") or os.getenv(
        "ANALYSIS_EXECUTION_MODE", "thread"
    )


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


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_analysis_run(run_id: str) -> None:
    try:
        _repo().delete_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/{run_id}/progress", response_model=AnalysisProgress)
def get_analysis_progress(run_id: str) -> AnalysisProgress:
    try:
        return _repo().progress(run_id)
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


@router.post("/{run_id}/industry-profile/confirm", response_model=AnalysisRun)
def confirm_industry_profile(
    run_id: str,
    payload: IndustryProfileConfirmation,
) -> AnalysisRun:
    try:
        return _repo().confirm_industry_profile(
            run_id,
            payload,
            dispatch_mode=_api_dispatch_mode(),
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{run_id}/risk-score")
def freeze_risk_score(run_id: str) -> dict:
    """Freeze the offline, explainable score used by IPO backtests only."""
    try:
        run = _repo().get(run_id)
        return backtest_service.score_for_analysis_run(run)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{run_id}/start", response_model=AnalysisRun)
def start_analysis_run(run_id: str) -> AnalysisRun:
    try:
        return _repo().start(run_id, dispatch_mode=_api_dispatch_mode())
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{run_id}/expert-selection", response_model=AnalysisRun)
def submit_expert_selection(run_id: str, payload: ExpertSelectionRequest) -> AnalysisRun:
    try:
        return _repo().select_experts(
            run_id,
            payload.agent_names,
            dispatch_mode=_api_dispatch_mode(),
        )
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


@router.get("/{run_id}/research-artifacts", response_model=list[ResearchArtifact])
def get_research_artifacts(run_id: str) -> list[ResearchArtifact]:
    try:
        return _repo().get(run_id).research_artifacts
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc


@router.get("/{run_id}/skill-snapshot", response_model=dict[str, object])
def get_skill_snapshot(run_id: str) -> dict[str, object]:
    try:
        return _repo().get(run_id).skill_snapshot
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc


@router.get("/{run_id}/stream")
async def stream_analysis_run(run_id: str, after: int = 0) -> StreamingResponse:
    try:
        run = _repo().get(run_id)
        if _should_resume(run):
            _repo().resume(run_id, dispatch_mode=_api_dispatch_mode())
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc

    async def event_source():
        broker = get_event_broker()
        queue = await broker.subscribe(run_id)
        redis_subscription = await subscribe_redis_events(run_id)
        seen_events: set[tuple[object, object, object, object]] = set()
        seen_deltas: set[tuple[object, object]] = set()
        cursor = max(0, after)
        last_durable_sync = 0.0
        try:
            while True:
                loop = asyncio.get_running_loop().time()
                if loop - last_durable_sync >= 0.25:
                    durable_events = _repo().events(run_id, cursor)
                    cursor += len(durable_events)
                    last_durable_sync = loop
                    for event in durable_events:
                        signature = _event_signature(event)
                        if signature in seen_events:
                            continue
                        seen_events.add(signature)
                        yield _sse_data(event)
                run = _repo().get(run_id)
                if run.status in {"awaiting_industry_confirmation", "awaiting_expert_selection"}:
                    yield _sse_data({"type": "paused", "run": run.model_dump()})
                    return
                if run.status in {"completed", "failed"}:
                    if not _repo().events(run_id, cursor):
                        yield _sse_data({"type": "done", "run": run.model_dump()})
                        return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    event = None
                if event:
                    signature = _event_signature(event)
                    if signature not in seen_events:
                        seen_events.add(signature)
                        yield _sse_data(event)
                if redis_subscription:
                    event = await redis_subscription.get()
                    if event:
                        delta_key = (event.get("call_id"), event.get("sequence"))
                        if event.get("event_type") != "llm_response_delta" or delta_key not in seen_deltas:
                            seen_deltas.add(delta_key)
                            yield _sse_data(event)
                if event is None:
                    await asyncio.sleep(0.05)
        except KeyError:
            yield _sse_data({"type": "error", "message": "Run not found"})
            return
        finally:
            await broker.unsubscribe(run_id, queue)
            if redis_subscription:
                await redis_subscription.close()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse_data(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _is_terminal_event(event: dict[str, object]) -> bool:
    event_type = event.get("event_type") or event.get("type")
    if event_type == "run_completed":
        return True
    return (
        event_type == "stage_completed"
        and event.get("stage_key") == "completed"
        and event.get("stage_status") == "completed"
    )


def _event_signature(event: dict[str, object]) -> tuple[object, object, object, object]:
    return (
        event.get("event_id") or event.get("call_id") or event.get("created_at"),
        event.get("event_type") or event.get("type"),
        event.get("actor_name"),
        event.get("title"),
    )


def _should_resume(run: AnalysisRun) -> bool:
    if run.status in {
        "ready",
        "queued",
        "completed",
        "failed",
        "clarifying",
        "awaiting_industry_confirmation",
        "awaiting_expert_selection",
    }:
        return False
    if not run.events:
        return False
    last_event = run.events[-1]
    return (
        last_event.get("event_type") == "stage_completed"
        and last_event.get("stage_key") == run.current_stage
        and last_event.get("stage_status") == "completed"
    )


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


@router.get("/{run_id}/report.html")
def get_analysis_report_html(run_id: str, download: bool = False) -> HTMLResponse:
    """Serve the standalone HTML report. ``/report.html`` is the public URL."""

    return _analysis_report_html_response(run_id, download=download)


@router.get("/{run_id}/report/artifact")
def download_analysis_report_artifact(run_id: str, download: bool = False) -> HTMLResponse:
    return _analysis_report_html_response(run_id, download=download)


def _analysis_report_html_response(run_id: str, *, download: bool) -> HTMLResponse:
    try:
        run = _repo().get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
    if run.status != "completed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Run status is {run.status}, not completed")
    if run.report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report artifact not found")
    html = ensure_risk_report_html(
        company_name=run.company_name,
        run_id=run.run_id,
        report_id=run.report.report_id,
        created_at=run.report.created_at,
        risk_level=run.report.overall_risk_level,
        html=run.report.html,
        summary=run.report.summary,
        markdown=run.report.markdown,
        themes=run.report.themes,
        exploratory_findings=run.report.exploratory_findings,
        counterevidence=run.report.counterevidence,
        limitations=run.report.limitations,
        evidence_index=run.report.evidence_index,
    )
    run.report.html = html
    disposition = "attachment" if download else "inline"
    return HTMLResponse(
        content=html,
        headers={
            "Content-Disposition": f"{disposition}; filename=risk-report-{run_id}.html",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{run_id}/stream-report")
async def stream_report(run_id: str) -> StreamingResponse:
    try:
        run = _repo().get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    if run.status != "completed":
        raise HTTPException(status_code=409, detail=f"Run status is {run.status}, not completed")
    if run.report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    async def gen():
        try:
            for chunk in _report_chunks(run.report.markdown):
                yield f"data: {json.dumps({'type': 'report_chunk', 'delta': chunk}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"
            return
        yield f"data: {json.dumps({'type': 'report_done'})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _report_chunks(markdown: str, *, size: int = 4096):
    """Yield the persisted report verbatim without invoking an LLM again."""

    for start in range(0, len(markdown), size):
        yield markdown[start : start + size]


@router.get("/{run_id}/trace", response_model=AnalysisTrace)
def get_analysis_trace(run_id: str) -> AnalysisTrace:
    try:
        return _repo().trace(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
