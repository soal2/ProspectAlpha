from pathlib import Path
import time

import fitz
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from riskagent.analysis_runs import AnalysisRunCreate, AnalysisRunInputPatch
from riskagent.api.analysis import _api_dispatch_mode
from riskagent.api.main import create_app
from riskagent.common.cache import TTLCache
from riskagent.database import Base, make_engine
from riskagent.documents import DocumentRepository
from riskagent.models import (
    AnalysisRunRow,
    DocumentAssetRow,
    DocumentBlockRow,
    EvidenceLibraryRow,
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

    completed = repo.start(run.run_id)
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


def test_analysis_api_exposes_minimal_pipeline_resources(
    tmp_path, monkeypatch, analysis_repo_factory
):
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
    assert payload["rule_snapshot"]["totals"]["rule_count"] == 143
    assert payload["status"] == "ready"
    assert payload["findings"] == []

    run_id = payload["run_id"]
    started = client.post(f"/analysis-runs/{run_id}/start").json()
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
    assert "IPO 风险分析草案" in report["markdown"]


def test_pdf_upload_drives_analysis_with_page_and_bbox_evidence(
    tmp_path, monkeypatch, analysis_repo_factory
):
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
    completed = analysis_repo.start(run.run_id)

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
