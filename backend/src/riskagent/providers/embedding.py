"""Embedding provider boundary for retrieval indexing."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol


class EmbeddingClient(Protocol):
    def embed(self, text: str) -> list[float]:
        ...


class HashingEmbeddingClient:
    """Deterministic local embedding used when no external provider is configured."""

    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[idx] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    latin = re.findall(r"[a-z0-9]+", lowered)
    cjk = re.findall(r"[\u4e00-\u9fff]{2,}", lowered)
    return latin + cjk


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    return sum(left[idx] * right[idx] for idx in range(size))
