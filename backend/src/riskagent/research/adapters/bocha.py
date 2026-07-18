"""Bocha (博查) web-search adapter.

API contract implemented from public documentation (open.bochaai.com and the
official BochaAI/bocha-search-mcp repository) because the official Feishu wiki
(https://bocha-ai.feishu.cn/wiki/HmtOw1z6vik14Fkdu5uc9VaInBb) is behind a
login wall.  Contract used here:

    POST https://api.bochaai.com/v1/web-search
    Authorization: Bearer <key>
    body: query (required), freshness (noLimit|oneDay|oneWeek|oneMonth|
          oneYear|YYYY-MM-DD|YYYY-MM-DD..YYYY-MM-DD), count (1-50), summary
    response: Bing-shaped, webPages.value[] items with
          id/name/url/siteName/siteIcon/snippet/summary/datePublished
    errors: 400 bad request, 401 auth, 403 balance, 429 rate limit

If live responses ever diverge, only the parsing in this adapter changes —
the domain models (SearchHit/SearchResponse) stay put.

Bocha has NO native include/exclude domain parameters.  Domain filtering is
honestly emulated: include via ``site:`` operators appended to the query
(max 5 domains) plus registrable-domain post-filtering; exclude via
post-filtering only.  Responses are tagged ``domain_filter_mode="emulated"``
so the audit trail never claims a native capability.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from ..config import BochaConfig
from ..gateway import GatewayError, SearchHit, SearchQuery, SearchResponse

_MAX_SITE_OPERATORS = 5


def _registrable_match(host: str, domain: str) -> bool:
    host = host.lower().strip(".")
    domain = domain.lower().strip(".")
    return host == domain or host.endswith("." + domain)


def _host_of(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def parse_published_date(raw: Any) -> Optional[str]:
    """Lenient datePublished parsing; unparseable -> None (card is undated)."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    candidate = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate).date().isoformat()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


class BochaAdapter:
    name = "bocha"

    def __init__(
        self,
        config: BochaConfig,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self._config = config
        self._transport = transport
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds

    def available(self) -> bool:
        return self._config.api_key is not None

    def _sanitize(self, message: str) -> str:
        if self._config.api_key is not None:
            secret = self._config.api_key.get_secret_value()
            if secret:
                message = message.replace(secret, "***")
        return message

    async def search(self, query: SearchQuery) -> SearchResponse:
        if self._config.api_key is None:
            # Raised before any HTTP so a missing key never produces traffic.
            raise GatewayError("auth", "BOCHA_API_KEY is not configured")

        text = query.text
        include = [d for d in query.include_domains if d][:_MAX_SITE_OPERATORS]
        exclude = [d for d in query.exclude_domains if d]
        if include:
            operators = " OR ".join(f"site:{domain}" for domain in include)
            text = f"{text} ({operators})" if len(include) > 1 else f"{text} site:{include[0]}"

        count = query.count if query.count is not None else self._config.default_count
        count = max(1, min(50, int(count)))

        body: dict[str, Any] = {"query": text, "count": count, "summary": True}
        if query.freshness:
            body["freshness"] = query.freshness

        data = await self._post_with_retries(body)
        hits = self._parse_hits(data)

        if include:
            hits = [h for h in hits if any(_registrable_match(_host_of(h.url), d) for d in include)]
        if exclude:
            hits = [h for h in hits if not any(_registrable_match(_host_of(h.url), d) for d in exclude)]

        return SearchResponse(
            hits=hits,
            provider=self.name,
            domain_filter_mode="emulated" if (include or exclude) else "none",
        )

    async def _post_with_retries(self, body: dict[str, Any]) -> dict[str, Any]:
        last_error: Optional[GatewayError] = None
        attempts = self._max_retries + 1
        for attempt in range(attempts):
            try:
                return await self._post_once(body)
            except GatewayError as exc:
                last_error = exc
                if not exc.transient or attempt == attempts - 1:
                    raise
                delay = self._retry_backoff_seconds * (attempt + 1)
                if delay > 0:
                    await asyncio.sleep(delay * (1 + random.random() * 0.25))
        raise last_error  # pragma: no cover - loop always raises or returns

    async def _post_once(self, body: dict[str, Any]) -> dict[str, Any]:
        assert self._config.api_key is not None
        # The Authorization header exists only inside this scope and is never
        # logged, echoed via event hooks, or attached to exceptions.
        headers = {
            "Authorization": f"Bearer {self._config.api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        client_kwargs: dict[str, Any] = {"timeout": self._config.timeout_seconds}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.post(self._config.endpoint, json=body, headers=headers)
        except httpx.TimeoutException:
            raise GatewayError("timeout", self._sanitize("bocha request timed out"))
        except httpx.HTTPError as exc:
            raise GatewayError("network", self._sanitize(f"bocha request failed: {type(exc).__name__}"))

        status = response.status_code
        if status == 200:
            try:
                payload = response.json()
            except ValueError:
                raise GatewayError("protocol", "bocha returned non-JSON body")
            if not isinstance(payload, dict):
                raise GatewayError("protocol", "bocha returned unexpected JSON shape")
            return payload
        if status == 400:
            raise GatewayError("bad_request", "bocha rejected the request (400)")
        if status == 401:
            raise GatewayError("auth", "bocha authentication failed (401)")
        if status == 403:
            raise GatewayError("balance", "bocha account balance/permission issue (403)")
        if status == 429:
            raise GatewayError("rate_limited", "bocha rate limit hit (429)")
        if status >= 500:
            raise GatewayError("unavailable", f"bocha server error ({status})")
        raise GatewayError("protocol", f"bocha unexpected status ({status})")

    def _parse_hits(self, payload: dict[str, Any]) -> list[SearchHit]:
        container = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        web_pages = container.get("webPages") if isinstance(container, dict) else None
        values = web_pages.get("value") if isinstance(web_pages, dict) else None
        hits: list[SearchHit] = []
        for item in values or []:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not url or not isinstance(url, str):
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=item.get("name") or None,
                    site_name=item.get("siteName") or None,
                    snippet=item.get("snippet") or None,
                    summary=item.get("summary") or None,
                    published_at=parse_published_date(item.get("datePublished")),
                )
            )
        return hits
