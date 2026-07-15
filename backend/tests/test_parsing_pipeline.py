import json
from pathlib import Path

from riskagent.parsing import (
    ChunkRequest,
    ChunkerFactory,
    PdfProcessRequest,
    PdfProcessorFactory,
    build_chunk_request,
    load_rag_pipeline_settings,
)


def _write_content_list(path: Path) -> None:
    path.parent.mkdir(parents=True)
    (path.parent / "images").mkdir()
    (path.parent / "images" / "table.jpg").write_bytes(b"table-image")
    (path.parent / "images" / "image.jpg").write_bytes(b"image")
    path.write_text(
        json.dumps(
            [
                {"type": "text", "text": "第一段", "bbox": [1, 2, 3, 4], "page_idx": 0},
                {"type": "text", "text": "第二段", "bbox": [2, 3, 8, 9], "page_idx": 0},
                {"type": "page_number", "text": "1", "bbox": [0, 0, 1, 1], "page_idx": 0},
                {
                    "type": "table",
                    "img_path": "images/table.jpg",
                    "table_caption": ["财务摘要"],
                    "table_footnote": [],
                    "table_body": "<table><tr><td>收益</td><td>100</td></tr></table>",
                    "bbox": [10, 20, 30, 40],
                    "page_idx": 1,
                },
                {
                    "type": "image",
                    "img_path": "images/image.jpg",
                    "image_caption": ["研发管线图"],
                    "image_footnote": [],
                    "bbox": [5, 6, 7, 8],
                    "page_idx": 2,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_mineru_processor_reuses_existing_content_list(tmp_path):
    pdf = tmp_path / "raw" / "ysyy.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF existing")
    content_list = tmp_path / "processed" / "doc_ysyy" / "ocr" / "doc_ysyy_content_list.json"
    _write_content_list(content_list)

    result = PdfProcessorFactory().create().process(
        PdfProcessRequest(
            doc_key="ysyy",
            document_id="doc_ysyy",
            pdf_path=pdf,
            processed_root=tmp_path / "processed",
            reuse_existing=True,
        )
    )

    assert result.content_list_path == content_list.resolve()
    assert result.output_dir == (tmp_path / "processed" / "doc_ysyy" / "ocr").resolve()


def test_block_chunker_outputs_unified_text_table_image_chunks(tmp_path):
    content_list = tmp_path / "processed" / "ysyy" / "ocr" / "ysyy_content_list.json"
    _write_content_list(content_list)
    output_dir = tmp_path / "processed" / "ysyy" / "chunks" / "block"

    result = ChunkerFactory().create().chunk(
        ChunkRequest(
            doc_key="ysyy",
            content_list_path=content_list,
            output_dir=output_dir,
            max_chars=100,
        )
    )

    assert result.output_path == output_dir.resolve() / "chunks.jsonl"
    assert [chunk.chunk_type for chunk in result.chunks] == ["text", "table", "image"]
    assert result.chunks[0].document_id == "ysyy"
    assert result.chunks[0].chunk_id == "ysyy_chunk_000001"

    text_chunk = result.chunks[0]
    assert text_chunk.text == "第一段\n第二段"
    assert text_chunk.source_item_indices == [0, 1]
    assert text_chunk.merged_bboxes[0].bbox == [1.0, 2.0, 8.0, 9.0]

    table_chunk = result.chunks[1]
    assert table_chunk.table_html is not None
    assert table_chunk.text == "财务摘要\n收益 100"
    assert table_chunk.image_path is not None
    assert table_chunk.image_path.endswith("backend/data/processed/ysyy/ocr/images/table.jpg") or table_chunk.image_path.endswith("processed/ysyy/ocr/images/table.jpg")

    image_chunk = result.chunks[2]
    assert image_chunk.text == "研发管线图"
    assert image_chunk.mineru_img_path == "images/image.jpg"

    lines = result.output_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["bbox_refs"][0]["source_item_index"] == 0


def test_block_chunker_never_unions_bboxes_across_pages(tmp_path):
    content_list = tmp_path / "processed" / "cross-page" / "ocr" / "content_list.json"
    content_list.parent.mkdir(parents=True)
    content_list.write_text(
        json.dumps(
            [
                {"type": "text", "text": "第一页末尾", "bbox": [10, 20, 30, 40], "page_idx": 0},
                {"type": "text", "text": "第二页开头", "bbox": [100, 200, 300, 400], "page_idx": 1},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = ChunkerFactory().create().chunk(
        ChunkRequest(doc_key="cross-page", content_list_path=content_list, max_chars=100)
    )

    assert len(result.chunks) == 1
    assert [
        {"page_idx": region.page_idx, "bbox": region.bbox}
        for region in result.chunks[0].merged_bboxes
    ] == [
        {"page_idx": 1, "bbox": [10.0, 20.0, 30.0, 40.0]},
        {"page_idx": 2, "bbox": [100.0, 200.0, 300.0, 400.0]},
    ]


def test_block_chunker_keeps_image_as_hard_text_boundary(tmp_path):
    content_list = tmp_path / "processed" / "ysyy" / "ocr" / "ysyy_content_list.json"
    content_list.parent.mkdir(parents=True)
    (content_list.parent / "images").mkdir()
    (content_list.parent / "images" / "image.jpg").write_bytes(b"image")
    content_list.write_text(
        json.dumps(
            [
                {"type": "text", "text": "图片前的短句", "bbox": [1, 1, 9, 9], "page_idx": 0},
                {
                    "type": "image",
                    "img_path": "images/image.jpg",
                    "image_caption": ["中间图片"],
                    "image_footnote": [],
                    "bbox": [10, 10, 20, 20],
                    "page_idx": 0,
                },
                {"type": "text", "text": "图片后的短句", "bbox": [21, 21, 30, 30], "page_idx": 0},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = ChunkerFactory().create().chunk(
        ChunkRequest(
            doc_key="ysyy",
            content_list_path=content_list,
            max_chars=1000,
        )
    )

    assert [chunk.chunk_type for chunk in result.chunks] == ["text", "image", "text"]
    assert [chunk.text for chunk in result.chunks] == ["图片前的短句", "中间图片", "图片后的短句"]
    assert result.chunks[0].source_item_indices == [0]
    assert result.chunks[2].source_item_indices == [2]
    assert result.chunks[0].merged_bboxes[0].bbox == [1.0, 1.0, 9.0, 9.0]
    assert result.chunks[2].merged_bboxes[0].bbox == [21.0, 21.0, 30.0, 30.0]


def test_block_chunker_splits_large_tables_and_attaches_continuation_bboxes(tmp_path):
    content_list = tmp_path / "processed" / "ysyy" / "ocr" / "ysyy_content_list.json"
    content_list.parent.mkdir(parents=True)
    rows = "".join(f"<tr><td>术语{i}</td><td>指</td><td>{'说明' * 8}</td></tr>" for i in range(8))
    content_list.write_text(
        json.dumps(
            [
                {
                    "type": "table",
                    "img_path": "images/table.jpg",
                    "table_caption": ["技术词汇"],
                    "table_footnote": [],
                    "table_body": f"<table>{rows}</table>",
                    "bbox": [10, 20, 30, 40],
                    "page_idx": 42,
                },
                {
                    "type": "table",
                    "img_path": "",
                    "table_caption": [],
                    "table_footnote": [],
                    "bbox": [11, 21, 31, 41],
                    "page_idx": 43,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = ChunkerFactory().create().chunk(
        ChunkRequest(doc_key="ysyy", content_list_path=content_list, max_chars=60)
    )

    assert len(result.chunks) > 1
    assert all(chunk.chunk_type == "table" for chunk in result.chunks)
    assert all(chunk.text.strip() for chunk in result.chunks)
    assert all(chunk.metadata["table_split"] for chunk in result.chunks)
    assert {chunk.page_idx for chunk in result.chunks} == {43, 44}
    assert all(len(chunk.merged_bboxes) == 1 for chunk in result.chunks)
    assert {box.page_idx for chunk in result.chunks for box in chunk.merged_bboxes} == {43, 44}
    assert all(chunk.metadata["table_group_source_item_indices"] == [0, 1] for chunk in result.chunks)
    assert all(chunk.metadata["continuation_source_item_indices"] == [1] for chunk in result.chunks)


def test_block_chunker_repeats_table_header_for_split_table_parts(tmp_path):
    content_list = tmp_path / "processed" / "ysyy" / "ocr" / "ysyy_content_list.json"
    content_list.parent.mkdir(parents=True)
    rows = "".join(
        [
            "<tr><td>候选产品</td><td>机制</td><td>主要适应症</td></tr>",
            *[
                f"<tr><td>APG-{index}</td><td>{'机制描述' * 4}</td><td>{'适应症' * 4}</td></tr>"
                for index in range(8)
            ],
        ]
    )
    content_list.write_text(
        json.dumps(
            [
                {
                    "type": "table",
                    "img_path": "",
                    "table_caption": [],
                    "table_footnote": [],
                    "table_body": f"<table>{rows}</table>",
                    "bbox": [10, 20, 30, 40],
                    "page_idx": 1,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = ChunkerFactory().create().chunk(
        ChunkRequest(doc_key="ysyy", content_list_path=content_list, max_chars=80)
    )

    assert len(result.chunks) > 1
    assert all(chunk.metadata["table_header"] == "候选产品 | 机制 | 主要适应症" for chunk in result.chunks)
    assert all(chunk.text.startswith("候选产品 | 机制 | 主要适应症") for chunk in result.chunks)
    assert all(len(chunk.text) <= 80 for chunk in result.chunks)


def test_block_chunker_splits_single_text_item_larger_than_max_chars(tmp_path):
    content_list = tmp_path / "processed" / "ysyy" / "ocr" / "ysyy_content_list.json"
    content_list.parent.mkdir(parents=True)
    content_list.write_text(
        json.dumps(
            [
                {
                    "type": "text",
                    "text": "风险" * 20,
                    "bbox": [1, 2, 3, 4],
                    "page_idx": 0,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = ChunkerFactory().create().chunk(
        ChunkRequest(doc_key="ysyy", content_list_path=content_list, max_chars=10)
    )

    assert len(result.chunks) == 4
    assert all(len(chunk.text) <= 10 for chunk in result.chunks)
    assert all(chunk.source_item_indices == [0] for chunk in result.chunks)


def test_block_chunker_passes_absolute_image_path_to_describer(tmp_path):
    seen_paths = []
    seen_contexts = []

    class FakeImageDescriber:
        def describe(self, image_path, source_item, context=None):
            seen_paths.append(image_path)
            seen_contexts.append(context)
            return "VLM 图片描述"

    content_list = tmp_path / "processed" / "ysyy" / "ocr" / "ysyy_content_list.json"
    content_list.parent.mkdir(parents=True)
    (content_list.parent / "images").mkdir()
    (content_list.parent / "images" / "image.jpg").write_bytes(b"image")
    content_list.write_text(
        json.dumps(
            [
                {"type": "text", "text": "图片前文本", "bbox": [1, 1, 2, 2], "page_idx": 2},
                {
                    "type": "image",
                    "img_path": "images/image.jpg",
                    "image_caption": [],
                    "image_footnote": [],
                    "bbox": [5, 6, 7, 8],
                    "page_idx": 2,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = ChunkerFactory(image_describer=FakeImageDescriber()).create().chunk(
        ChunkRequest(doc_key="ysyy", content_list_path=content_list)
    )

    image_chunk = result.chunks[1]
    assert image_chunk.text == "VLM 图片描述"
    assert image_chunk.image_path is not None
    assert image_chunk.image_path.endswith("processed/ysyy/ocr/images/image.jpg")
    assert seen_paths == [(content_list.parent / "images" / "image.jpg").resolve()]
    assert seen_contexts[0]["nearby_text_before"][0]["text"] == "图片前文本"
    assert image_chunk.metadata["image_context"]["nearby_text_before"][0]["text"] == "图片前文本"


def test_chunker_uses_external_config_for_text_size_and_strategy(tmp_path):
    content_list = tmp_path / "processed" / "ysyy" / "ocr" / "ysyy_content_list.json"
    _write_content_list(content_list)
    config = tmp_path / "rag.yaml"
    config.write_text(
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
    max_chars: 3
    include_types: [text]
    skip_types: [page_number]
    merge_text: true
    require_image_description: false
""",
        encoding="utf-8",
    )
    settings = load_rag_pipeline_settings(config)

    result = ChunkerFactory().create(settings.chunker.name).chunk(
        build_chunk_request(
            doc_key="ysyy",
            content_list_path=content_list,
            output_dir=None,
            settings=settings.chunker,
        )
    )

    assert [chunk.chunk_type for chunk in result.chunks] == ["text", "text"]
    assert [chunk.text for chunk in result.chunks] == ["第一段", "第二段"]
