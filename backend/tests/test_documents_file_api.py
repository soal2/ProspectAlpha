"""WP2 contract tests: PDF original file serving + evidence↔document linkage."""

from __future__ import annotations

import time
from types import SimpleNamespace

import fitz
import pytest
from fastapi.testclient import TestClient
from pathlib import Path

from riskagent.analysis_runs import RunEvidence
from riskagent.api.main import create_app
from riskagent.documents import DocumentAsset, DocumentRepository
from riskagent.ingestion import ProspectusIngestionRepository, ProspectusRegisterRequest
from riskagent.retrieval import ChunkSearchResult
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


def test_database_upload_returns_registered_asset_and_schedules_mineru(
    monkeypatch,
) -> None:
    scheduled: list[str] = []
    asset = DocumentAsset(
        document_id="doc_async",
        filename="async.pdf",
        content_type="application/pdf",
        file_sha256="a" * 64,
        storage_uri="file:///tmp/async.pdf",
        file_size_bytes=123,
        page_count=1,
        text_chars=0,
        block_count=0,
        processing_status="queued",
        created_at="2026-07-15T00:00:00+00:00",
    )

    class FakeRepository:
        use_database = True

        def get(self, document_id):
            assert document_id == "doc_async"
            return asset

    monkeypatch.setattr("riskagent.api.documents._repo", lambda: FakeRepository())
    monkeypatch.setattr(
        "riskagent.api.documents.register_uploaded_prospectus",
        lambda **_kwargs: SimpleNamespace(document_id="doc_async"),
    )
    monkeypatch.setattr(
        "riskagent.api.documents.process_registered_prospectus",
        lambda document_id: scheduled.append(document_id),
    )

    response = TestClient(create_app()).post(
        "/documents/upload",
        files={"file": ("async.pdf", _sample_pdf_bytes("async"), "application/pdf")},
    )

    assert response.status_code == 201
    assert response.json()["processing_status"] == "queued"
    assert response.json()["block_count"] == 0
    assert scheduled == ["doc_async"]


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


def test_pdf_upload_defers_blocks_until_page_progress_is_reported(tmp_path) -> None:
    pdf = fitz.open()
    for page_number in range(2):
        page = pdf.new_page()
        page.insert_text((72, 72), f"Risk Factors page {page_number + 1}", fontsize=11)
    content = pdf.tobytes()
    pdf.close()
    repository = DocumentRepository(
        path=tmp_path / "documents.yaml",
        use_database=False,
        object_store=LocalObjectStore(tmp_path / "object-store", bucket="test-bucket"),
    )
    asset = repository.create_from_bytes(
        filename="prospectus.pdf",
        content=content,
        content_type="application/pdf",
    )

    assert asset.page_count == 2
    assert asset.block_count == 0
    progress: list[tuple[int, int, int]] = []
    blocks = repository.ensure_parsed(
        asset.document_id,
        on_progress=lambda done, total, count: progress.append((done, total, count)),
    )

    assert [item[0] for item in progress] == [1, 2]
    assert all(item[1] == 2 for item in progress)
    assert progress[-1][2] == len(blocks)
    assert repository.get(asset.document_id).block_count == len(blocks)


def test_local_object_store_get_bytes(tmp_path) -> None:
    store = LocalObjectStore(tmp_path, bucket="b")
    store.put_bytes(key="documents/x.pdf", content=b"%PDF-abc", content_type="application/pdf")
    assert store.get_bytes(key="documents/x.pdf") == b"%PDF-abc"
    with pytest.raises(KeyError):
        store.get_bytes(key="documents/missing.pdf")


def test_registered_postgres_document_is_visible_without_reparsing(tmp_path) -> None:
    from sqlalchemy.orm import sessionmaker

    from riskagent.database import make_engine

    class ExistingChunkIndexer:
        document_id = ""
        normalized: list[str] = []

        def normalize_legacy_bbox_contract(self, document_id: str) -> int:
            self.normalized.append(document_id)
            return 1

        def indexed_chunk_count(self, document_id: str) -> int:
            return 1 if document_id == self.document_id else 0

        def list_document_chunks(self, document_id: str):
            assert document_id == self.document_id
            return [
                ChunkSearchResult(
                    chunk_id=f"{document_id}_chunk_1",
                    document_id=document_id,
                    doc_key="existing",
                    chunk_type="text",
                    text="真实招股书风险因素",
                    page_idx=3,
                    source_file="existing.pdf",
                    source_item_indices=[1],
                    bbox_refs=[],
                    merged_bboxes=[{"page_idx": 3, "bbox": [10, 20, 30, 40]}],
                    image_path=None,
                    mineru_img_path=None,
                    table_html=None,
                    metadata={"bbox_coordinate_space": "normalized_1000"},
                )
            ]

    engine = make_engine(f"sqlite:///{tmp_path / 'registered.db'}")
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    pdf_path = tmp_path / "existing.pdf"
    pdf_path.write_bytes(_sample_pdf_bytes("Existing prospectus"))
    ingestion = ProspectusIngestionRepository(
        raw_dir=tmp_path / "raw",
        session_factory=session_factory,
    )
    registration = ingestion.register(
        ProspectusRegisterRequest(source_path=pdf_path, doc_key="existing")
    )
    indexer = ExistingChunkIndexer()
    indexer.document_id = registration.document_id

    repository = DocumentRepository(
        session_factory=session_factory,
        use_database=True,
        object_store=LocalObjectStore(tmp_path / "objects", bucket="test"),
        chunk_indexer=indexer,  # type: ignore[arg-type]
    )

    duplicate_upload = repository.create_from_bytes(
        filename="existing-again.pdf",
        content=pdf_path.read_bytes(),
        content_type="application/pdf",
    )
    assert duplicate_upload.document_id == registration.document_id

    listed = repository.list_assets()
    assert [(item.document_id, item.block_count) for item in listed] == [
        (registration.document_id, 1)
    ]
    content, content_type, filename = repository.get_file(registration.document_id)
    assert content == pdf_path.read_bytes()
    assert content_type == "application/pdf"
    assert filename == "existing.pdf"
    assert repository.prepare_for_retrieval(registration.document_id).block_count == 1
    assert indexer.normalized == [registration.document_id]
    blocks = repository.get_blocks(registration.document_id)
    assert blocks[0].page_number == 3
    assert blocks[0].bbox == [10.0, 20.0, 30.0, 40.0]


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
    assert legacy.bbox_coordinate_space == "pdf_points"

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

    mineru = RunEvidence(
        evidence_id="ev_3",
        finding_id="f_1",
        document_block_id="doc_explicit_chunk_000001",
        document_id="doc_explicit",
        page_number=1,
        section_path="",
        text="示例",
        confidence=0.5,
        verifier_status="pending",
    )
    assert mineru.bbox_coordinate_space == "normalized_1000"


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
    for _ in range(200):
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
