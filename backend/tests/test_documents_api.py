from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from riskagent.api.main import create_app
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
