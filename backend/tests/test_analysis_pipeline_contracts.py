from pathlib import Path
import time

import fitz
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from riskagent.analysis_runs import AnalysisRunCreate, AnalysisRunInputPatch, IndustryProfileConfirmation
from riskagent.api.analysis import _api_dispatch_mode
from riskagent.api.main import create_app
from riskagent.backtesting.service import BacktestCreate, BacktestService, MarketImportRequest
from riskagent.common.cache import TTLCache
from riskagent.database import Base, make_engine
from riskagent.documents import DocumentRepository
from riskagent.models import (
    AnalysisRunRow,
    DocumentAssetRow,
    DocumentBlockRow,
    EvidenceLibraryRow,
    RiskScoreSnapshotRow,
)
from riskagent.providers.object_store import LocalObjectStore
from riskagent.risk_rules import RiskRuleRepository


def _rule_repo() -> RiskRuleRepository:
    return RiskRuleRepository(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")


def test_api_dispatch_mode_falls_back_to_shared_execution_setting(monkeypatch):
    monkeypatch.delenv("ANALYSIS_API_EXECUTION_MODE", raising=False)
    monkeypatch.setenv("ANALYSIS_EXECUTION_MODE", "rq")

    assert _api_dispatch_mode() == "rq"


def test_clarifying_patch_start_freezes_inputs_and_generates_artifacts(
    tmp_path, analysis_repo_factory
):
    repo = analysis_repo_factory(
        rule_repository=_rule_repo(),
        path=tmp_path / "runs.yaml",
        evidence_library_path=tmp_path / "evidence_library.yaml",
    )

    run = repo.create_draft(AnalysisRunCreate(company_name="待确认公司", document_text="", confirm_defaults=False))
    assert run.status == "clarifying"
    assert len(run.blocking_questions) == 3

    ready = repo.update_inputs(
        run.run_id,
        AnalysisRunInputPatch(
            company_name="测试 IPO",
            document_text="风险因素\n公司存在关联交易、对赌和赎回安排，经营活动现金流持续为负。",
            confirm_defaults=True,
            structured_data={"经营活动现金流": "连续两期为负"},
        ),
    )
    assert ready.status == "ready"

    proposed = repo.start(run.run_id)
    assert proposed.status == "awaiting_industry_confirmation"
    completed = repo.confirm_industry_profile(run.run_id, IndustryProfileConfirmation())
    assert completed.status == "completed"
    assert completed.inputs_frozen is True
    assert completed.document_blocks
    assert completed.findings
    assert completed.evidence
    assert completed.report is not None
    assert completed.report.review_status == "draft"
    assert all(item.text for item in completed.evidence)
    evidence_library = yaml.safe_load((tmp_path / "evidence_library.yaml").read_text()) or []
    assert evidence_library

    with pytest.raises(ValueError):
        repo.update_inputs(completed.run_id, AnalysisRunInputPatch(company_name="不可修改"))


def test_completed_report_freezes_score_and_enters_t5_backtest(tmp_path, analysis_repo_factory):
    """The report completion path, not a manual endpoint, creates the backtest input."""
    repo = analysis_repo_factory(
        rule_repository=_rule_repo(),
        path=tmp_path / "runs.yaml",
        evidence_library_path=tmp_path / "evidence_library.yaml",
    )
    completed = repo.create_and_run(
        AnalysisRunCreate(
            company_name="闭环测试 IPO",
            stock_code="0001.HK",
            document_text="风险因素：公司存在关联交易、对赌和赎回安排，经营活动现金流持续为负。",
            confirm_defaults=True,
        )
    )
    assert completed.status == "completed"
    assert any(event["event_type"] == "risk_score_frozen" for event in completed.events)
    with repo.session_factory() as session:
        score = session.get(RiskScoreSnapshotRow, f"score_{completed.run_id}")
        assert score is not None
        assert score.wind_code == "0001.HK"

    security = tmp_path / "security.csv"
    company = tmp_path / "company.csv"
    prices = tmp_path / "prices.csv"
    security.write_text(
        "S_INFO_WINDCODE,S_INFO_COMPCODE,S_INFO_NAME,S_INFO_LISTDATE,S_INFO_LISTPRICE\n"
        "0001.HK,COMP1,闭环测试 IPO,20270102,10\n",
        encoding="utf-8",
    )
    company.write_text("S_INFO_COMPCODE,S_INFO_COMPNAME\nCOMP1,闭环测试 IPO\n", encoding="utf-8")
    prices.write_text(
        "S_INFO_WINDCODE,TRADE_DT,S_DQ_LOW,S_DQ_CLOSE\n"
        "0001.HK,20270102,10,10\n"
        "0001.HK,20270103,9,9.5\n"
        "0001.HK,20270106,8.9,9.2\n"
        "0001.HK,20270107,9.1,9.4\n"
        "0001.HK,20270108,9.3,9.6\n",
        encoding="utf-8",
    )
    service = BacktestService(session_factory=repo.session_factory, initialize=False)
    imported = service.import_market_data(
        MarketImportRequest(
            security_csv=str(security), company_csv=str(company), prices_csv=str(prices)
        )
    )
    snapshots = service.list_market_snapshots()
    assert snapshots[0].snapshotId == imported["snapshot_id"]
    assert snapshots[0].t5Labels == 1
    assert snapshots[0].eligibleSamples == 1
    assert snapshots[0].canCreateBacktest is False
    service.publish_market_snapshot(imported["snapshot_id"])
    assert service.list_market_snapshots()[0].canCreateBacktest is True
    backtest = service.create_backtest(BacktestCreate(snapshot_id=imported["snapshot_id"], wind_code="0001.HK"))
    assert backtest.status == "待运行"
    job = service.start_backtest(backtest.id)
    service.run_job(job.id)
    detail = service.detail(backtest.id)
    assert detail["summary"]["status"] == "完成", detail.get("jobs", [{}])[0].get("error")
    assert detail["samples"][0]["actualDecline"] is True


def test_analysis_api_exposes_minimal_pipeline_resources(
    tmp_path, monkeypatch, analysis_repo_factory
):
    monkeypatch.setenv("ANALYSIS_API_EXECUTION_MODE", "inline")
    monkeypatch.setenv("ANALYSIS_RUNS_PATH", str(tmp_path / "runs.yaml"))
    monkeypatch.setenv("EVIDENCE_LIBRARY_PATH", str(tmp_path / "evidence_library.yaml"))
    monkeypatch.setenv("DOCUMENTS_PATH", str(tmp_path / "documents.yaml"))
    monkeypatch.setenv("RISK_RULES_PATH", str(Path(__file__).parents[1] / "configs" / "risk_rules.yaml"))
    monkeypatch.setenv("USE_DATABASE", "0")
    repo = analysis_repo_factory(
        rule_repository=_rule_repo(),
        path=tmp_path / "runs.yaml",
        evidence_library_path=tmp_path / "evidence_library.yaml",
    )
    monkeypatch.setattr("riskagent.api.analysis._repo", lambda: repo)
    client = TestClient(create_app())

    document_response = client.post(
        "/documents",
        json={
            "filename": "sample-prospectus.txt",
            "content_text": "风险因素\n招股书披露对赌、赎回、关联交易和供应商集中度风险。",
        },
    )
    assert document_response.status_code == 201
    document = document_response.json()
    assert document["text_chars"] == len(
        "风险因素\n招股书披露对赌、赎回、关联交易和供应商集中度风险。"
    )
    assert set(document) == {
        "document_id",
        "filename",
        "content_type",
        "file_sha256",
        "storage_uri",
        "file_size_bytes",
        "page_count",
            "text_chars",
        "block_count",
        "processing_status",
        "processing_error_code",
        "processing_error_message",
        "processing_attempts",
        "source_url",
        "created_at",
    }
    duplicate = client.post(
        "/documents",
        json={
            "filename": "duplicate.txt",
            "content_text": "风险因素\n招股书披露对赌、赎回、关联交易和供应商集中度风险。",
        },
    ).json()
    assert duplicate["document_id"] == document["document_id"]
    blocks_response = client.get(f"/documents/{document['document_id']}/blocks")
    assert blocks_response.status_code == 200
    blocks = blocks_response.json()
    assert blocks
    assert blocks[0]["document_id"] == document["document_id"]

    response = client.post(
        "/analysis-runs",
        json={
            "company_name": "测试 IPO",
            "document_id": document["document_id"],
            "structured_data": {"供应商集中度": "Top5 采购占比 80%"},
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert "input_payload" not in payload
    assert payload["rule_snapshot"]["totals"]["rule_count"] == (
        _rule_repo().load().totals["rule_count"]
    )
    assert payload["status"] == "ready"
    assert payload["findings"] == []

    run_id = payload["run_id"]
    started = client.post(f"/analysis-runs/{run_id}/start").json()
    assert started["status"] == "awaiting_industry_confirmation"
    started = client.post(f"/analysis-runs/{run_id}/industry-profile/confirm", json={}).json()
    assert started["status"] in {"queued", "completed"}
    for _ in range(200):
        current = client.get(f"/analysis-runs/{run_id}").json()
        if current["status"] == "completed":
            started = current
            break
        time.sleep(0.1)
    else:
        raise AssertionError("analysis run did not complete")
    events = client.get(f"/analysis-runs/{run_id}/events").json()["events"]
    assert events
    assert {event["actor_name"] for event in events}.issuperset(
        {"financial_agent", "legal_agent", "business_agent", "market_agent", "orchestrator"}
    )
    assert client.get(f"/analysis-runs/{run_id}/evidence").json()
    trace = client.get(f"/analysis-runs/{run_id}/trace").json()
    assert set(trace) == {"stages", "seed", "flow", "evidenceFeed"}
    assert trace["stages"][-1] == {
        "key": "completed",
        "label": "交付完成",
        "owner": "analysis_run",
        "status": "done",
        "cost": None,
    }
    assert {event["event_type"] for event in events}.issuperset(
        {"strategy_decision_validated", "peer_request_sent", "dynamic_team_completed"}
    )
    assert trace["evidenceFeed"]
    assert "text" not in trace["evidenceFeed"][0]
    report = client.get(f"/analysis-runs/{run_id}/report").json()
    assert report["review_status"] in {"draft", "needs_human_review"}
    assert report["overall_risk_level"] in {
        "very_low",
        "low",
        "medium",
        "high",
        "very_high",
        "unrated",
    }
    assert report["markdown"].startswith("# 测试 IPO IPO 隐性风险尽调报告")
    assert "## 探索性发现" in report["markdown"]
    assert "## 反证与争议" in report["markdown"]
    assert set(report).issuperset(
        {"summary", "themes", "exploratory_findings", "counterevidence", "limitations", "evidence_index"}
    )


def test_pdf_upload_drives_analysis_with_page_and_bbox_evidence(
    tmp_path, monkeypatch, analysis_repo_factory
):
    monkeypatch.setenv("ANALYSIS_API_EXECUTION_MODE", "inline")
    monkeypatch.setenv("ANALYSIS_RUNS_PATH", str(tmp_path / "runs.yaml"))
    monkeypatch.setenv("EVIDENCE_LIBRARY_PATH", str(tmp_path / "evidence_library.yaml"))
    monkeypatch.setenv("DOCUMENTS_PATH", str(tmp_path / "documents.yaml"))
    monkeypatch.setenv("OBJECT_STORE_PROVIDER", "local")
    monkeypatch.setenv("OBJECT_STORE_BUCKET", "test-bucket")
    monkeypatch.setenv("OBJECT_STORE_LOCAL_ROOT", str(tmp_path / "object-store"))
    monkeypatch.setenv("RISK_RULES_PATH", str(Path(__file__).parents[1] / "configs" / "risk_rules.yaml"))
    monkeypatch.setenv("USE_DATABASE", "0")
    repo = analysis_repo_factory(
        rule_repository=_rule_repo(),
        path=tmp_path / "runs.yaml",
        evidence_library_path=tmp_path / "evidence_library.yaml",
    )
    monkeypatch.setattr("riskagent.api.analysis._repo", lambda: repo)
    client = TestClient(create_app())

    pdf_bytes = _sample_pdf_bytes(
        "Risk Factors\nThe company has related party transactions, redemption obligations "
        "and negative operating cash flow."
    )
    upload = client.post(
        "/documents/upload",
        files={"file": ("prospectus.pdf", pdf_bytes, "application/pdf")},
    )
    assert upload.status_code == 201
    document = upload.json()
    assert document["content_type"] == "application/pdf"
    assert document["storage_uri"].startswith("local://")
    assert document["page_count"] == 1
    assert document["block_count"] >= 1
    stored_pdf = tmp_path / "object-store" / "test-bucket" / f"documents/{document['document_id']}.pdf"
    assert stored_pdf.exists()

    run_response = client.post(
        "/analysis-runs",
        json={"company_name": "PDF 测试 IPO", "document_id": document["document_id"]},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["run_id"]
    started = client.post(f"/analysis-runs/{run_id}/start").json()
    assert started["status"] == "awaiting_industry_confirmation"
    started = client.post(f"/analysis-runs/{run_id}/industry-profile/confirm", json={}).json()
    assert started["status"] in {"queued", "completed"}
    for _ in range(200):
        current = client.get(f"/analysis-runs/{run_id}").json()
        if current["status"] == "completed":
            started = current
            break
        time.sleep(0.1)
    else:
        raise AssertionError("analysis run did not complete")

    evidence = client.get(f"/analysis-runs/{run_id}/evidence").json()
    assert evidence
    assert evidence[0]["page_number"] == 1
    assert evidence[0]["bbox"]
    assert evidence[0]["source_hash"]
    progress = client.get(f"/analysis-runs/{run_id}/progress").json()
    assert progress["pdf"]["status"] == "completed"
    assert progress["pdf"]["parsed_pages"] == 1
    assert progress["pdf"]["total_pages"] == 1
    assert progress["pdf"]["parsed_blocks"] >= 1
    assert progress["index"]["status"] == "completed"

    legacy_run = repo.get(run_id)
    legacy_run.progress = {}
    repo._save_run(legacy_run)
    legacy_progress = client.get(f"/analysis-runs/{run_id}/progress").json()
    assert legacy_progress["pdf"]["status"] == "completed"
    assert legacy_progress["pdf"]["parsed_pages"] == 1


def test_database_persistence_covers_document_run_and_evidence(
    tmp_path, analysis_repo_factory
):
    class FakePostgresIndexer:
        def indexed_chunk_count(self, _document_id: str) -> int:
            return 0

        def index_chunks(self, *, chunks, **_kwargs) -> int:
            return len(chunks)

    engine = make_engine(f"sqlite:///{tmp_path / 'runtime.db'}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    document_repo = DocumentRepository(
        session_factory=session_factory,
        use_database=True,
        object_store=LocalObjectStore(tmp_path / "object-store", bucket="db-test"),
        chunk_indexer=FakePostgresIndexer(),  # type: ignore[arg-type]
    )
    analysis_repo = analysis_repo_factory(
        rule_repository=_rule_repo(),
        path=tmp_path / "runs.yaml",
        evidence_library_path=tmp_path / "evidence_library.yaml",
        document_repository=document_repo,
        session_factory=session_factory,
        use_database=True,
    )

    document = document_repo.create_from_bytes(
        filename="prospectus.pdf",
        content=_sample_pdf_bytes(
            "Risk Factors\nThe company has related party transactions and redemption obligations."
        ),
        content_type="application/pdf",
    )
    assert (tmp_path / "object-store" / "db-test" / f"documents/{document.document_id}.pdf").exists()
    run = analysis_repo.create_draft(
        AnalysisRunCreate(company_name="DB 测试 IPO", document_id=document.document_id)
    )
    proposed = analysis_repo.start(run.run_id)
    assert proposed.status == "awaiting_industry_confirmation"
    completed = analysis_repo.confirm_industry_profile(
        run.run_id,
        IndustryProfileConfirmation(),
    )

    assert completed.status == "completed"
    with session_factory() as session:
        assert session.query(DocumentAssetRow).count() == 1
        assert session.query(DocumentBlockRow).count() >= 1
        assert session.query(AnalysisRunRow).count() == 1
        assert session.query(EvidenceLibraryRow).count() >= 1


def test_ttl_cache_prunes_under_pressure():
    cache: TTLCache[str] = TTLCache(ttl_seconds=0.001, max_items=16)
    for idx in range(100):
        cache.set(f"k{idx}", "x" * 100)
    assert len(cache) <= 16
    cache.collect_garbage()
    assert len(cache) <= 16
    cache.clear()
    assert len(cache) == 0


def _sample_pdf_bytes(text: str) -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text, fontsize=11)
    content = pdf.tobytes()
    pdf.close()
    return content
