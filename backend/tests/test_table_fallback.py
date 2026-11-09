"""Fallback table extraction when MinerU table recognition is disabled."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import fitz
from sqlalchemy.orm import sessionmaker

from riskagent.database import make_engine
from riskagent.documents import ProspectusPipelineRequest, process_prospectus_document
from riskagent.ingestion import ProspectusIngestionRepository
from riskagent.parsing.chunker import BlockChunker, ChunkRequest
from riskagent.parsing.table_fallback import content_list_for_chunking, extract_pdf_tables


class _Indexer:
    def index_chunks(self, *, registration, chunks, processed_path=None):
        self.chunks = chunks
        return len([chunk for chunk in chunks if chunk.text.strip()])


def _ruled_table_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    rect = fitz.Rect(72, 72, 420, 210)
    data = [["Metric", "2023", "2024"], ["Revenue", "100", "80"], ["Margin", "40%", "35%"]]
    width = rect.width / 3
    height = rect.height / 3
    shape = page.new_shape()
    for index in range(4):
        y = rect.y0 + index * height
        shape.draw_line(fitz.Point(rect.x0, y), fitz.Point(rect.x1, y))
        x = rect.x0 + index * width
        shape.draw_line(fitz.Point(x, rect.y0), fitz.Point(x, rect.y1))
    shape.finish(color=(0, 0, 0), width=1)
    shape.commit()
    for row_index, row in enumerate(data):
        for column_index, cell in enumerate(row):
            cell_rect = fitz.Rect(
                rect.x0 + column_index * width + 4,
                rect.y0 + row_index * height + 6,
                rect.x0 + (column_index + 1) * width - 4,
                rect.y0 + (row_index + 1) * height - 4,
            )
            page.insert_textbox(cell_rect, cell, fontsize=11)
    doc.save(path)
    doc.close()


def _content_list(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [{"type": "text", "text": "风险因素正文", "bbox": [1, 2, 3, 4], "page_idx": 0}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_extract_pdf_tables_reads_ruled_table(tmp_path: Path) -> None:
    pdf_path = tmp_path / "tables.pdf"
    _ruled_table_pdf(pdf_path)

    tables = extract_pdf_tables(pdf_path)

    assert tables
    assert tables[0]["type"] == "table"
    assert tables[0]["page_idx"] == 0
    assert "Revenue" in tables[0]["table_body"]
    assert "100" in tables[0]["table_body"]
    assert tables[0]["table_source"] in {"pdfplumber", "pymupdf"}
    assert len(tables[0]["bbox"]) == 4
    assert all(0 <= value <= 1000 for value in tables[0]["bbox"])


def test_disabled_table_recognition_appends_fallback_before_chunking(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MINERU_TABLE_ENABLE", "false")
    pdf_path = tmp_path / "tables.pdf"
    content_list = tmp_path / "doc_content_list.json"
    _ruled_table_pdf(pdf_path)
    _content_list(content_list)

    chunk_source = content_list_for_chunking(content_list_path=content_list, pdf_path=pdf_path)
    result = BlockChunker().chunk(ChunkRequest(doc_key="doc", content_list_path=chunk_source, max_chars=400))

    assert chunk_source != content_list.resolve()
    assert chunk_source.name.endswith(".fallback_tables.json")
    table_chunks = [chunk for chunk in result.chunks if chunk.chunk_type == "table"]
    assert table_chunks
    assert "Revenue" in table_chunks[0].text
    assert table_chunks[0].table_html
    assert "100" in table_chunks[0].table_html


def test_enabled_table_recognition_keeps_mineru_content_list(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MINERU_TABLE_ENABLE", "true")
    pdf_path = tmp_path / "tables.pdf"
    content_list = tmp_path / "doc_content_list.json"
    _ruled_table_pdf(pdf_path)
    _content_list(content_list)

    assert content_list_for_chunking(content_list_path=content_list, pdf_path=pdf_path) == content_list.resolve()
    assert not content_list.with_name("doc_content_list.fallback_tables.json").exists()


def test_pipeline_indexes_fallback_tables_when_mineru_tables_are_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MINERU_TABLE_ENABLE", "false")
    monkeypatch.setattr(
        "riskagent.ingestion.prospectus.uuid.uuid4",
        lambda: uuid.UUID("22222222-2222-2222-2222-222222222222"),
    )
    pdf_path = tmp_path / "upload" / "sample.pdf"
    pdf_path.parent.mkdir(parents=True)
    _ruled_table_pdf(pdf_path)
    processed_root = tmp_path / "processed"
    document_id = "doc_222222222222"
    content_list = processed_root / document_id / "ocr" / f"{document_id}_content_list.json"
    _content_list(content_list)
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
    image_analysis: false
chunker:
  default: block
  block:
    max_chars: 800
    include_types: [text, table, image]
    skip_types: [header, footer, page_number]
    merge_text: true
    require_image_description: false
    image_context_window_blocks: 1
""",
        encoding="utf-8",
    )
    engine = make_engine(f"sqlite:///{tmp_path / 'ingestion.db'}")
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    ingestion = ProspectusIngestionRepository(
        raw_dir=tmp_path / "raw",
        session_factory=session_factory,
    )
    indexer = _Indexer()

    result = process_prospectus_document(
        ProspectusPipelineRequest(
            source_path=pdf_path,
            doc_key="sample",
            config_path=config_path,
            processed_root=processed_root,
            enable_image_vlm=False,
            index_chunks=True,
        ),
        ingestion_repository=ingestion,
        chunk_indexer=indexer,
    )

    assert result.parsed.content_list_path == content_list.resolve()
    assert any(chunk.chunk_type == "table" and "Revenue" in chunk.text for chunk in result.chunked.chunks)
    assert any(chunk.chunk_type == "table" for chunk in indexer.chunks)
