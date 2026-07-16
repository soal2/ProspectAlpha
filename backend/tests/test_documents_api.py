from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from riskagent.api.main import create_app
from riskagent.documents import DocumentAsset
from riskagent.ingestion import ProspectusRegistration


class _DocumentRepository:
    def __init__(self, registration: ProspectusRegistration) -> None:
        self.registration = registration

    def get(self, document_id: str) -> ProspectusRegistration:
        if document_id != self.registration.document_id:
            raise KeyError(document_id)
        return self.registration


def _registration(tmp_path: Path) -> ProspectusRegistration:
    pdf_path = tmp_path / "prospectus.pdf"
    with fitz.open() as pdf:
        pdf.new_page()
        pdf.save(pdf_path)
    return ProspectusRegistration(
        document_id="doc_test",
        doc_key="test",
        original_filename="prospectus.pdf",
        raw_pdf_path=pdf_path,
        content_type="application/pdf",
        file_sha256="hash",
        file_size_bytes=pdf_path.stat().st_size,
        source_url=None,
        status="registered",
    )


def test_document_metadata_and_pdf_routes(tmp_path, monkeypatch):
    registration = _registration(tmp_path)
    monkeypatch.setattr(
        "riskagent.api.documents._repo", lambda: _DocumentRepository(registration)
    )
    client = TestClient(create_app())

    metadata = client.get("/documents/doc_test")
    assert metadata.status_code == 200
    assert metadata.json()["page_count"] == 1
    assert metadata.json()["storage_uri"] == "/documents/doc_test/file"

    pdf = client.get("/documents/doc_test/file")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")

    assert client.get("/documents/missing/file").status_code == 404


def test_failed_document_can_be_reparsed_without_reupload(monkeypatch):
    asset = DocumentAsset(
        document_id="doc_failed",
        filename="failed.pdf",
        content_type="application/pdf",
        file_sha256="hash",
        storage_uri="file:///tmp/failed.pdf",
        file_size_bytes=100,
        page_count=2,
        text_chars=0,
        block_count=0,
        processing_status="failed",
        processing_error_code="model_download_failed",
        processing_error_message="模型下载失败",
        processing_attempts=1,
        source_url=None,
        created_at="2026-07-16T00:00:00+00:00",
    )

    class Repo:
        use_database = True

        def get(self, document_id):
            assert document_id == asset.document_id
            return asset.model_copy(update={"processing_status": "queued"})

    queued = []
    processed = []
    monkeypatch.setattr("riskagent.api.documents._repo", lambda: Repo())
    monkeypatch.setattr(
        "riskagent.api.documents.queue_registered_prospectus",
        lambda document_id: queued.append(document_id),
    )
    monkeypatch.setattr(
        "riskagent.api.documents.process_registered_prospectus",
        lambda document_id: processed.append(document_id),
    )

    response = TestClient(create_app()).post("/documents/doc_failed/reparse")

    assert response.status_code == 202
    assert response.json()["processing_status"] == "queued"
    assert queued == ["doc_failed"]
    assert processed == ["doc_failed"]
