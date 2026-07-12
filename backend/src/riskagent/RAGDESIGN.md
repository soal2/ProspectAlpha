# Prospectus RAG Pipeline Design

This document records the prospectus document pipeline design for ProspectAlpha.
The first parsing implementation targets MinerU output, while keeping the
processor and chunker replaceable by factory interfaces. It also defines which
responsibilities belong to ingestion, parsing, and retrieval.

## Goals

- Keep original prospectus asset intake separate from parsing.
- Parse registered prospectus PDFs with MinerU by default.
- Keep MinerU raw outputs under `backend/data/processed`.
- Convert MinerU `content_list.json` items into a stable internal chunk format.
- Support text, table, and image chunks in one unified schema.
- Preserve page and bbox metadata for frontend PDF navigation and highlighting.
- Enrich image chunks with VLM descriptions in parsing so every emitted chunk has
  embeddable `text`.
- Embed the unified `text` field and store chunks in PostgreSQL from the
  retrieval/indexing layer.
- Orchestrate the full document lifecycle from `backend/src/riskagent/documents.py`
  while keeping module-specific behavior in ingestion, parsing, retrieval, and
  providers.

## Module Boundaries

### Documents Orchestrator

`backend/src/riskagent/documents.py` owns the end-to-end document workflow
orchestration.

Responsibilities:

- Provide the application-facing document entrypoint used by API routes,
  workers, and analysis runs.
- Coordinate ingestion registration, parsing, chunking, and retrieval indexing
  in order.
- Pass stable identifiers and paths between modules, such as `doc_key`,
  raw PDF path, processed output path, and chunk artifact path.
- Persist or return high-level document status for the rest of the application.
- Keep API route handlers thin by hiding workflow coordination behind document
  services.

`documents.py` does not own:

- Low-level raw PDF validation and registration rules beyond calling ingestion.
- MinerU-specific parsing logic.
- Chunk construction rules.
- Embedding provider behavior.
- PostgreSQL vector index implementation or retrieval query behavior.

The intended orchestration shape is:

```text
documents.py
  -> ingestion.register_prospectus(...)
  -> parsing.PdfProcessorFactory.create(...).process(...)
  -> parsing.ChunkerFactory.create(...).chunk(...)
  -> retrieval.ChunkIndexer.index(...)
```

Final ownership summary:

```text
ingestion: document metadata -> PostgreSQL
parsing: raw PDF -> MinerU output -> chunks.jsonl
providers: VLM and embedding clients
retrieval: chunks.jsonl -> embedding -> PostgreSQL / pgvector -> search API
documents.py: orchestration across the modules above
```

### Ingestion

Ingestion owns prospectus asset intake and registration.

Responsibilities:

- Accept a source PDF path or uploaded PDF bytes from the caller.
- Validate or discover original prospectus PDFs.
- Decide the first-version `doc_key` or future database `document_id`.
- Register the canonical raw PDF location as
  `backend/data/raw/prospectus/{doc_key}.pdf`.
- Copy the source PDF into the canonical raw location when needed. If the file is
  already there, ingestion only records metadata and does not move it.
- Record source metadata such as original filename, content type, source URL,
  file size, and file hash.
- Persist document metadata in PostgreSQL.
- Ensure the raw file is immutable once registered.
- Return registered document metadata to `documents.py` so the orchestrator can
  continue with parsing.

Ingestion does not own:

- MinerU parsing details.
- Chunk construction.
- Embedding generation.
- PostgreSQL vector indexing.

### Parsing

Parsing owns PDF processing and chunk construction.

Responsibilities:

- Consume an already registered `doc_key` and raw PDF path from ingestion.
- Produce or read MinerU output under `backend/data/processed/{doc_key}/ocr`.
- Read `{doc_key}_content_list.json`.
- Convert MinerU items into unified text, table, and image chunks.
- Preserve frontend location metadata: `page_idx`, `bbox_refs`, and
  `merged_bboxes`.
- Resolve MinerU relative `img_path` values into project-relative `image_path`
  values under `backend/data/processed/{doc_key}/ocr`.
- Request VLM descriptions for image chunks before embedding. The VLM client
  itself belongs to providers; parsing only owns when and how the description is
  attached to the chunk.
- Ensure emitted text, table, and image chunks all have a unified `text` field
  that retrieval can embed later.
- Write chunk artifacts such as
  `backend/data/processed/{doc_key}/chunks/block/chunks.jsonl`.

Parsing does not own:

- Raw PDF registration or deduplication.
- Persistent document asset metadata.
- Embedding writes to PostgreSQL.
- Retrieval query behavior.

### Retrieval

Retrieval owns embedding, indexing, and search.

Responsibilities:

- Read chunk artifacts produced by parsing.
- Validate each chunk has embeddable `text`.
- Generate embeddings from the unified `text` field.
- Store chunks, metadata, and vectors in PostgreSQL / pgvector.
- Upsert or rebuild indexes for a document.
- Provide the retrieval entrypoint for vector, keyword, or hybrid search over
  stored chunks.

Retrieval does not own:

- Raw PDF intake.
- MinerU execution.
- Chunk construction rules.

### Providers

Providers own external model integrations used by other modules.

Responsibilities:

- Provide embedding clients for retrieval.
- Provide VLM clients for image description generation requested by parsing.
- Load provider configuration and credentials.

Providers should not decide document lifecycle or storage layout.

Default VLM provider configuration:

```yaml
providers:
  vllm:
    default: dashscope_qwen37_plus
    models:
      dashscope_qwen37_plus:
        provider: dashscope
        model: qwen3.7-plus
        api_key_env: DASHSCOPE_API_KEY
        base_url: https://llm-brsq0ce4o43nxul5.cn-beijing.maas.aliyuncs.com/api/v1
```

DashScope VLM calls should use DashScope's native multimodal message shape
directly:

```python
messages = [
    {
        "role": "user",
        "content": [
            {"image": image_path_or_url},
            {"text": "图中描绘的是什么景象?"},
        ],
    }
]
```

Provider code should not hide this behind OpenAI-format conversion helpers for
the MinerU image-description path.

## PostgreSQL Configuration and Startup

Database connection helpers live in:

```text
backend/src/riskagent/database.py
```

The backend uses `DATABASE_URL` as the SQLAlchemy connection string. If
`DATABASE_URL` is not set, it falls back to local SQLite at
`backend/data/processed/prospect_alpha.db`.

Local PostgreSQL configuration:

```env
DATABASE_URL=postgresql+psycopg://prospect_alpha:prospect_alpha@localhost:5432/prospect_alpha
```

Docker Compose already defines a PostgreSQL service:

```text
database: prospect_alpha
user: prospect_alpha
password: prospect_alpha
port: 5432
```

The local PostgreSQL image is built from:

```text
backend/docker/postgres/Dockerfile
```

It extends `pgvector/pgvector:0.8.2-pg16`, compiles `pg_jieba`, and copies
`backend/docker/postgres/init-extensions.sql` into Docker's initdb directory.
On a fresh database volume, the init SQL creates:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_jieba;
CREATE TEXT SEARCH CONFIGURATION jiebacfg (PARSER = jieba);
```

Important: files in `/docker-entrypoint-initdb.d` only run when PostgreSQL
initializes an empty data directory. If `postgres_data` already exists, rebuild
the image and recreate the database volume before expecting `pg_jieba` and
`jiebacfg` to appear automatically.

Start only PostgreSQL for local backend development:

```bash
cd backend
docker compose up -d --build postgres
```

Start the full backend stack:

```bash
cd backend
docker compose up --build
```

Application code should use `SessionLocal` or `session_scope()` from
`riskagent.database`. The same helpers work for SQLite tests and PostgreSQL
runtime. Table creation is currently handled by `init_db()`, which calls
`Base.metadata.create_all`.

Ingestion writes document metadata to PostgreSQL. Retrieval writes chunk rows and
vectors to PostgreSQL / pgvector. Parsing writes file artifacts only.

## Data Layout

Ingestion stores original prospectus PDFs under:

```text
backend/data/raw/prospectus/
  ysyy.pdf
```

Parsing stores MinerU processed outputs under:

```text
backend/data/processed/
  ysyy/
    ocr/
      images/
      ysyy_content_list.json
      ysyy_content_list_v2.json
      ysyy.md
      ysyy_middle.json
      ysyy_model.json
      ysyy_layout.pdf
      ysyy_origin.pdf
      ysyy_span.pdf
```

Parsing stores chunker outputs under:

```text
backend/data/processed/
  ysyy/
    chunks/
      block/
        chunks.jsonl
```

`ysyy` is the document key in this first version. Ingestion owns choosing this
key. It can later be replaced by a database `document_id` without changing the
chunk schema.

## MinerU Source Contract

The first version reads:

```text
backend/data/processed/{doc_key}/ocr/{doc_key}_content_list.json
```

This file is a JSON array. MinerU items have fields such as:

```json
{
  "type": "text",
  "text": "...",
  "bbox": [55, 98, 247, 122],
  "page_idx": 0
}
```

Image items include:

```json
{
  "type": "image",
  "img_path": "images/example.jpg",
  "image_caption": [],
  "image_footnote": [],
  "bbox": [0, 168, 1000, 708],
  "page_idx": 0
}
```

Table items include:

```json
{
  "type": "table",
  "img_path": "images/example.jpg",
  "table_caption": [],
  "table_footnote": [],
  "table_body": "<table>...</table>",
  "bbox": [112, 113, 878, 393],
  "page_idx": 17
}
```

Important distinction:

- `page_idx`, `bbox`, `type`, `text`, `img_path`, and `table_body` are MinerU
  source fields.
- `source_item_indices`, `bbox_refs`, `merged_bboxes`, `chunk_id`, and
  `image_path` are fields generated by ProspectAlpha during chunking.

## PDF Processor Design

The PDF processor belongs to parsing. It is responsible for producing or reading
MinerU output for an already registered document.

Current module:

```text
backend/src/riskagent/parsing/pdf_parser.py
```

Responsibilities:

- Receive the source PDF path from ingestion, usually
  `backend/data/raw/prospectus/{doc_key}.pdf`.
- Locate the MinerU output directory under
  `backend/data/processed/{doc_key}/ocr`.
- Use existing MinerU output when `reuse_existing` is enabled.
- Run MinerU when output is missing and the implementation supports execution.
- Return the path to `{doc_key}_content_list.json` and the raw MinerU output
  directory.

Factory shape:

```python
processor = PdfProcessorFactory().create("mineru")
result = processor.process(request)
```

Default processor:

```text
mineru
```

Future processors can implement different parsing tools or custom logic without
changing the chunker.

## Chunker Design

The chunker converts MinerU source items into unified chunks.

Current module:

```text
backend/src/riskagent/parsing/chunker.py
```

Factory shape:

```python
chunker = ChunkerFactory().create("block")
result = chunker.chunk(request)
```

Default chunker:

```text
block
```

The block chunker handles different MinerU item types as follows:

- `text`: may merge multiple adjacent text items into one chunk. A single
  oversized text item is split so text chunks respect `max_chars`.
- `table`: small MinerU table items become one table chunk. Oversized table
  HTML is split by table rows so table chunks respect `max_chars`.
- `image`: one MinerU image item becomes one image chunk after VLM description.
- `table` and `image` are hard chunk boundaries. If the chunker is currently
  buffering text and then sees a table or image, it must close the current text
  chunk before emitting the table or image chunk.
- Empty table items that immediately follow a non-empty table are treated as
  continuation bboxes for a cross-page table. They do not become empty chunks;
  their `source_item_index` and bbox are attached to the previous table chunk
  group.

Skip types for the first version:

```text
header
footer
page_number
```

Other types such as `chart`, `equation`, `code`, and `list` can be enabled by
configuration later.

## Chunk Configuration

Chunking behavior should be configurable outside the code. A future config file
can look like:

```yaml
pdf_processor:
  default: mineru
  mineru:
    method: ocr
    backend: pipeline
    lang: chinese_cht
    reuse_existing: true

chunker:
  default: block
  block:
    max_chars: 1200
    min_chars: 1
    include_types:
      - text
      - table
      - image
    skip_types:
      - header
      - footer
      - page_number
    image_context_window_blocks: 3
    text:
      merge_adjacent: true
    image:
      require_description: true
      on_description_error: skip
      description_source: vlm
      prompt_version: image-description-v1
```

`max_chars` controls embeddable text size for text and table chunks. If multiple
text items are merged and the next item would exceed `max_chars`, the chunker
closes the current text chunk and starts another one. If a single text item is
larger than `max_chars`, it is split directly. If table text generated from
MinerU HTML is larger than `max_chars`, it is split by `<tr>` rows where
possible, with a plain text fallback for unusually long rows.

## Unified Chunk Schema

All text, table, and image chunks use one schema. Type-specific fields can be
empty.

```json
{
  "chunk_id": "ysyy_chunk_000001",
  "doc_key": "ysyy",
  "chunk_type": "text",
  "text": "...",
  "page_idx": 8,
  "source_file": "backend/data/processed/ysyy/ocr/ysyy_content_list.json",
  "source_item_indices": [120, 121, 122],
  "bbox_refs": [
    {
      "page_idx": 8,
      "bbox": [110, 698, 887, 878],
      "source_item_index": 120
    }
  ],
  "merged_bboxes": [
    {
      "page_idx": 8,
      "bbox": [110, 698, 887, 878]
    }
  ],
  "image_path": null,
  "mineru_img_path": null,
  "table_html": null,
  "metadata": {}
}
```

Field notes:

- `chunk_id`: generated by ProspectAlpha for the final embeddable record.
- `doc_key`: human-readable document key, initially the PDF stem. It is used
  for deterministic file layout such as `data/processed/{doc_key}` and for
  debugging. It is not the primary retrieval isolation key.
- `chunk_type`: usually `text`, `table`, or `image`.
- `text`: the single field used for embedding.
- `page_idx`: the main page index for navigation. This keeps MinerU's 0-based
  value. Frontend display can add 1 when needed.
- `source_file`: full project-relative path to the MinerU content list.
- `source_item_indices`: JSON array indices from MinerU `content_list.json`.
  This is generated by `enumerate(content_list)` and is not a MinerU field.
- `bbox_refs`: original bboxes for precise frontend highlighting.
- `merged_bboxes`: bbox unions grouped by `page_idx` for coarse navigation or
  large highlight boxes.
- `image_path`: full project-relative path under `backend/data/processed`.
- `mineru_img_path`: original relative `img_path` from MinerU.
- `table_html`: raw table HTML from MinerU `table_body`.
- `metadata`: type-specific details such as VLM model and prompt version.

## Text Chunking

Text items can be merged because MinerU may split a paragraph into several
nearby text items.

Rules:

- Merge adjacent `text` items when enabled.
- Do not merge across skipped structural boundaries if later logic adds such
  boundaries.
- Do not merge across `table` or `image` items. These visual blocks are
  boundaries even when the current text chunk is shorter than the configured
  target size.
- Keep all source item indices in `source_item_indices`.
- Keep every original bbox in `bbox_refs`.
- Build `merged_bboxes` by grouping `bbox_refs` by `page_idx`.
- Do not merge bboxes across pages.

The reason for treating table and image as hard boundaries is evidence
traceability: a text chunk whose `merged_bboxes` jumps over an intervening image
or table becomes awkward for frontend highlighting. Short text chunks are
acceptable in this case. Later retrieval can improve recall by retrieving
neighbor chunks or by adding a small text-only parent context, but the stored
source chunk should keep its own bbox span visually coherent.

Example:

```text
text A -> image X -> text B
```

must produce:

```text
text chunk(A), image chunk(X), text chunk(B)
```

It must not produce one text chunk containing `A + B`, because that would make
`merged_bboxes` cross the image region.

Merged bbox calculation per page:

```text
x0 = min(all x0)
y0 = min(all y0)
x1 = max(all x1)
y1 = max(all y1)
```

Text chunk example:

```json
{
  "chunk_type": "text",
  "text": "merged text...",
  "page_idx": 8,
  "source_item_indices": [120, 121, 122],
  "bbox_refs": [
    {"page_idx": 8, "bbox": [110, 698, 887, 878], "source_item_index": 120},
    {"page_idx": 8, "bbox": [110, 85, 885, 220], "source_item_index": 121}
  ],
  "merged_bboxes": [
    {"page_idx": 8, "bbox": [110, 85, 887, 878]}
  ]
}
```

## Table Chunking

Simple table items are one-to-one:

```text
one MinerU table item -> one table chunk
```

Large table items are split:

```text
one oversized MinerU table item -> many table chunks
```

The chunk should include:

- `chunk_type = "table"`
- `table_html = item["table_body"]`
- `text` generated from table HTML, table caption, or a later table summarizer
- `image_path` resolved from MinerU `img_path` when present
- `mineru_img_path` preserving the original `img_path`
- one `source_item_indices` entry
- one `bbox_refs` entry

Table chunks can be embedded together with text and image chunks because they
share the same `text` field.

Large table splitting rules:

- Generate table text from caption, HTML rows, and footnote.
- Keep each emitted table chunk at or below `max_chars` when possible.
- Prefer splitting at `<tr>` boundaries so row content is not mixed arbitrarily.
- If a single row is longer than `max_chars`, split that row as plain text.
- Preserve local table traceability in every emitted part:
  `source_item_indices`, `bbox_refs`, `merged_bboxes`, and
  `metadata.table_part_index/table_part_count`.
- Preserve full-table group traceability in metadata:
  `metadata.table_group_source_item_indices` and
  `metadata.table_group_bbox_refs`.
- Store `table_html` on the first part, or on the only part when the table is
  not split. Later parts can keep `table_html = null` to avoid duplicating very
  large HTML in every row.
- Repeat inferred table header text in each split part when a table header can
  be detected. This helps retrieval because a later table part should still
  carry column meaning such as `候选产品 | 机制 | 主要适应症`.
- If a cross-page table has more page bboxes than text parts, split further so
  each page bbox can have at least one local table chunk.

Cross-page table continuation:

MinerU can emit a non-empty table item for the first page of a large table and
then emit later table items with only `page_idx` and `bbox`, without
`table_body`, `img_path`, caption, or footnote. These later items are not
standalone empty tables. They are continuation layout blocks for the previous
cross-page table.

The chunker handles this by:

- Not emitting a chunk for a table item whose generated text is empty.
- Treating consecutive empty table items after a non-empty table as continuation
  pages for that table.
- Splitting the table into enough parts to cover continuation page bboxes when
  possible.
- Assigning each emitted table part a local `page_idx`, `bbox_refs`, and
  `merged_bboxes` instead of attaching every page bbox to every chunk.
- Recording full-table group refs in metadata so the relationship can still be
  reconstructed.
- Recording continuation indices in
  `metadata.continuation_source_item_indices`.

Example from `ysyy_content_list.json`:

```text
292: table, page_idx=42, table_body length=11155
295: table, page_idx=43, bbox only
298: table, page_idx=44, bbox only
301: table, page_idx=45, bbox only
304: table, page_idx=46, bbox only
307: table, page_idx=47, bbox only
310: table, page_idx=48, bbox only
313: table, page_idx=49, bbox only
```

This should become several non-empty table chunks split from item `292`. Each
chunk should carry a local page bbox, for example:

```text
part 1 -> page_idx=42, source_item_indices=[292], bbox source item=292
part 2 -> page_idx=43, source_item_indices=[292, 295], bbox source item=295
part 3 -> page_idx=44, source_item_indices=[292, 298], bbox source item=298
```

The full cross-page table relation is kept in:

```json
{
  "table_group_source_item_indices": [292, 295, 298, 301, 304, 307, 310, 313],
  "continuation_source_item_indices": [295, 298, 301, 304, 307, 310, 313]
}
```

This gives the frontend a precise default jump target for a retrieved chunk
while still preserving the fact that the chunk belongs to a larger cross-page
table.

Table bbox fallback order:

1. Use the table or continuation item's own MinerU `bbox`.
2. If a table/continuation item has no bbox, infer a same-page content area from
   the union of other MinerU bboxes on that page.
3. If no same-page bboxes exist, leave `bbox_refs` empty and record
   `metadata.bbox_source = "missing_bbox"` rather than inventing a misleading
   highlight.

`metadata.bbox_source` should identify how reliable the local bbox is:

```text
mineru_bbox
inferred_content_area
missing_bbox
```

## Image Chunking and VLM

Image items are one-to-one:

```text
one MinerU image item -> one image chunk
```

Image chunks require a textual description before embedding. Parsing owns the
chunking decision and calls a provider-owned VLM client to obtain the
description.

Pipeline:

```text
MinerU image item
  -> resolve image_path
  -> collect same-page nearby text context
  -> VLM image describer
  -> text description
  -> image chunk
  -> embedding
```

Image chunk requirements:

- `chunk_type = "image"`
- `image_path` must be the full project-relative path, for example:
  `backend/data/processed/ysyy/ocr/images/example.jpg`
- `mineru_img_path` keeps the original MinerU value:
  `images/example.jpg`
- `text` is the VLM-generated description.
- `metadata.description_source = "vlm"`
- `metadata.image_context` records same-page nearby text that was sent to the
  VLM prompt.
- `metadata.vlm_model` records the model name when available.
- `metadata.prompt_version` records the VLM prompt version.

The first VLM prompt should include:

- page index from MinerU `page_idx`
- image caption and footnote
- nearby same-page text before and after the image block
- instructions to avoid inventing facts outside the image/context

If VLM generation fails:

- `on_description_error: skip` means do not emit that image chunk.
- `on_description_error: error` means fail the chunking job.

Very complex image blocks may later need a stronger VLM prompt, multiple
descriptions, or region-level splitting. The first version keeps one MinerU
image item as one chunk and stores the VLM description in the unified `text`
field.

## Retrieval Embedding

Embedding belongs to retrieval indexing. It should not need to know the original
chunk type. It reads only:

```python
embedding_text = chunk["text"]
```

Rules:

- Embed `text` for text chunks.
- Embed table text or table summary for table chunks.
- Embed VLM description for image chunks.
- Skip chunks whose `text` is empty unless a later implementation has a
  dedicated multimodal embedding path.

The same embedding pipeline can therefore handle:

```text
text chunk -> text embedding
table chunk -> text embedding
image chunk -> VLM description text embedding
```

## PostgreSQL Storage

PostgreSQL storage has two owners:

- Ingestion writes document metadata rows.
- Retrieval writes chunk, metadata, and vector rows.

Parsing does not write PostgreSQL rows. It produces MinerU output and chunk
artifacts on disk.

Suggested ingestion document metadata table shape:

```text
prospectus_documents
  document_id
  doc_key
  original_filename
  raw_pdf_path
  content_type
  file_sha256
  file_size_bytes
  source_url
  status
  created_at
```

Retrieval uses an index backend abstraction. The first backend is PostgreSQL /
pgvector, created through:

```python
indexer = create_chunk_indexer("postgres")
```

The PostgreSQL implementation lives in:

```text
backend/src/riskagent/retrieval/index.py
```

The default schema is `rag`. Tables should match ProspectAlpha's document and
chunk contracts, not a generic RAG schema.

Indexed document table:

```text
rag.prospectus_documents
  document_id
  doc_key
  original_filename
  raw_pdf_path
  processed_path
  content_type
  file_sha256
  file_size_bytes
  source_url
  created_at
```

Indexed chunk table:

```text
rag.prospectus_chunks
  chunk_id
  document_id
  doc_key
  chunk_type
  text
  embedding VECTOR(dim)
  content_tsv TSVECTOR
  page_idx
  source_file
  source_item_indices JSONB
  bbox_refs JSONB
  merged_bboxes JSONB
  image_path
  mineru_img_path
  table_html
  metadata
  created_at
```

Chunk columns preserve ProspectAlpha traceability directly:

- `doc_key`
- `chunk_type`
- `page_idx`
- `source_file`
- `source_item_indices`
- `bbox_refs`
- `merged_bboxes`
- `image_path`
- `mineru_img_path`
- `table_html`

Chunk metadata stores type-specific extensions:

- VLM metadata such as `description_source` and `image_context`

The retrieval writer should:

1. Read `chunks.jsonl`.
2. Validate each chunk has non-empty `text`.
3. Generate embedding from `chunk.text`.
4. Insert or upsert the chunk row into PostgreSQL.
5. Use pgvector for vector search and PostgreSQL full-text search for lexical
   matching. Chinese full-text search requires `pg_jieba` and `jiebacfg`; the
   indexer should fail fast if the extension or config is missing.

Retrieval has three explicit entrypoints:

- `retrieval/vector.py`: vector search using `ORDER BY embedding <=> query_vector`.
- `retrieval/full_text.py`: full-text search using `content_tsv @@ plainto_tsquery(...)`.
- `retrieval/hybrid.py`: hybrid search combining vector distance and full-text rank.

Every retrieval entrypoint must accept `document_id` and use it as the required
filter for RAG over a single prospectus. `doc_key` is not a tokenizer workaround
and should not be part of the retrieval API. It remains only as a file-layout and
debugging field for paths such as `data/processed/{doc_key}`.

There is no application-side tokenizer fallback in retrieval. Full-text and
hybrid search rely on PostgreSQL `content_tsv @@ plainto_tsquery('jiebacfg', ...)`.
If `pg_jieba` or `jiebacfg` is unavailable, initialization should raise an error
instead of silently using `simple` or `ILIKE` matching.

## Issues Found and Current Decisions

This section records implementation problems found while testing the `ysyy`
MinerU output and the decisions made so later changes do not reintroduce them.

### Empty Table Chunks

Observed problem:

- Some table chunks had empty `text`, for example source item indices
  `295/298/301/304/307/310`.
- At first glance these looked like independent tables that MinerU failed to
  parse.
- Checking `ysyy_content_list_v2.json` showed the same table blocks still had
  empty `html` and only an `image_source.path` directory, not a concrete image.
- Checking `ysyy.md` and neighboring `content_list.json` items showed the real
  table text existed in a previous very large table item.

Root cause:

- MinerU represents some large cross-page tables as one non-empty table item
  followed by several bbox-only table continuation items.
- The continuation items are useful for page navigation and highlighting, but
  they do not contain embeddable text by themselves.

Decision:

- Do not emit empty table chunks.
- Treat bbox-only continuation table items as page-local layout evidence for
  the previous non-empty table group.
- Keep each emitted table part's `bbox_refs` and `merged_bboxes` local to the
  page assigned to that part.
- Keep full cross-page evidence in `metadata.table_group_source_item_indices`,
  `metadata.table_group_bbox_refs`, and
  `metadata.continuation_source_item_indices`.
- Retrieval still skips any chunk with empty `text`; after this fix, empty
  table chunks should not be produced by the block chunker.

### Oversized Table Blocks

Observed problem:

- Some MinerU table items have very large `table_body` HTML. In `ysyy`, item
  `292` had more than 11k characters of table HTML.
- Keeping this as one table chunk violates the configurable chunk size and can
  produce poor embeddings.

Decision:

- Split large table text by HTML table rows.
- Preserve the original source item on every emitted table part, and attach only
  the local assigned continuation bbox when the part maps to a continuation
  page.
- Record `metadata.table_part_index`, `metadata.table_part_count`, and
  `metadata.table_split`.
- Repeat detected table header text in split parts so each retrieved part keeps
  column meaning.
- This keeps retrieval chunks small while giving the frontend a precise default
  jump/highlight page for each result.

### Oversized Single Text Items

Observed problem:

- Most text chunks are controlled by merge logic, but a single MinerU text item
  can itself exceed `max_chars`.

Decision:

- Split a single oversized text item into multiple text chunks.
- Each split chunk keeps the same `source_item_index` and bbox. This is less
  precise than character-level bboxes, but it preserves source traceability and
  enforces the embedding size contract.

### Image Chunks Without VLM

Observed problem:

- If VLM is disabled, some image items only have empty caption/footnote and
  therefore produce empty image chunk text.

Decision:

- Retrieval/indexing skips chunks with empty `text`.
- In production document processing, image VLM should be enabled when image
  chunks are meant to be searchable.
- Later work can add crop-based enrichment for table/image regions that MinerU
  does not export as concrete image files.

### PostgreSQL and Chinese Full-Text Search

Observed problem:

- Plain PostgreSQL images do not include `vector` or `pg_jieba`.
- Existing PostgreSQL volumes do not rerun `/docker-entrypoint-initdb.d` scripts
  after the image is rebuilt.

Decision:

- The local postgres image builds from `pgvector/pgvector:0.8.2-pg16`, compiles
  `pg_jieba`, and installs init SQL for `vector`, `pg_jieba`, and `jiebacfg`.
- Existing volumes must either run the init SQL manually or be recreated.
- Retrieval does not fall back to `simple` or `ILIKE` matching. Missing
  `pg_jieba` / `jiebacfg` is a configuration error and should stop indexing or
  retrieval setup immediately.

## Flow Audit

The current implemented flow matches the intended module boundaries:

- `documents.py` coordinates the workflow through
  `process_prospectus_document(...)`.
- `ingestion` registers original PDFs and stores prospectus metadata.
- `parsing/pdf_parser.py` owns MinerU execution or reuse of existing MinerU
  output.
- `parsing/chunker.py` owns conversion from MinerU items to unified chunks,
  including text/table splitting, table continuation bboxes, and image VLM
  enrichment.
- `providers` owns DashScope VLM and embedding clients plus provider
  configuration loading.
- `retrieval/index.py` owns embedding, PostgreSQL schema setup, chunk upsert,
  and vector/full-text/hybrid search.

Current caveats:

- Ingestion currently persists `ProspectusDocumentRow` through SQLAlchemy, while
  retrieval mirrors indexed document metadata into `rag.prospectus_documents`.
  This is acceptable for the first index backend, but later we should decide
  whether `rag.prospectus_documents` remains an index-owned mirror or becomes
  the single canonical prospectus table.
- `content_list_v2.json`, `middle.json`, and `ysyy.md` are useful diagnostics,
  but the implemented chunker reads only `{doc_key}_content_list.json`.
  Extending the parser to fall back to `middle.json` or markdown should be a
  deliberate parser/chunker version change.
- Cross-page table continuation detection is intentionally local: it attaches
  empty table items only to the immediately preceding non-empty table group.
  If future MinerU output inserts unrelated items between a table and its
  continuation pages, this heuristic may need a stronger page/bbox based merge.
- Split text chunks reuse the same bbox for each part because MinerU does not
  provide character-level bbox ranges for the split. Frontend highlighting will
  still jump to the correct source block, but it cannot highlight only the exact
  substring.

## End-to-End Flow

```text
backend/src/riskagent/documents.py
  -> ingestion registers backend/data/raw/prospectus/ysyy.pdf
  -> parsing PdfProcessorFactory.create("mineru")
  -> parsing reads or writes backend/data/processed/ysyy/ocr/ysyy_content_list.json
  -> parsing ChunkerFactory.create("block")
  -> parsing creates text/table/image chunks
  -> providers VLM client describes image chunks when requested by parsing
  -> parsing writes backend/data/processed/ysyy/chunks/block/chunks.jsonl
  -> retrieval ChunkIndexer reads chunks.jsonl
  -> providers embedding client embeds chunk.text
  -> retrieval writes PostgreSQL / pgvector
```

`documents.py` is the workflow coordinator. The modules it calls still own their
own behavior and storage contracts.

## Open Implementation Notes

- `source_item_indices` are generated from the array position in
  `content_list.json`; they are not written by MinerU.
- `image_path` must not be the raw MinerU relative path. It should include
  `backend/data/processed/{doc_key}/ocr/`.
- `page_idx` remains 0-based to match MinerU and frontend PDF navigation.
- Bbox coordinates remain MinerU's original coordinate system.
- Text, table, and image chunks must share one schema so embedding and storage
  stay simple.
