from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from riskagent.analysis_runs import AnalysisRunCreate
from riskagent.api.main import create_app
from riskagent.risk_rules import RiskRuleRepository
from riskagent.runtime_events import EventBroker


def test_event_broker_delivers_to_subscriber():
    async def run():
        broker = EventBroker()
        queue = await broker.subscribe("run_1")
        event = {"event_type": "stage_started"}

        await broker.publish("run_1", event)

        assert await asyncio.wait_for(queue.get(), timeout=1) == event

    asyncio.run(run())


def test_event_broker_fans_out_to_all_subscribers():
    async def run():
        broker = EventBroker()
        first = await broker.subscribe("run_1")
        second = await broker.subscribe("run_1")
        event = {"event_type": "stage_started"}

        await broker.publish("run_1", event)

        assert await asyncio.wait_for(first.get(), timeout=1) == event
        assert await asyncio.wait_for(second.get(), timeout=1) == event

    asyncio.run(run())


def test_event_broker_unsubscribe_stops_delivery():
    async def run():
        broker = EventBroker()
        queue = await broker.subscribe("run_1")

        await broker.unsubscribe("run_1", queue)
        await broker.publish("run_1", {"event_type": "stage_started"})

        assert queue.empty()

    asyncio.run(run())


def test_event_broker_publish_sync_from_fresh_thread_delivers_event():
    async def setup():
        broker = EventBroker()
        queue = await broker.subscribe("run_1")
        return broker, queue

    broker, queue = asyncio.run(setup())
    event = {"event_type": "stage_started"}

    thread = threading.Thread(target=lambda: broker.publish_sync("run_1", event))
    thread.start()
    thread.join()

    assert queue.get_nowait() == event


def test_repository_save_dual_writes_new_events_to_broker(
    tmp_path, monkeypatch, analysis_repo_factory
):
    import riskagent.runtime_events as runtime_events

    broker = EventBroker()
    monkeypatch.setattr(runtime_events, "_EVENT_BROKER", broker)
    rule_repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    repo = analysis_repo_factory(
        rule_repository=rule_repo,
        path=tmp_path / "runs.yaml",
    )
    run = repo.create_draft(
        AnalysisRunCreate(company_name="Streaming IPO", document_text="招股书正文")
    )
    queue = asyncio.run(broker.subscribe(run.run_id))
    run = repo.get(run.run_id)

    run = repo._transition(
        run,
        "pdf_parsing",
        "stage_started",
        "PDF 解析器开始解析招股书材料",
        "解析文本、页码、章节、bbox，并为后续 Agent 建立可回溯材料底座。",
    )
    repo._save_run(run)

    event = queue.get_nowait()
    assert event["event_type"] == "stage_started"
    assert event["stage_key"] == "pdf_parsing"
    assert event["stage_status"] == "running"


def test_stream_completed_run_replays_events_emits_done_and_closes(
    tmp_path, monkeypatch, analysis_repo_factory
):
    rule_repo = RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    repo = analysis_repo_factory(
        rule_repository=rule_repo,
        path=tmp_path / "runs.yaml",
    )
    run = repo.create_draft(
        AnalysisRunCreate(company_name="Streaming IPO", document_text="公司存在关联交易。")
    )
    run.status = "completed"
    run.current_stage = "completed"
    repo._save_run(run)
    monkeypatch.setattr("riskagent.api.analysis._repo", lambda: repo)
    client = TestClient(create_app())

    with client.stream("GET", f"/analysis-runs/{run.run_id}/stream") as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    messages = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    assert any(message.get("event_type") == "stage_started" for message in messages)
    assert messages[-1]["type"] == "done"
    assert messages[-1]["run"]["status"] == "completed"
