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
- Keep `process_prospectus_document(...)` as the canonical prospectus RAG
  pipeline entrypoint. Validation scripts should call it instead of duplicating
  the workflow.
- Pass stable identifiers and paths between modules, especially `document_id`,
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

`backend/scripts/run_ysyy_pipeline.py` is only an end-to-end smoke runner for the
sample `ysyy` document. It may add progress logging and a sample retrieval query,
but it must call `process_prospectus_document(...)` for the actual pipeline.

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
- Decide the database `document_id` and optional human-readable `doc_key`.
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

- Consume an already registered `document_id`, optional `doc_key`, and raw PDF
  path from ingestion.
- Produce or read MinerU output under `backend/data/processed/{document_id}/ocr`.
- Read the MinerU `content_list.json` artifact for that document.
- Convert MinerU items into unified text, table, and image chunks.
- Preserve frontend location metadata: `page_idx`, `bbox_refs`, and
  `merged_bboxes`.
- Resolve MinerU relative `img_path` values into project-relative `image_path`
  values under `backend/data/processed/{document_id}/ocr`.
- Request VLM descriptions for image chunks before embedding. The VLM client
  itself belongs to providers; parsing only owns when and how the description is
  attached to the chunk.
- Ensure emitted text, table, and image chunks all have a unified `text` field
  that retrieval can embed later.
- Write chunk artifacts such as
  `backend/data/processed/{document_id}/chunks/block/chunks.jsonl`.

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
  {document_id}.pdf
```

The original uploaded filename, for example `ysyy.pdf`, is metadata. It should
not decide the processed artifact directory.

Parsing stores MinerU processed outputs under:

```text
backend/data/processed/
  {document_id}/
    ocr/
      images/
      {document_id}_content_list.json
      {document_id}_content_list_v2.json
      {document_id}.md
      {document_id}_middle.json
      {document_id}_model.json
      {document_id}_layout.pdf
      {document_id}_origin.pdf
      {document_id}_span.pdf
```

Parsing stores chunker outputs under:

```text
backend/data/processed/
  {document_id}/
    chunks/
      block/
        image_descriptions.json
        chunks.jsonl
```

`chunks.jsonl` is the final chunk artifact. Each line is one unified chunk JSON
object, so retrieval can stream or batch-read it without loading unrelated
artifacts.

`image_descriptions.json` is a resumable VLM cache for image chunks. Each image
description is written immediately after a successful VLM call. If chunking is
interrupted during image enrichment, the next run reuses cached descriptions and
continues without paying for already completed image calls again.

`document_id` is the stable processed directory name. The original file name
and human-readable `doc_key` stay in metadata and database rows. Avoid directory
names with a PDF extension, such as `processed/ysyy.pdf/ocr`, because they make
a directory look like a source file and become confusing in scripts.

The implementation should not read `processed/{doc_key}/...` or MinerU-created
`processed/{filename}.pdf/...` paths as an automatic compatibility fallback. If
legacy outputs exist, migrate them once into `processed/{document_id}/...` before
running the pipeline.

## MinerU Source Contract

The parser/chunker reads:

```text
backend/data/processed/{document_id}/ocr/{document_id}_content_list.json
```

Legacy MinerU files such as `ysyy.pdf_content_list.json` should be renamed or
copied during migration to the `document_id` naming convention before parsing.
The runtime parser should fail fast when the expected `document_id` content-list
path is missing.

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
  `backend/data/raw/prospectus/{document_id}.pdf`.
- Locate the MinerU output directory under
  `backend/data/processed/{document_id}/ocr`.
- Use existing MinerU output when `reuse_existing` is enabled.
- Run MinerU when output is missing and the implementation supports execution.
- Return the path to the document's MinerU `content_list.json` and the raw MinerU output
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
  "chunk_id": "doc_269f78d05439_chunk_000001",
  "document_id": "doc_269f78d05439",
  "doc_key": "ysyy",
  "chunk_type": "text",
  "text": "...",
  "page_idx": 8,
  "source_file": "backend/data/processed/doc_269f78d05439/ocr/doc_269f78d05439_content_list.json",
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
- `document_id`: stable database identifier. It is the processed directory name
  and the required retrieval isolation key.
- `doc_key`: human-readable document key, initially often derived from the PDF
  stem. It is a label/debugging field only and is not the primary retrieval
  isolation key.
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
- `image_path`: full project-relative path under
  `backend/data/processed/{document_id}/ocr`.
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
  `backend/data/processed/{document_id}/ocr/images/example.jpg`
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

The current implementation writes `chunks.jsonl` after the full chunk pass has
completed. VLM image descriptions are checkpointed earlier in
`image_descriptions.json`, so the expensive part can resume. If later text/table
chunking becomes long-running too, the chunker can be extended to stream chunks
to a temporary JSONL and atomically rename it when complete.

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

DashScope embedding currently rejects batches larger than 10 input strings, so
the default PostgreSQL indexer embedding batch size is 10.

Retrieval has three explicit entrypoints:

- `retrieval/vector.py`: vector search using `ORDER BY embedding <=> query_vector`.
- `retrieval/full_text.py`: full-text search using `content_tsv @@ plainto_tsquery(...)`.
- `retrieval/hybrid.py`: hybrid search combining vector distance and full-text rank.

Every retrieval entrypoint must accept `document_id` and use it as the required
filter for RAG over a single prospectus. `doc_key` is not a tokenizer workaround
and should not be part of the retrieval API. It remains only as a human-readable
label and debugging field.

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
- `content_list_v2.json`, `middle.json`, and MinerU markdown output are useful
  diagnostics, but the implemented chunker reads only the resolved
  `content_list.json` path returned by the parser:
  `{document_id}_content_list.json`.
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
  -> ingestion registers backend/data/raw/prospectus/{doc_key}.pdf
  -> parsing PdfProcessorFactory.create("mineru")
  -> parsing reads or writes backend/data/processed/{document_id}/ocr/{document_id}_content_list.json
  -> parsing ChunkerFactory.create("block")
  -> parsing creates text/table/image chunks
  -> providers VLM client describes image chunks when requested by parsing
  -> parsing writes backend/data/processed/{document_id}/chunks/block/chunks.jsonl
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
  `backend/data/processed/{document_id}/ocr/`.
- MinerU source `page_idx` remains 0-based in the raw artifact. The chunker
  converts it once at the parsing boundary; PostgreSQL `page_idx`, API
  `page_number`, and PDF.js all use the same 1-based page number.
- Bbox coordinates use MinerU's normalized `1000 x 1000`, top-left-origin
  coordinate space. The frontend scales them into the current PDF viewport.
- Text, table, and image chunks must share one schema so embedding and storage
  stay simple.

## Main Agent Retrieval Entry Migration

The main analysis-agent workflow consumes the public PostgreSQL chunk retrieval
boundary owned by `retrieval`; no run-scoped retrieval implementation exists.

Migration scope:

- Export `prospectus_hybrid_search(...)` from `riskagent.retrieval` as the
  package-level hybrid chunk retrieval entrypoint.
- Pass the stable prospectus `document_id` through the analysis run and the
  dynamic Agent supervisor. Do not pass the complete prospectus text into each
  expert thread.
- Each expert builds retrieval queries from its strategic assignment, Domain
  Skill, and frozen risk rules, retrieves the relevant chunks, and evaluates
  only the retrieved source text.
- Persona skills must own non-overlapping rule families where the team contains
  multiple specialists in the same broad domain: business operations and R&D
  pipeline rules are split between their experts, while valuation/pricing and
  industry competition rules are split between the two market experts. This
  prevents duplicate searches from consuming the rule-coverage budget.
- Rule-specific compact queries and Traditional-Chinese disclosure aliases run
  before the full user requirement. Explicit counter-evidence such as “no
  material litigation” remains auditable instead of being discarded only
  because the corresponding risk did not trigger.
- Replacement experts use the same retrieval boundary as the initially
  selected experts.
- Evidence extraction reuses the same public retrieval entrypoint instead of
  querying a run-scoped search index.
- Prospectus parsing and chunk indexing remain owned by
  `process_prospectus_document(...)`; the analysis-agent workflow must not
  re-index locally constructed `document_blocks`.

`ChunkSearchResult` maps into analysis evidence as follows:

- `chunk_id` -> `document_block_id`
- `page_idx` -> `page_number` without an offset; both are 1-based
- the `merged_bboxes` entry matching `page_idx` -> `bbox`; fall back to the
  first merged bbox when no page-matched entry exists
- `text` -> evidence text
- `metadata.section_path` -> `section_path`, with `chunk_type` as the fallback
- `metadata.char_start`, `metadata.char_end`, and `metadata.source_hash` -> the
  corresponding traceability fields when present
- when `metadata.source_hash` is absent, hash the retrieved chunk text so
  evidence deduplication still has a stable source-content key

The migration changes retrieval orchestration and evidence data handling. It
does not change embedding, hybrid ranking, model-provider behavior, or report
generation contracts.

## Interactive Upload, PostgreSQL Retrieval, and Evidence Highlighting

The frontend `/intake` workflow uses the application document service:

```text
POST /documents/upload
  -> DocumentRepository.create_from_bytes(...)
  -> DocumentRepository.prepare_for_retrieval(document_id)
  -> PyMuPDF page/block parsing
  -> PostgresChunkIndexer.index_chunks(...)
  -> rag.prospectus_documents + rag.prospectus_chunks

POST /documents/{document_id}/ask
  -> prospectus_hybrid_search(document_id=...)
  -> streamed grounded answer + citations
```

Upload finishes parsing and PostgreSQL indexing before returning. Consequently,
`block_count` reports searchable blocks. Duplicate immutable files reuse their
document id and verify the existing PostgreSQL chunk count.

The ask endpoint accepts `question` and `top_k` (1 through 10), streams SSE
`delta` events, and finishes with a `done` event. Each citation contains the
`document_id`, 1-based `page_number`, `section_path`, snippet, and page-local
`bbox_regions`.

All application retrieval uses `prospectus_hybrid_search(...)` and
`PostgresChunkIndexer.hybrid_search(...)` with a required `document_id`. There
is no Qdrant provider, legacy `retrieval_index` ORM table, run-scoped index, or
cross-document fallback in the application runtime.

For the interactive upload path, bboxes are PyMuPDF PDF points with top-left
origin and downward-positive y. PDF.js receives the same 1-based page number,
and the viewer scales `[x0, y0, x1, y1]` directly by its render scale. Invalid
or empty bboxes do not create a fabricated highlight.

`merged_bboxes` and question citation regions are grouped by page before union:

```text
group by page_number
  -> x0=min(x0), y0=min(y0), x1=max(x1), y1=max(y1)
```

A bbox union must never span two pages. A multi-page chunk therefore produces
one region per page, and the viewer shows only the active page's region.

### Real-Scenario Acceptance (2026-07-15)

The flow was exercised with Docker PostgreSQL and a generated two-page PDF
through the real HTTP API. Upload produced six searchable chunks covering pages
1 and 2 in `rag.prospectus_chunks`. A real model question about supplier
concentration returned 82% for the top five suppliers and 41% for the largest
supplier, with citations to the matching page-2 block ids and PDF-point bboxes.

The frontend `/intake` page selected that exact `document_id`, displayed its six
indexed blocks, submitted the question to
`POST /documents/{document_id}/ask`, and rendered the streamed answer. Citation
chips are interactive: selecting the 82% citation opened page 2 at 125% zoom and
highlighted `[72.0, 148.175, 462.687, 163.289]`. The observed overlay
`left=87`, `top=182.219`, `width=494.359`, and `height=24.893` exactly matches
PDF-point scaling plus the viewer's three-pixel outline padding. Browser runtime
logs contained no warnings or errors.

The MinerU parser was separately started against a real PDF through page
rendering and model initialization, then intentionally stopped before the model
download/full parse completed. This confirms that the configured parsing path
can start without claiming a complete MinerU acceptance run.

Old Qdrant, run-scoped retrieval-index, and compatibility-facade code was
removed. Runtime retrieval now has one production boundary: PostgreSQL
`rag.prospectus_chunks`, always filtered by the required `document_id`. Parser
and QA tests explicitly assert that bbox unions are grouped by page and never
span pages.

The environment reported a PostgreSQL collation-version warning (database 2.41
versus container operating system 2.36). It did not affect this acceptance run, but the
local database should be refreshed during infrastructure maintenance.

### Existing Indexed Prospectus Discovery

The document API merges frontend-uploaded `documents` with registered
`prospectus_documents` that already have rows in `rag.prospectus_chunks`.
Registered indexed prospectuses are therefore selectable by the frontend
without copying, parsing, or embedding them again. Their canonical
`raw_pdf_path` is served by `/documents/{document_id}/file`, and existing chunks
are materialized as `DocumentBlock` values only when an analysis workflow asks
for them.

The workspace loads this merged document list on entry and allows a registered
prospectus to be selected before creating an analysis run. Upload deduplication
also checks registered documents by SHA-256, and the list prefers the canonical
registered/indexed document when an upload asset has the same file hash. This
prevents an already indexed prospectus from being presented as requiring a new
upload or a second parse. Local Vite origins on `localhost`, `127.0.0.1`, and
`0.0.0.0` are accepted on any port so failed CORS preflights cannot silently
turn populated backend responses into empty workspace state.

Legacy MinerU chunks are upgraded idempotently on first retrieval. Rows without
`metadata.page_number_base=1` receive a one-time `page_idx + 1` update in the
chunk row and both bbox JSON arrays, plus:

```json
{
  "bbox_coordinate_space": "normalized_1000",
  "page_number_base": 1
}
```

No text, embedding, ranking data, or source-item linkage changes during this
upgrade. New MinerU chunks write this metadata at chunk creation. PyMuPDF upload
chunks explicitly use `bbox_coordinate_space=pdf_points`.

The real pre-indexed `ysyy.pdf` was verified without rerunning MinerU or
embedding: `doc_269f78d05439`, 700 pages, and 1091 PostgreSQL chunks appeared in
the frontend. A question for the stock code returned `6855`; citation
`doc_269f78d05439_chunk_000001` opened PDF page 1 and highlighted normalized
bbox `[55, 98, 247, 157]` over the cover text `股份代号: 6855`. At a 744 by 992
pixel canvas, the observed padded overlay was left `37.922`, top `94.227`, width
`148.859`, and height `64.531`, matching normalized scaling exactly.

This implementation changes document/evidence data handling and evidence
traceability and adds grounded document QA. Generated-report contracts are
unchanged.

### Evidence Lifecycle and Inline PDF Review

Analysis evidence and accepted risk findings have different lifecycles. If the
red-team debate rejects a finding, that finding is removed from report and
module references, but its source evidence remains in the run evidence ledger
with `verifier_status=rejected_by_debate`. This preserves the audit trail without
presenting rejected conclusions as report findings. For analysis runs created
before this rule, `GET /analysis-runs/{run_id}/evidence` can reconstruct only
traceable candidates recorded by `evidence_found` events and matched to real
PostgreSQL chunks; unmatched events are omitted rather than synthesized.

Both the global evidence library and the per-analysis evidence page render the
selected PDF page directly in the right-hand panel. The viewer receives the
evidence `document_id`, one-based `page_number`, bbox, and explicit coordinate
space, then draws the overlay in either PDF points or normalized-1000 units.
The PDF.js loader supplies a compatibility implementation when a browser lacks
the newer static `URL.parse` API. PDF.js CMaps, standard fonts, and WASM files
are generated under `frontend/public/pdfjs` by the frontend postinstall script.

This behavior touches evidence data handling and traceability. Rejected evidence
is visible for audit, while report generation continues to reference accepted
findings and their approved evidence only.

### Asynchronous Upload Processing

`POST /documents/upload` no longer keeps the HTTP request open while MinerU
parses a prospectus. In PostgreSQL mode it validates and registers the original
PDF first, returns the canonical `document_id` with
`processing_status=queued`, and schedules the canonical MinerU -> chunker ->
PostgreSQL indexing pipeline as a background task. Duplicate file hashes reuse
the existing registered document; already indexed documents are returned as
`ready` and do not start MinerU again.

`GET /documents/{document_id}` exposes `queued`, `processing`, `ready`, or
`failed` state together with the current indexed block count. The frontend polls
this endpoint every three seconds, keeps navigation responsive, and disables
question submission and analysis creation until PostgreSQL contains searchable
chunks. A failed background job is visible to the user and remains recorded on
`prospectus_documents.status`; it is not silently replaced with the legacy
PyMuPDF retrieval path. Failed rows also persist a stable error code, a safe
user-facing message, and the number of processing attempts. The frontend shows
that diagnostic and offers `POST /documents/{document_id}/reparse`; duplicate
upload is no longer the recovery contract. `GET /documents/parser-health`
performs a non-networking MinerU/model-cache readiness check, and startup logs a
warning before the first user upload when model download is still required.
Local non-database tests retain the lightweight PyMuPDF
path only as an isolated test-mode boundary.

MinerU receives the extensionless stable `document_id` as `pdf_file_name`.
MinerU uses that value verbatim for its directory and file prefix, so parsing
must read `processed/{document_id}/ocr/{document_id}_content_list.json` and must
not append `.pdf`. Polling metadata and all vector/full-text/hybrid query paths
are read-only and instantiate the indexer with `ensure=False`; they never run
PostgreSQL extension/schema DDL. Trigger/function/schema creation belongs to
ingestion or startup only, avoiding `AccessExclusiveLock` deadlocks when several
experts search concurrently.

### Rule-aware Query Rewriting and Evidence Scores

Broad user instructions such as "review this prospectus for IPO risks" are not
sent directly to PostgreSQL. The strategist first assigns domain modules. Before
retrieval, each expert asks its LLM to rewrite the original user requirement,
assignment goal, expected outputs, priority rules, and deterministic seed terms
into its own atomic Queries. The code planner merges those expert-authored
Queries with validated Traditional-Chinese exact terms, negative-disclosure
phrases, English aliases, and a deterministic fallback. Query variants are short
and complementary instead of concatenating an entire category and several
trigger conditions into one embedding. Every
candidate finding is searched again with complementary exact-disclosure,
simplified/traditional Chinese, English terminology, report-period fact, and
counter-evidence queries. Short exact queries give PostgreSQL full-text ranking
more weight; longer semantic questions retain a larger vector component. Every
call remains scoped to one required `document_id`.

Rule keyword matching is recall-only and returns `candidate`, never a risk
finding. Generic adjectives such as `重大`, `显著`, `可能`, and `模糊` cannot
trigger a rule by themselves. An expert second pass must explicitly return
`triggered`, `not_triggered`, or `insufficient_evidence` after evaluating the
actual trigger condition. A confident statement that the trigger does not exist
is `not_triggered`, never a risk finding; missing or malformed expert verification
is fail-closed as `insufficient_evidence`.

When modules overlap on a frozen rule, the supervisor keeps the strongest
auditable conclusion instead of whichever result arrived first. A direct
`triggered` or `not_triggered` conclusion outranks `insufficient_evidence`, then
evidence presence and confidence break ties. This also preserves a valid
replacement result when an earlier module could not evaluate the same rule.

When the expert marks `needs_more_evidence`, its A2A request includes evidence
gaps and attempted queries. The strategist returns actionable suggested queries,
the expert performs one bounded supplemental retrieval round, and the new chunks
are evaluated again before the finding is finalized. Cross-module review consumes
the originating finding summaries and executes rule-specific counter-evidence
queries; it no longer reports unrelated candidates from the reviewer's own
module. Every planned query, retrieval round, and retrieved chunk id is emitted
as a runtime event for audit. Initial intent/Query budgets and rule-coverage
budgets are independently configurable through `AGENT_RETRIEVAL_*` environment
variables. Retrieval-completed events also carry the top
candidate chunks with document/page/bbox/text and raw retrieval score. The live
UI labels these as candidate source blocks; admitted evidence remains separate
and uses the rule-aware relevance score. An explicit empty rule list remains
empty, which keeps evidence-only roles from silently evaluating the full rule
set. The quality arbiter explicitly owns the `textual_nlp` rules instead of
receiving an accidental empty rule list.

Evidence stores two distinct values. `retrieval_score` is the raw PostgreSQL
hybrid ranking signal and is not a probability. `relevance_score` is a separate
rule-aware score based on specific risk anchors, target-section matches,
numeric evidence requirements, and the raw ranking signal. The frontend labels
the latter as evidence relevance instead of confidence. Finding confidence is
kept separate. Its final value is calibrated from expert judgment and the
strongest admitted evidence relevance: supported findings are capped at 0.95,
weakly supported findings at 0.69, and evidence-gate failures below 0.50.
Finding confidence never substitutes for evidence admission.

Report synthesis receives only findings and evidence that passed the evidence
gate. Module summaries are regenerated from admitted identifiers so rejected
candidate counts cannot leak into the report. If no finding is admitted, the
system emits a deterministic `未评级（无已验证结论）` report and states that absence
of verified evidence does not prove absence of risk.

Each rule sent to expert second-pass judgment now receives a separate,
rule-aware evidence pack containing up to four reranked source blocks. Numeric,
ratio, and trend rules can therefore combine tables and explanatory text across
pages instead of being decided from the first global snippet. A bounded
rule-coverage round distributes Queries across previously unevaluated rules;
duplicate Queries from earlier rounds are not executed again. Unresolved and
not-evaluated rules are retained as `screening_observations`. They may appear in
the report only as concrete retrieval coverage, evidence gaps, and manual-review
directions; they can never be promoted to formal findings.

`GET /analysis-runs/{run_id}/evidence` also reconstructs persisted top retrieval
chunks as `retrieval_candidate` records. This keeps the evidence browser useful
when every candidate is rejected by the formal gate. Candidate records retain
the raw `retrieval_score`, always have zero `relevance_score`, and are labelled
unverified by the frontend. Business operations and technology/pipeline personas
use distinct rule domains, preventing the previous duplicate scan of the same
business rules. Internal report synthesis continues to consume only
`run.evidence`, so exposing retrieval audit records does not weaken evidence
admission or change a report's risk conclusion.

Local MinerU parsing runs in a dedicated subprocess with a database heartbeat.
Closing the browser does not cancel parsing, and restarting the API does not
delete the raw PDF or mistake a live child process for a failed task. A stale
heartbeat changes `processing` to recoverable `interrupted`; retrying uses the
saved PDF through `POST /documents/{document_id}/reparse`. Duplicate uploads
return the existing indexed document, requeue interrupted/failed documents, and
do not launch a second parser for an already-processing document. Concurrency,
heartbeat, timeout, CPU threads, and device selection are environment settings.
Apple Silicon uses `mps` with PyTorch CPU fallback for unsupported operators;
NVIDIA deployments use `cuda` or `cuda:<index>`.

The existing `ysyy.pdf` PostgreSQL index was used as a no-reparse regression
fixture. Targeted searches kept the document boundary, rejected customer
concentration evidence when no specific passage was retrieved, and recovered
traditional-Chinese continuing-operation and connected-transaction passages
with the configured `text-embedding-v4` provider. Representative raw hybrid
scores around `0.20-0.39` became rule-aware relevance scores around `0.65-0.86`;
the raw values remain visible for diagnostics and are never treated as
probabilities.

All embedding entry points load `backend/.env` before resolving provider keys.
`DASHSCOPE_API_KEY` is a supported fallback for `EMBEDDING_API_KEY`, so API,
worker, CLI, and regression processes consistently use the configured
`text-embedding-v4` provider instead of silently mixing dense indexed vectors
with hashing query vectors. Application retrieval has no hashing fallback: a
missing provider key fails startup and retrieval with an explicit configuration
error. `HashingEmbeddingClient` remains available only for explicit unit-test
injection.
