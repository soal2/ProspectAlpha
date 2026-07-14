"""Analysis run routes."""

from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from riskagent.analysis_runs import (
    AnalysisRun,
    AnalysisRunCreate,
    AnalysisRunInputPatch,
    AnalysisTrace,
    AnalysisRunRepository,
    ExpertSelectionRequest,
    ReportDraft,
    RunEvidence,
    _build_report_streaming,
    get_default_analysis_repository,
)
from riskagent.runtime_events import get_event_broker


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


@router.get("/{run_id}/stream")
async def stream_analysis_run(run_id: str) -> StreamingResponse:
    try:
        run = _repo().get(run_id)
        if _should_resume(run):
            _repo().resume(run_id, dispatch_mode=_api_dispatch_mode())
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc

    async def event_source():
        broker = get_event_broker()
        queue = await broker.subscribe(run_id)
        seen_events: set[tuple[object, object, object, object]] = set()
        try:
            for event in _repo().events(run_id, 0):
                seen_events.add(_event_signature(event))
                yield _sse_data(event)
            run = _repo().get(run_id)
            if run.status == "awaiting_expert_selection":
                yield _sse_data({"type": "paused", "run": run.model_dump()})
                return
            if run.status in {"completed", "failed"}:
                yield _sse_data({"type": "done", "run": run.model_dump()})
                return

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                signature = _event_signature(event)
                if signature in seen_events:
                    continue
                seen_events.add(signature)
                yield _sse_data(event)
                if _is_terminal_event(event):
                    final_run = _repo().get(run_id)
                    yield _sse_data({"type": "done", "run": final_run.model_dump()})
                    return
                run = _repo().get(run_id)
                if run.status == "awaiting_expert_selection":
                    yield _sse_data({"type": "paused", "run": run.model_dump()})
                    return
                if run.status in {"completed", "failed"}:
                    yield _sse_data({"type": "done", "run": run.model_dump()})
                    return
        except KeyError:
            yield _sse_data({"type": "error", "message": "Run not found"})
            return
        finally:
            await broker.unsubscribe(run_id, queue)

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
        event.get("created_at"),
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


@router.get("/{run_id}/stream-report")
async def stream_report(run_id: str) -> StreamingResponse:
    try:
        run = _repo().get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    if run.status != "completed":
        raise HTTPException(status_code=409, detail=f"Run status is {run.status}, not completed")

    async def gen():
        try:
            async for chunk in _build_report_streaming(run):
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


@router.get("/{run_id}/trace", response_model=AnalysisTrace)
def get_analysis_trace(run_id: str) -> AnalysisTrace:
    try:
        return _repo().trace(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
