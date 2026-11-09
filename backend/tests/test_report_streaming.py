from __future__ import annotations

import json

from fastapi.testclient import TestClient

from riskagent.analysis_runs import AnalysisRun, ReportDraft
from riskagent.api import analysis as analysis_api
from riskagent.api.main import create_app


class _ReportRepository:
    def __init__(self, run: AnalysisRun | None):
        self.run = run

    def get(self, run_id: str) -> AnalysisRun:
        if self.run is None or self.run.run_id != run_id:
            raise KeyError(run_id)
        return self.run


def _run(*, status: str = "completed", with_report: bool = True) -> AnalysisRun:
    report = None
    if with_report:
        markdown = "# 港股IPO风险穿透预警报告\n\n" + ("证据表格内容\n" * 900)
        report = ReportDraft(
            report_id="report_fixed",
            markdown=markdown,
            html=markdown.replace("\n", "<br>"),
            created_at="2026-08-11T00:01:00Z",
        )
    return AnalysisRun(
        run_id="run_report",
        company_name="测试科技",
        status=status,
        current_stage=status,
        created_at="2026-08-11T00:00:00Z",
        completed_at="2026-08-11T00:01:00Z" if status == "completed" else None,
        data_cutoff_date="2026-08-11",
        rule_snapshot={"rule_set_id": "rs-1", "version": "v1"},
        input_summary={},
        report=report,
    )


def _events(response_text: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response_text.splitlines()
        if line.startswith("data: ")
    ]


def test_report_chunks_reassemble_persisted_markdown_verbatim():
    markdown = "港股 IPO 风险报告" * 1000
    chunks = list(analysis_api._report_chunks(markdown, size=97))

    assert len(chunks) > 1
    assert "".join(chunks) == markdown
    assert all(len(chunk) <= 97 for chunk in chunks)


def test_stream_report_returns_only_persisted_flexible_report(monkeypatch):
    run = _run()
    monkeypatch.setattr(analysis_api, "_repo", lambda: _ReportRepository(run))
    client = TestClient(create_app())

    response = client.get("/analysis-runs/run_report/stream-report")
    events = _events(response.text)

    assert response.status_code == 200
    assert events[-1] == {"type": "report_done"}
    assert "".join(
        str(event["delta"])
        for event in events
        if event.get("type") == "report_chunk"
    ) == run.report.markdown
    assert all(event.get("type") != "error" for event in events)


def test_stream_report_rejects_missing_incomplete_or_reportless_run(monkeypatch):
    client = TestClient(create_app())

    monkeypatch.setattr(analysis_api, "_repo", lambda: _ReportRepository(None))
    assert client.get("/analysis-runs/missing/stream-report").status_code == 404

    monkeypatch.setattr(
        analysis_api,
        "_repo",
        lambda: _ReportRepository(_run(status="risk_identification")),
    )
    assert client.get("/analysis-runs/run_report/stream-report").status_code == 409

    monkeypatch.setattr(
        analysis_api,
        "_repo",
        lambda: _ReportRepository(_run(with_report=False)),
    )
    assert client.get("/analysis-runs/run_report/stream-report").status_code == 404


def test_report_artifact_previews_persisted_html_and_downloads(monkeypatch):
    run = _run()
    assert run.report is not None
    run.report.html = "<!doctype html><html><body><h1>测试报告</h1><p>证据 ev_1</p></body></html>"
    monkeypatch.setattr(analysis_api, "_repo", lambda: _ReportRepository(run))
    client = TestClient(create_app())

    preview = client.get("/analysis-runs/run_report/report/artifact")
    download = client.get("/analysis-runs/run_report/report/artifact?download=1")

    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("text/html")
    assert preview.headers["content-disposition"].startswith("inline")
    assert preview.headers["x-content-type-options"] == "nosniff"
    assert preview.text == run.report.html
    assert download.headers["content-disposition"].startswith("attachment")


def test_report_artifact_rejects_incomplete_or_reportless_run(monkeypatch):
    client = TestClient(create_app())
    monkeypatch.setattr(analysis_api, "_repo", lambda: _ReportRepository(_run(status="risk_identification")))
    assert client.get("/analysis-runs/run_report/report/artifact").status_code == 409
    assert client.get("/analysis-runs/run_report/report.html").status_code == 409
    monkeypatch.setattr(analysis_api, "_repo", lambda: _ReportRepository(_run(with_report=False)))
    assert client.get("/analysis-runs/run_report/report/artifact").status_code == 404
    assert client.get("/analysis-runs/run_report/report.html").status_code == 404


def test_report_html_route_generates_missing_html(monkeypatch):
    run = _run()
    assert run.report is not None
    run.report.html = "  "
    run.report.summary = "收入连续下滑"
    monkeypatch.setattr(analysis_api, "_repo", lambda: _ReportRepository(run))
    client = TestClient(create_app())

    page = client.get("/analysis-runs/run_report/report.html")
    artifact = client.get("/analysis-runs/run_report/report/artifact")
    download = client.get("/analysis-runs/run_report/report.html?download=1")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "收入连续下滑" in page.text
    assert page.text.startswith("<!doctype html>")
    assert artifact.text == page.text
    assert download.headers["content-disposition"].startswith("attachment")
    assert "risk-report-run_report.html" in download.headers["content-disposition"]
