"""Source-adapter boundary.

Provider response formats (e.g. Bocha's Bing-shaped payload) never cross
this boundary: agents, reports and domain models only ever see
``SearchQuery`` / ``SearchHit`` / ``SearchResponse``.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .models import InjectedContext

if TYPE_CHECKING:
    from .cache import QueryCache

# Kinds the adapter may retry internally; anything else is persistent.
TRANSIENT_ERROR_KINDS = frozenset({"rate_limited", "timeout", "network", "unavailable"})


class GatewayError(Exception):
    """Sanitized gateway failure.

    ``message`` must never contain request headers or the API key; adapters
    are responsible for scrubbing before raising.
    """

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        self.message = message
        super().__init__(f"[{kind}] {message}")

    @property
    def transient(self) -> bool:
        return self.kind in TRANSIENT_ERROR_KINDS


class SearchQuery(BaseModel):
    text: str
    freshness: Optional[str] = None
    count: Optional[int] = None
    include_domains: list[str] = Field(default_factory=list)
    exclude_domains: list[str] = Field(default_factory=list)


class SearchHit(BaseModel):
    url: str
    title: Optional[str] = None
    site_name: Optional[str] = None
    snippet: Optional[str] = None
    summary: Optional[str] = None
    published_at: Optional[str] = None


class SearchResponse(BaseModel):
    hits: list[SearchHit] = Field(default_factory=list)
    provider: str
    domain_filter_mode: str = "none"


@runtime_checkable
class SourceAdapter(Protocol):
    name: str

    def available(self) -> bool: ...

    async def search(self, query: SearchQuery) -> SearchResponse: ...


def cache_key_for(adapter_name: str, query: SearchQuery, scope: InjectedContext) -> str:
    # data_cutoff_date and policy are part of the key, so cached results can
    # never leak across cutoff dates or policies.
    canonical = json.dumps(
        {
            "adapter": adapter_name,
            "query": query.model_dump(mode="json"),
            "data_cutoff_date": scope.data_cutoff_date,
            "policy": scope.policy.value,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ResearchGateway:
    def __init__(self, adapter: SourceAdapter, *, cache: Optional["QueryCache"] = None) -> None:
        self.adapter = adapter
        self.cache = cache

    def available(self) -> bool:
        return self.adapter.available()

    async def search(
        self, query: SearchQuery, *, cache_scope: InjectedContext
    ) -> tuple[SearchResponse, bool]:
        """Returns (response, cache_hit)."""
        key = cache_key_for(self.adapter.name, query, cache_scope)
        if self.cache is not None:
            cached = self.cache.get(key)
            if cached is not None:
                return cached, True
        response = await self.adapter.search(query)
        if self.cache is not None:
            self.cache.set(key, response)
        return response, False
