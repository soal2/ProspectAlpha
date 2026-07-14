"""WP2 contract tests: PDF original file serving + evidence↔document linkage."""

from __future__ import annotations

import time

import fitz
import pytest
from fastapi.testclient import TestClient
from pathlib import Path

from riskagent.analysis_runs import RunEvidence
from riskagent.api.main import create_app
from riskagent.providers.object_store import LocalObjectStore
from riskagent.risk_rules import RiskRuleRepository


def _sample_pdf_bytes(text: str) -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text, fontsize=11)
    content = pdf.tobytes()
    pdf.close()
    return content


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DOCUMENTS_PATH", str(tmp_path / "documents.yaml"))
    monkeypatch.setenv("OBJECT_STORE_PROVIDER", "local")
    monkeypatch.setenv("OBJECT_STORE_BUCKET", "test-bucket")
    monkeypatch.setenv("OBJECT_STORE_LOCAL_ROOT", str(tmp_path / "object-store"))
    monkeypatch.setenv("USE_DATABASE", "0")
    return TestClient(create_app())


def test_document_file_roundtrip_serves_original_pdf(client) -> None:
    pdf_bytes = _sample_pdf_bytes("Risk Factors: related party transactions.")
    upload = client.post(
        "/documents/upload",
        files={"file": ("招股书.pdf", pdf_bytes, "application/pdf")},
    )
    assert upload.status_code == 201
    document_id = upload.json()["document_id"]

    response = client.get(f"/documents/{document_id}/file")
    assert response.status_code == 200
    assert response.content[:5] == b"%PDF-"
    assert response.content == pdf_bytes
    assert response.headers["content-type"].startswith("application/pdf")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("inline")
    assert "filename*=UTF-8''" in disposition


def test_document_file_404_for_unknown_id(client) -> None:
    response = client.get("/documents/doc_missing/file")
    assert response.status_code == 404


def test_list_documents_returns_uploaded_assets(client) -> None:
    assert client.get("/documents").json() == []
    pdf_bytes = _sample_pdf_bytes("Prospectus body")
    upload = client.post(
        "/documents/upload",
        files={"file": ("prospectus.pdf", pdf_bytes, "application/pdf")},
    )
    document_id = upload.json()["document_id"]
    listed = client.get("/documents").json()
    assert [item["document_id"] for item in listed] == [document_id]
    assert listed[0]["filename"] == "prospectus.pdf"


def test_local_object_store_get_bytes(tmp_path) -> None:
    store = LocalObjectStore(tmp_path, bucket="b")
    store.put_bytes(key="documents/x.pdf", content=b"%PDF-abc", content_type="application/pdf")
    assert store.get_bytes(key="documents/x.pdf") == b"%PDF-abc"
    with pytest.raises(KeyError):
        store.get_bytes(key="documents/missing.pdf")


def test_run_evidence_backfills_document_id_from_block_id() -> None:
    legacy = RunEvidence(
        evidence_id="ev_1",
        finding_id="f_1",
        document_block_id="block_doc_ab12cd34ef56_0003_012",
        page_number=3,
        section_path="风险因素",
        text="示例",
        confidence=0.8,
        verifier_status="pending",
    )
    assert legacy.document_id == "doc_ab12cd34ef56"

    explicit = RunEvidence(
        evidence_id="ev_2",
        finding_id="f_1",
        document_block_id="block_doc_x_0001_001",
        document_id="doc_explicit",
        page_number=1,
        section_path="",
        text="示例",
        confidence=0.5,
        verifier_status="pending",
    )
    assert explicit.document_id == "doc_explicit"


def test_pipeline_evidence_carries_document_id(
    tmp_path, monkeypatch, analysis_repo_factory
) -> None:
    monkeypatch.setenv("ANALYSIS_RUNS_PATH", str(tmp_path / "runs.yaml"))
    monkeypatch.setenv("EVIDENCE_LIBRARY_PATH", str(tmp_path / "evidence_library.yaml"))
    monkeypatch.setenv("DOCUMENTS_PATH", str(tmp_path / "documents.yaml"))
    monkeypatch.setenv("OBJECT_STORE_PROVIDER", "local")
    monkeypatch.setenv("OBJECT_STORE_BUCKET", "test-bucket")
    monkeypatch.setenv("OBJECT_STORE_LOCAL_ROOT", str(tmp_path / "object-store"))
    monkeypatch.setenv(
        "RISK_RULES_PATH", str(Path(__file__).parents[1] / "configs" / "risk_rules.yaml")
    )
    monkeypatch.setenv("USE_DATABASE", "0")
    repo = analysis_repo_factory(
        rule_repository=RiskRuleRepository(
            Path(__file__).parents[1] / "configs" / "risk_rules.yaml"
        ),
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
    document_id = upload.json()["document_id"]

    run_id = client.post(
        "/analysis-runs",
        json={"company_name": "PDF 溯源测试", "document_id": document_id},
    ).json()["run_id"]
    client.post(f"/analysis-runs/{run_id}/start")
    for _ in range(50):
        if client.get(f"/analysis-runs/{run_id}").json()["status"] == "completed":
            break
        time.sleep(0.1)
    else:
        raise AssertionError("analysis run did not complete")

    evidence = client.get(f"/analysis-runs/{run_id}/evidence").json()
    assert evidence
    for item in evidence:
        assert item["document_id"] == document_id
        assert item["page_number"] >= 1
        assert len(item["bbox"]) == 4
        assert item["source_hash"]
