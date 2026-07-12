import json
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from riskagent.database import make_engine
from riskagent.documents import ProspectusPipelineRequest, process_prospectus_document
from riskagent.ingestion import ProspectusIngestionRepository


class FakeChunkIndexer:
    def __init__(self) -> None:
        self.calls = []

    def index_chunks(self, *, registration, chunks, processed_path=None):
        self.calls.append(
            {
                "registration": registration,
                "chunks": chunks,
                "processed_path": processed_path,
            }
        )
        return len([chunk for chunk in chunks if chunk.text.strip()])


def test_process_prospectus_document_orchestrates_register_parse_chunk_and_index(tmp_path):
    source_pdf = tmp_path / "upload" / "YSYY.pdf"
    source_pdf.parent.mkdir(parents=True)
    source_pdf.write_bytes(b"%PDF-1.4 ysyy")

    processed_root = tmp_path / "processed"
    content_list = processed_root / "ysyy" / "ocr" / "ysyy_content_list.json"
    content_list.parent.mkdir(parents=True)
    content_list.write_text(
        json.dumps(
            [
                {"type": "text", "text": "第一段风险提示", "bbox": [1, 2, 3, 4], "page_idx": 0},
                {"type": "text", "text": "第二段业务描述", "bbox": [2, 3, 8, 9], "page_idx": 0},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    config_path = tmp_path / "rag.yaml"
    config_path.write_text(
        """
pdf_processor:
  default: mineru
  mineru:
    method: ocr
    backend: pipeline
    lang: chinese_cht
    reuse_existing: true
    image_analysis: true
chunker:
  default: block
  block:
    max_chars: 100
    include_types: [text, table, image]
    skip_types: [header, footer, page_number]
    merge_text: true
    require_image_description: false
    image_context_window_blocks: 3
""",
        encoding="utf-8",
    )

    engine = make_engine(f"sqlite:///{tmp_path / 'ingestion.db'}")
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    ingestion_repository = ProspectusIngestionRepository(
        raw_dir=tmp_path / "raw" / "prospectus",
        session_factory=session_factory,
    )
    indexer = FakeChunkIndexer()

    result = process_prospectus_document(
        ProspectusPipelineRequest(
            source_path=source_pdf,
            doc_key="ysyy",
            config_path=config_path,
            processed_root=processed_root,
            enable_image_vlm=False,
            index_chunks=True,
        ),
        ingestion_repository=ingestion_repository,
        chunk_indexer=indexer,
    )

    assert result.registration.doc_key == "ysyy"
    assert result.parsed.content_list_path == content_list.resolve()
    assert result.chunks_path == processed_root / "ysyy" / "chunks" / "block" / "chunks.jsonl"
    assert result.indexed_chunk_count == 1
    assert len(result.chunked.chunks) == 1
    assert result.chunked.chunks[0].text == "第一段风险提示\n第二段业务描述"
    assert indexer.calls[0]["registration"].document_id == result.registration.document_id
    assert indexer.calls[0]["chunks"] == result.chunked.chunks
    assert indexer.calls[0]["processed_path"].endswith("processed/ysyy/ocr")
