"""PostgreSQL/pgvector hybrid retrieval for indexed prospectus chunks."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

from riskagent.database import BACKEND_ROOT
from riskagent.retrieval.index import ChunkIndexError, ChunkSearchResult, PostgresChunkIndexer

logger = logging.getLogger(__name__)

DEFAULT_RAG_CONFIG_PATH = BACKEND_ROOT / "configs" / "rag.yaml"

# Fallback defaults when config file is missing or lacks retrieval section
_DEFAULT_VECTOR_WEIGHT = 0.75
_DEFAULT_FULLTEXT_WEIGHT = 0.25

_QUERY_SPLIT_RE = re.compile(r"[\s,，、;；/|()（）\[\]]+")


def _load_retrieval_config(
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load the retrieval.hybrid_search section from rag.yaml.

    Returns an empty dict when the file or section is absent so callers
    can fall back to hard-coded defaults (backward compatible).
    """
    path = Path(
        config_path or os.getenv("RAG_CONFIG_PATH") or DEFAULT_RAG_CONFIG_PATH
    )
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            return {}
        retrieval = raw.get("retrieval")
        if not isinstance(retrieval, dict):
            return {}
        return retrieval.get("hybrid_search") or {}
    except Exception:
        logger.warning("Failed to load retrieval config from %s", path, exc_info=True)
        return {}


def _compute_adaptive_weights(
    query: str,
    config: dict[str, Any],
    default_vector_weight: float = _DEFAULT_VECTOR_WEIGHT,
    default_fulltext_weight: float = _DEFAULT_FULLTEXT_WEIGHT,
) -> tuple[float, float, float]:
    """Adjust search weights based on query characteristics.

    - Numeric-heavy queries (containing lots of numbers, percentages, financial
      figures) → boost fulltext weight for precise matching.
    - Conceptual/semantic queries (longer text, fewer numbers)
      → boost vector weight for semantic similarity.

    Returns ``(vector_weight, fulltext_weight, numeric_ratio)`` so callers can
    log the diagnostic ratio.
    """
    adaptive_cfg = config.get("adaptive") or {}
    if not adaptive_cfg.get("enabled", False):
        return default_vector_weight, default_fulltext_weight, 0.0

    # Detect numeric density
    numeric_chars = sum(
        1 for c in query if c.isdigit() or c in "%¥$€£,."
    )
    numeric_ratio = numeric_chars / max(len(query), 1)

    threshold = float(adaptive_cfg.get("numeric_char_ratio_threshold", 0.3))

    if numeric_ratio > threshold:
        # Numeric-heavy query → boost fulltext
        boost = float(adaptive_cfg.get("numeric_query_fulltext_boost", 0.15))
        vw = max(0.0, default_vector_weight - boost)
        fw = min(1.0, default_fulltext_weight + boost)
        return vw, fw, numeric_ratio
    elif len(query) > 50 and numeric_ratio < 0.1:
        # Long semantic query → boost vector
        boost = float(adaptive_cfg.get("semantic_query_vector_boost", 0.10))
        vw = min(1.0, default_vector_weight + boost)
        fw = max(0.0, default_fulltext_weight - boost)
        return vw, fw, numeric_ratio

    return default_vector_weight, default_fulltext_weight, numeric_ratio


def _is_postgres_unavailable(exc: BaseException) -> bool:
    """True when the failure means 'no usable PostgreSQL', not a data error."""
    if isinstance(exc, ChunkIndexError):
        return True
    return type(exc).__module__.startswith("psycopg")


def _local_keyword_search(
    *,
    query: str,
    document_id: str,
    limit: int,
) -> list[ChunkSearchResult]:
    """Degraded keyword retrieval over the document store (SQLite fallback mode).

    Used only when the PostgreSQL/pgvector index is unreachable. Results keep the
    same ChunkSearchResult contract and are marked ``degraded`` in metadata so
    downstream verifiers can treat scores with appropriate caution.
    """
    # Imported lazily: riskagent.documents imports this package at module load.
    from riskagent.documents import get_default_document_repository

    blocks = get_default_document_repository().get_blocks(document_id)
    terms = [term for term in _QUERY_SPLIT_RE.split(query) if term]
    scored: list[tuple[int, Any]] = []
    for block in blocks:
        text = str(block.text or "")
        if not text.strip():
            continue
        hits = sum(1 for term in terms if term in text)
        if hits > 0:
            scored.append((hits, block))
    scored.sort(key=lambda item: item[0], reverse=True)
    denominator = max(len(terms), 1)
    return [
        ChunkSearchResult(
            chunk_id=str(block.block_id),
            document_id=document_id,
            doc_key="",
            chunk_type=str(block.block_type or "text"),
            text=str(block.text or ""),
            page_idx=int(block.page_number or 1),
            source_file="",
            source_item_indices=[],
            bbox_refs=[],
            merged_bboxes=[{"page_idx": int(block.page_number or 1), "bbox": list(block.bbox or [])}]
            if block.bbox
            else [],
            image_path=None,
            mineru_img_path=None,
            table_html=None,
            metadata={
                "section_path": str(block.section_path or "招股书原文"),
                "degraded": True,
                "retrieval_mode": "local_keyword",
            },
            score=round(hits / denominator, 4),
        )
        for hits, block in scored[:limit]
    ]


def prospectus_hybrid_search(
    *,
    query: str,
    document_id: str,
    limit: int = 5,
    vector_weight: float | None = None,
    indexer: PostgresChunkIndexer | None = None,
    config_path: str | Path | None = None,
) -> list[ChunkSearchResult]:
    """Search one prospectus in PostgreSQL; cross-document search is forbidden.

    Weights are read from ``configs/rag.yaml`` (``retrieval.hybrid_search``) and
    optionally adjusted at runtime via adaptive weight calculation.  When the
    config section is absent the legacy defaults (vector 0.75 / fulltext 0.25)
    are used, preserving backward compatibility.
    """
    # --- resolve weights from config + adaptive logic -----------------------
    hs_config = _load_retrieval_config(config_path)

    default_vw = float(hs_config.get("default_vector_weight", _DEFAULT_VECTOR_WEIGHT))
    default_fw = float(hs_config.get("default_fulltext_weight", _DEFAULT_FULLTEXT_WEIGHT))

    # An explicit caller-supplied weight takes precedence over config defaults.
    if vector_weight is not None:
        default_vw = vector_weight
        default_fw = 1.0 - vector_weight

    vw, fw, numeric_ratio = _compute_adaptive_weights(
        query, hs_config, default_vw, default_fw
    )

    logger.info(
        "Adaptive weights for query: vector=%.2f, fulltext=%.2f, numeric_ratio=%.2f",
        vw,
        fw,
        numeric_ratio,
    )

    # Query paths must never run schema DDL. Multiple experts search in
    # parallel, and repeating trigger/function creation here can deadlock the
    # entire agent team. Schema creation belongs to ingestion/startup.
    active_indexer = indexer
    try:
        if active_indexer is None:
            active_indexer = PostgresChunkIndexer(ensure=False)
        return active_indexer.hybrid_search(
            query=query,
            document_id=document_id,
            limit=limit,
            vector_weight=vw,
        )
    except Exception as exc:
        if indexer is not None or not _is_postgres_unavailable(exc):
            raise
        # SQLite fallback mode (or PostgreSQL outage): degrade to keyword search
        # over the document store instead of failing the whole analysis run.
        logger.warning(
            "PostgreSQL 混合检索不可用（%s: %s），回落为本地关键词检索 doc=%s",
            type(exc).__name__,
            exc,
            document_id,
        )
        return _local_keyword_search(query=query, document_id=document_id, limit=limit)
    finally:
        if indexer is None and active_indexer is not None:
            active_indexer.close()
