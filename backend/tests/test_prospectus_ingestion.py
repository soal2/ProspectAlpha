from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from riskagent.database import make_engine
from riskagent.ingestion import ProspectusIngestionRepository, ProspectusRegisterRequest
from riskagent.models import ProspectusDocumentRow


def _repo(tmp_path: Path) -> ProspectusIngestionRepository:
    engine = make_engine(f"sqlite:///{tmp_path / 'ingestion.db'}")
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return ProspectusIngestionRepository(
        raw_dir=tmp_path / "raw" / "prospectus",
        session_factory=session_factory,
    )


def test_register_source_path_copies_pdf_and_persists_metadata(tmp_path):
    source = tmp_path / "uploads" / "YSYY.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.4 prospectus")
    repo = _repo(tmp_path)

    registered = repo.register(ProspectusRegisterRequest(source_path=source, doc_key="ysyy"))

    assert registered.doc_key == "ysyy"
    assert registered.raw_pdf_path == (tmp_path / "raw" / "prospectus" / "ysyy.pdf").resolve()
    assert registered.raw_pdf_path.read_bytes() == source.read_bytes()
    with repo.session_factory() as session:
        row = session.scalar(
            select(ProspectusDocumentRow).where(ProspectusDocumentRow.doc_key == "ysyy")
        )
    assert row is not None
    assert row.file_sha256 == registered.file_sha256


def test_register_existing_canonical_pdf_only_records_metadata(tmp_path):
    raw_pdf = tmp_path / "raw" / "prospectus" / "ysyy.pdf"
    raw_pdf.parent.mkdir(parents=True)
    raw_pdf.write_bytes(b"%PDF-1.4 canonical")
    repo = _repo(tmp_path)

    registered = repo.register(ProspectusRegisterRequest(source_path=raw_pdf))

    assert registered.doc_key == "ysyy"
    assert registered.raw_pdf_path == raw_pdf.resolve()


def test_register_duplicate_hash_returns_existing_document(tmp_path):
    source = tmp_path / "ysyy.pdf"
    source.write_bytes(b"%PDF duplicate")
    repo = _repo(tmp_path)

    first = repo.register(ProspectusRegisterRequest(source_path=source, doc_key="ysyy"))
    second = repo.register(ProspectusRegisterRequest(content=b"%PDF duplicate", doc_key="other"))

    assert second.document_id == first.document_id
    assert second.doc_key == "ysyy"
    assert repo.get(first.document_id) == first
