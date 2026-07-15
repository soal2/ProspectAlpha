"""Vector store provider boundary for semantic retrieval."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse


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

        client_kwargs = {}
        if _is_loopback_url(url):
            client_kwargs["trust_env"] = False
        self.client = QdrantClient(url=url, api_key=api_key, **client_kwargs)
        self.vector_size = vector_size

    def upsert(self, *, collection: str, points: list[VectorPoint]) -> None:
        if not points:
            return
        self._ensure_collection(collection)
        from qdrant_client.models import PointStruct

        for start in range(0, len(points), 128):
            self.client.upsert(
                collection_name=collection,
                points=[
                    PointStruct(
                        id=_point_uuid(point.point_id),
                        vector=point.vector,
                        payload={**point.payload, "point_id": point.point_id},
                    )
                    for point in points[start : start + 128]
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


def _is_loopback_url(url: str) -> bool:
    return urlparse(url).hostname in {"localhost", "127.0.0.1", "::1"}
