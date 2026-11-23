"""Text offsets stay text offsets, and PDF evidence keeps real pages."""

from __future__ import annotations

import fitz

from riskagent.analysis_runs import (
    AnalysisRun,
    AnalysisRunCreate,
    DocumentBlock,
    ReportDraft,
    RiskFinding,
    RunEvidence,
    VerificationResult,
    _chunk_evidence_candidate,
    _parse_document_blocks,
    _quality_review,
)
from riskagent.documents import _parse_pdf, _parse_text
from riskagent.provenance import (
    MANUFACTURED_FULL_PAGE_BBOX,
    format_evidence_location,
    iter_text_chunks,
    passes_real_pdf_evidence,
)
from riskagent.reporting.flexible import render_report_markdown
from riskagent.reporting.html import render_risk_report_html
from riskagent.retrieval import ChunkSearchResult


def test_text_chunks_use_source_offsets_not_chunk_index():
    text = ("甲" * 1000) + "\n\n" + ("乙" * 40)
    chunks = iter_text_chunks(text, size=900)

    assert len(chunks) == 3
    assert all(text[chunk.char_start : chunk.char_end] == chunk.text for chunk in chunks)
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == 900
    assert chunks[1].char_start == 900
    assert chunks[2].char_start == text.find("乙")
    assert chunks[2].char_start != 1800

    blocks = _parse_document_blocks(
        "run_text",
        AnalysisRunCreate(company_name="文本 IPO", document_text=text),
        text,
    )
    assert blocks
    assert all(block.locator_kind == "text_offset" for block in blocks)
    assert all(block.page_number is None for block in blocks)
    assert all(block.bbox == [] for block in blocks)
    assert all(block.coordinates_are_pdf is False for block in blocks)
    assert all(text[block.char_start : block.char_end] == block.text for block in blocks)
    assert not any(tuple(block.bbox) == MANUFACTURED_FULL_PAGE_BBOX for block in blocks)


def test_uploaded_plain_text_has_no_pdf_page():
    text = "风险因素\n\n公司存在关联交易。" + ("甲" * 1000)
    blocks = _parse_text("doc_text", text)

    assert blocks[0].locator_kind == "text_offset"
    assert blocks[0].page_number is None
    assert blocks[0].bbox == []
    assert text[blocks[0].char_start : blocks[0].char_end] == blocks[0].text
    assert text[blocks[-1].char_start : blocks[-1].char_end] == blocks[-1].text
    assert blocks[-1].char_start > 900


def test_real_pdf_keeps_parser_page_and_bbox():
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "风险因素 关联交易", fontsize=12)
    content = pdf.tobytes()
    pdf.close()

    blocks = _parse_pdf("doc_pdf", content)

    assert blocks
    assert blocks[0].locator_kind == "pdf_page"
    assert blocks[0].page_number == 1
    assert blocks[0].coordinates_are_pdf is True
    assert len(blocks[0].bbox) == 4
    assert tuple(blocks[0].bbox) != MANUFACTURED_FULL_PAGE_BBOX
    assert passes_real_pdf_evidence(
        locator_kind=blocks[0].locator_kind,
        page_number=blocks[0].page_number,
        bbox=blocks[0].bbox,
        source_hash=blocks[0].source_hash,
        coordinates_are_pdf=blocks[0].coordinates_are_pdf,
        parser_version=blocks[0].parser_version,
    )


def test_text_evidence_does_not_pass_pdf_quality_and_pdf_evidence_does():
    text_run = _run_with_evidence(
        RunEvidence(
            evidence_id="ev_text",
            finding_id="finding_001",
            document_block_id="block_text",
            document_id="doc_text",
            section_path="风险因素",
            text="关联交易",
            source_hash="abc",
            char_start=4,
            char_end=8,
            locator_kind="text_offset",
            coordinates_are_pdf=False,
            parser_version="text-offset-v1",
            page_number=3,
            bbox=list(MANUFACTURED_FULL_PAGE_BBOX),
            verifier_status="supported",
            relevance_score=0.8,
        )
    )
    assert text_run.evidence[0].page_number is None
    assert text_run.evidence[0].bbox == []
    text_quality = _quality_review(text_run)
    assert text_quality["review_status"] == "needs_human_review"
    assert any("不能按真实 PDF 证据通过质检" in issue for issue in text_quality["issues"])
    assert not passes_real_pdf_evidence(
        locator_kind="text_offset",
        page_number=None,
        bbox=list(MANUFACTURED_FULL_PAGE_BBOX),
        source_hash="abc",
        coordinates_are_pdf=False,
        parser_version="text-offset-v1",
    )

    pdf_run = _run_with_evidence(
        RunEvidence(
            evidence_id="ev_pdf",
            finding_id="finding_001",
            document_block_id="block_pdf",
            document_id="doc_pdf",
            section_path="风险因素",
            text="关联交易",
            source_hash="def",
            page_number=7,
            bbox=[10.0, 20.0, 30.0, 40.0],
            bbox_coordinate_space="pdf_points",
            locator_kind="pdf_page",
            coordinates_are_pdf=True,
            parser_version="pymupdf-v1",
            verifier_status="supported",
            relevance_score=0.8,
        )
    )
    pdf_quality = _quality_review(pdf_run)
    assert pdf_quality["review_status"] == "draft"
    assert pdf_quality["issues"] == []
    assert pdf_run.evidence[0].page_number == 7
    assert pdf_run.evidence[0].bbox == [10.0, 20.0, 30.0, 40.0]


def test_cross_page_table_keeps_real_pages_header_and_does_not_invent_unit():
    chunk = ChunkSearchResult(
        chunk_id="doc_table_chunk_0001",
        document_id="doc_table",
        doc_key="doc_table",
        chunk_type="table",
        text="年份 收入\n2024 100",
        page_idx=12,
        source_file="prospectus.pdf",
        source_item_indices=[4, 5],
        bbox_refs=[],
        merged_bboxes=[
            {"page_idx": 12, "bbox": [1.0, 2.0, 3.0, 4.0]},
            {"page_idx": 13, "bbox": [5.0, 6.0, 7.0, 8.0]},
        ],
        image_path=None,
        mineru_img_path=None,
        table_html="<table></table>",
        metadata={
            "section_path": "财务资料",
            "bbox_coordinate_space": "normalized_1000",
            "table_header": "年份 收入",
            "table_row": "营业收入",
            "column_index": 2,
            "table_group_bbox_refs": [
                {"page_idx": 12, "bbox": [1.0, 2.0, 3.0, 4.0]},
                {"page_idx": 13, "bbox": [5.0, 6.0, 7.0, 8.0]},
            ],
            "source_hash": "tablehash",
            "parser_version": "mineru-v1",
            "coordinates_are_pdf": True,
        },
    )
    candidate = _chunk_evidence_candidate(chunk)
    evidence = RunEvidence(
        evidence_id="ev_table",
        finding_id="finding_001",
        document_block_id=candidate["block_id"],
        document_id=candidate["document_id"],
        section_path=candidate["section_path"],
        text=candidate["text"],
        source_hash=candidate["source_hash"],
        verifier_status="supported",
        **{
            key: candidate[key]
            for key in (
                "locator_kind",
                "page_number",
                "bbox",
                "bbox_coordinate_space",
                "coordinates_are_pdf",
                "parser_version",
                "table_header",
                "table_row",
                "table_column",
                "pdf_regions",
                "char_start",
                "char_end",
            )
            if key in candidate
        },
    )

    assert evidence.locator_kind == "pdf_page"
    assert evidence.page_number == 12
    assert [region.page_number for region in evidence.pdf_regions] == [12, 13]
    assert evidence.table_header == "年份 收入"
    assert evidence.table_row == "营业收入"
    assert evidence.table_column == "2"
    assert evidence.table_unit is None
    assert "table_unit" not in candidate
    location = format_evidence_location(evidence)
    assert "P.12" in location and "P.13" in location
    assert "表头 年份 收入" in location
    assert "行 营业收入" in location
    assert "列 2" in location
    assert "单位" not in location

    document = {
        "summary": "摘要",
        "themes": [],
        "exploratory_findings": [],
        "counterevidence": [],
        "limitations": [],
        "evidence_index": [
            {
                "evidence_id": evidence.evidence_id,
                "finding_id": evidence.finding_id,
                "page_number": evidence.page_number,
                "locator_kind": evidence.locator_kind,
                "section_path": evidence.section_path,
                "text": evidence.text,
                "location": location,
                "table_header": evidence.table_header,
            }
        ],
    }
    markdown = render_report_markdown(type("Run", (), {"company_name": "表格 IPO"})(), document)
    html = render_risk_report_html(
        company_name="表格 IPO",
        run_id="run_table",
        report_id="report_table",
        created_at="2026-11-16T00:00:00Z",
        risk_level="unrated",
        document=document,
    )
    assert "P.12、P.13" in markdown
    assert "P.12、P.13" in html
    assert "单位未提供" not in markdown
    assert "单位未提供" not in html


def test_loaded_text_block_keeps_historical_values_as_unknown():
    block = DocumentBlock(
        block_id="block_old",
        document_id="doc_old",
        page_number=4,
        section_path="正文",
        text="旧文本",
        bbox=list(MANUFACTURED_FULL_PAGE_BBOX),
        char_start=0,
        char_end=900,
        source_hash="old",
        parser_version="local-text-parser-v1",
    )
    assert block.locator_kind == "legacy_unknown"
    assert block.page_number == 4
    assert block.bbox == list(MANUFACTURED_FULL_PAGE_BBOX)
    assert block.char_start == 0
    assert block.char_end == 900
    assert block.parser_version == "local-text-parser-v1"
    assert block.coordinates_are_pdf is False
    location = format_evidence_location(block)
    assert location == "历史定位未核实"
    assert not passes_real_pdf_evidence(
        locator_kind=block.locator_kind,
        page_number=block.page_number,
        bbox=block.bbox,
        source_hash=block.source_hash,
        coordinates_are_pdf=block.coordinates_are_pdf,
        parser_version=block.parser_version,
    )


def _run_with_evidence(evidence: RunEvidence) -> AnalysisRun:
    markdown = "\n".join(
        [
            "## 执行摘要",
            "摘要",
            "## 风险主题",
            "主题",
            "## 探索性发现",
            "无。",
            "## 反证与争议",
            "无。",
            "## 局限",
            "无。",
            "## 证据索引",
            "ev_text finding_001",
            "ev_pdf finding_001",
        ]
    )
    return AnalysisRun(
        run_id="run_provenance",
        company_name="溯源 IPO",
        status="completed",
        current_stage="completed",
        created_at="2026-11-16T00:00:00Z",
        data_cutoff_date="2026-01-01",
        rule_snapshot={},
        input_summary={},
        findings=[
            RiskFinding(
                finding_id="finding_001",
                dimension_code="D1",
                rule_type="qualitative_disclosure",
                category="关联交易",
                title="关联交易",
                severity="medium",
                confidence=0.7,
                trigger_condition="关联交易",
                evaluation_result="triggered",
                reasoning_summary="存在关联交易。",
                evidence_ids=[evidence.evidence_id],
                verification_status="supported",
            )
        ],
        evidence=[evidence],
        verification_results=[
            VerificationResult(
                finding_id="finding_001",
                status="supported",
                checked_evidence_ids=[evidence.evidence_id],
                reason="测试",
                created_at="2026-11-16T00:00:00Z",
            )
        ],
        report=ReportDraft(
            report_id="report_provenance",
            markdown=markdown,
            html="<p>report</p>",
            created_at="2026-11-16T00:00:00Z",
        ),
    )
