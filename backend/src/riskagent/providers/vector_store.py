"""Vector store provider boundary for semantic retrieval."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VectorPoint:
    point_id: str
    vector: list[float]
    payload: dict


@dataclass(frozen=True)
class VectorMatch:
    point_id: str
    score: float
    payload: dict


class VectorStore(Protocol):
    def upsert(self, *, collection: str, points: list[VectorPoint]) -> None:
        ...

    def search(
        self,
        *,
        collection: str,
        vector: list[float],
        run_id: str,
        limit: int,
    ) -> list[VectorMatch]:
        ...


class NoopVectorStore:
    def upsert(self, *, collection: str, points: list[VectorPoint]) -> None:
        return None

    def search(
        self,
        *,
        collection: str,
        vector: list[float],
        run_id: str,
        limit: int,
    ) -> list[VectorMatch]:
        return []


class QdrantVectorStore:
    def __init__(self, *, url: str, vector_size: int, api_key: str | None = None) -> None:
        from qdrant_client import QdrantClient

        self.client = QdrantClient(url=url, api_key=api_key)
        self.vector_size = vector_size

    def upsert(self, *, collection: str, points: list[VectorPoint]) -> None:
        if not points:
            return
        self._ensure_collection(collection)
        from qdrant_client.models import PointStruct

        self.client.upsert(
            collection_name=collection,
            points=[
                PointStruct(
                    id=_point_uuid(point.point_id),
                    vector=point.vector,
                    payload={**point.payload, "point_id": point.point_id},
                )
                for point in points
            ],
        )

    def search(
        self,
        *,
        collection: str,
        vector: list[float],
        run_id: str,
        limit: int,
    ) -> list[VectorMatch]:
        self._ensure_collection(collection)
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        response = self.client.query_points(
            collection_name=collection,
            query=vector,
            query_filter=Filter(
                must=[FieldCondition(key="run_id", match=MatchValue(value=run_id))]
            ),
            limit=limit,
        )
        hits = response.points
        return [
            VectorMatch(
                point_id=str(hit.payload.get("point_id") or hit.id),
                score=float(hit.score),
                payload=dict(hit.payload or {}),
            )
            for hit in hits
        ]

    def _ensure_collection(self, collection: str) -> None:
        from qdrant_client.models import Distance, VectorParams

        existing = {item.name for item in self.client.get_collections().collections}
        if collection in existing:
            return
        self.client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE),
        )


def get_default_vector_store(vector_size: int) -> VectorStore:
    provider = os.getenv("VECTOR_STORE_PROVIDER", "noop").lower()
    if provider == "qdrant":
        return QdrantVectorStore(
            url=os.getenv("QDRANT_URL", "http://localhost:6333"),
            api_key=os.getenv("QDRANT_API_KEY") or None,
            vector_size=vector_size,
        )
    return NoopVectorStore()


def _point_uuid(value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, value))
