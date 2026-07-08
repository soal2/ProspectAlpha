"""Persistent hybrid retrieval over document blocks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import delete, select

from riskagent.database import SessionLocal
from riskagent.models import RetrievalIndexRow
from riskagent.providers.embedding import (
    EmbeddingClient,
    HashingEmbeddingClient,
    cosine_similarity,
    tokenize,
)
from riskagent.providers.vector_store import VectorPoint, VectorStore, get_default_vector_store


DEFAULT_COLLECTION = "prospect_alpha_document_blocks"


class IndexableBlock(Protocol):
    block_id: str
    document_id: str
    page_number: int
    section_path: str
    block_type: str
    text: str
    bbox: list[float]
    char_start: int
    char_end: int
    source_hash: str
    parser_version: str


@dataclass
class EvidenceCandidate:
    block_id: str
    document_id: str
    page_number: int
    section_path: str
    text: str
    bbox: list[float]
    char_start: int
    char_end: int
    source_hash: str
    parser_version: str
    score: float
    lexical_score: float
    vector_score: float


class HybridSearchIndex:
    def __init__(
        self,
        *,
        session_factory=SessionLocal,
        embedding_client: EmbeddingClient | None = None,
        vector_store: VectorStore | None = None,
        collection_name: str = DEFAULT_COLLECTION,
    ) -> None:
        self.session_factory = session_factory
        self.embedding_client = embedding_client or HashingEmbeddingClient()
        probe_vector = self.embedding_client.embed("probe")
        self.vector_store = vector_store or get_default_vector_store(len(probe_vector))
        self.collection_name = collection_name

    def index_blocks(self, *, run_id: str, blocks: list[IndexableBlock]) -> int:
        vector_points: list[VectorPoint] = []
        with self.session_factory() as session:
            session.execute(delete(RetrievalIndexRow).where(RetrievalIndexRow.run_id == run_id))
            rows = []
            for block in blocks:
                terms = tokenize(block.text)
                embedding = self.embedding_client.embed(block.text)
                point_id = _index_id(run_id, block.block_id)
                payload = {
                    "run_id": run_id,
                    "document_id": block.document_id,
                    "block_id": block.block_id,
                    "page_number": block.page_number,
                    "section_path": block.section_path,
                    "block_type": block.block_type,
                    "bbox": block.bbox,
                    "char_start": block.char_start,
                    "char_end": block.char_end,
                    "source_hash": block.source_hash,
                    "parser_version": block.parser_version,
                }
                rows.append(
                    RetrievalIndexRow(
                        index_id=point_id,
                        run_id=run_id,
                        document_id=block.document_id,
                        block_id=block.block_id,
                        text=block.text,
                        terms=terms,
                        embedding=embedding,
                        payload=payload,
                    )
                )
                vector_points.append(
                    VectorPoint(point_id=point_id, vector=embedding, payload=payload)
                )
            session.add_all(rows)
            session.commit()
        self.vector_store.upsert(collection=self.collection_name, points=vector_points)
        return len(vector_points)

    def search(
        self,
        *,
        run_id: str,
        query: str,
        limit: int = 5,
    ) -> list[EvidenceCandidate]:
        query_terms = tokenize(query)
        query_embedding = self.embedding_client.embed(query)
        vector_scores = {
            match.point_id: max(0.0, match.score)
            for match in self.vector_store.search(
                collection=self.collection_name,
                vector=query_embedding,
                run_id=run_id,
                limit=max(limit * 4, 20),
            )
        }
        with self.session_factory() as session:
            rows = session.scalars(
                select(RetrievalIndexRow).where(RetrievalIndexRow.run_id == run_id)
            ).all()
        candidates = []
        for row in rows:
            lexical = _lexical_score(query_terms, row.terms or [], row.text)
            local_vector = max(0.0, cosine_similarity(query_embedding, row.embedding or []))
            vector = max(local_vector, vector_scores.get(row.index_id, 0.0))
            raw_score = lexical * 0.65 + vector * 0.35
            if raw_score <= 0:
                continue
            score = min(0.99, 0.5 + raw_score * 0.49)
            payload = row.payload or {}
            candidates.append(
                EvidenceCandidate(
                    block_id=row.block_id,
                    document_id=row.document_id,
                    page_number=int(payload.get("page_number", 0)),
                    section_path=str(payload.get("section_path", "")),
                    text=row.text,
                    bbox=[float(value) for value in payload.get("bbox", [])],
                    char_start=int(payload.get("char_start", 0)),
                    char_end=int(payload.get("char_end", 0)),
                    source_hash=str(payload.get("source_hash", "")),
                    parser_version=str(payload.get("parser_version", "")),
                    score=score,
                    lexical_score=lexical,
                    vector_score=vector,
                )
            )
        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates[:limit]


def _index_id(run_id: str, block_id: str) -> str:
    return hashlib.sha256(f"{run_id}:{block_id}".encode("utf-8")).hexdigest()


def _lexical_score(query_terms: list[str], block_terms: list[str], text: str) -> float:
    if not query_terms:
        return 0.0
    block_term_set = set(block_terms)
    hits = sum(1 for term in query_terms if term in block_term_set or term in text.lower())
    return hits / len(set(query_terms))
