"""Bocha adapter, safe page fetcher and hypothesis-level research loop."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import random
import re
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Protocol
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from riskagent.hidden_risk import ResearchBudget, RiskHypothesis


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class GatewayError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        self.message = message
        super().__init__(f"[{kind}] {message}")

    @property
    def transient(self) -> bool:
        return self.kind in {"rate_limited", "timeout", "network", "unavailable"}


class SearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    freshness: str | None = None
    count: int = Field(default=10, ge=1, le=50)
    include_domains: list[str] = Field(default_factory=list)
    exclude_domains: list[str] = Field(default_factory=list)


class SearchHit(BaseModel):
    url: str
    title: str | None = None
    site_name: str | None = None
    snippet: str | None = None
    summary: str | None = None
    published_at: str | None = None


class SearchResponse(BaseModel):
    hits: list[SearchHit] = Field(default_factory=list)
    provider: str
    domain_filter_mode: str = "none"


class QueryCache(Protocol):
    def get(self, key: str) -> SearchResponse | None: ...

    def set(self, key: str, response: SearchResponse) -> None: ...


class InMemoryQueryCache:
    def __init__(self, *, ttl_seconds: int = 7 * 24 * 3600) -> None:
        self.ttl_seconds = ttl_seconds
        self.entries: dict[str, tuple[float, SearchResponse]] = {}
        self.lock = threading.Lock()

    def get(self, key: str) -> SearchResponse | None:
        with self.lock:
            entry = self.entries.get(key)
            if entry is None:
                return None
            stored_at, response = entry
            if time.time() - stored_at > self.ttl_seconds:
                self.entries.pop(key, None)
                return None
            return response.model_copy(deep=True)

    def set(self, key: str, response: SearchResponse) -> None:
        with self.lock:
            self.entries[key] = (time.time(), response.model_copy(deep=True))


class FileQueryCache:
    def __init__(self, directory: str | Path, *, ttl_seconds: int = 7 * 24 * 3600) -> None:
        self.directory = Path(directory)
        self.ttl_seconds = ttl_seconds
        self.lock = threading.Lock()

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> SearchResponse | None:
        path = self._path(key)
        with self.lock:
            if not path.exists():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if time.time() - float(payload["stored_at"]) > self.ttl_seconds:
                    path.unlink(missing_ok=True)
                    return None
                return SearchResponse.model_validate(payload["response"])
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                return None

    def set(self, key: str, response: SearchResponse) -> None:
        path = self._path(key)
        with self.lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            temp.write_text(
                json.dumps(
                    {"stored_at": time.time(), "response": response.model_dump(mode="json")},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            temp.replace(path)


class SourceAdapter(Protocol):
    name: str

    def available(self) -> bool: ...

    async def search(self, query: SearchQuery) -> SearchResponse: ...


class BochaConfig(BaseModel):
    api_key: SecretStr | None = None
    endpoint: str = "https://api.bochaai.com/v1/web-search"
    timeout_seconds: float = 10.0
    default_count: int = 10

    @classmethod
    def from_env(cls) -> "BochaConfig":
        key = os.getenv("BOCHA_API_KEY", "").strip()
        return cls(
            api_key=SecretStr(key) if key else None,
            endpoint=os.getenv("BOCHA_ENDPOINT", "https://api.bochaai.com/v1/web-search"),
            timeout_seconds=float(os.getenv("BOCHA_TIMEOUT", "10")),
            default_count=int(os.getenv("BOCHA_DEFAULT_COUNT", "10")),
        )


def parse_published_date(raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


class BochaAdapter:
    """The only provider-specific Seam exposed to the research module."""

    name = "bocha"

    def __init__(
        self,
        config: BochaConfig | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.config = config or BochaConfig.from_env()
        self.transport = transport
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    def available(self) -> bool:
        return self.config.api_key is not None

    async def search(self, query: SearchQuery) -> SearchResponse:
        if self.config.api_key is None:
            raise GatewayError("auth", "BOCHA_API_KEY is not configured")
        include = [item.strip().lower() for item in query.include_domains if item.strip()][:5]
        exclude = [item.strip().lower() for item in query.exclude_domains if item.strip()]
        text = query.text
        if include:
            operators = " OR ".join(f"site:{item}" for item in include)
            text = f"{text} ({operators})" if len(include) > 1 else f"{text} {operators}"
        body: dict[str, Any] = {
            "query": text,
            "freshness": query.freshness or "noLimit",
            "summary": True,
            "count": min(50, max(1, query.count or self.config.default_count)),
        }
        payload = await self._post(body)
        container = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        web_pages = container.get("webPages") if isinstance(container, dict) else None
        values = web_pages.get("value") if isinstance(web_pages, dict) else []
        hits: list[SearchHit] = []
        for item in values or []:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue
            host = (urlsplit(item["url"]).hostname or "").lower()
            if include and not any(host == domain or host.endswith(f".{domain}") for domain in include):
                continue
            if exclude and any(host == domain or host.endswith(f".{domain}") for domain in exclude):
                continue
            hits.append(
                SearchHit(
                    url=item["url"],
                    title=item.get("name") or item.get("title"),
                    site_name=item.get("siteName"),
                    snippet=item.get("snippet"),
                    summary=item.get("summary"),
                    published_at=parse_published_date(item.get("datePublished")),
                )
            )
        return SearchResponse(
            hits=hits,
            provider=self.name,
            domain_filter_mode="emulated" if include or exclude else "none",
        )

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        error: GatewayError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await self._post_once(body)
            except GatewayError as exc:
                error = exc
                if not exc.transient or attempt >= self.max_retries:
                    raise
                await asyncio.sleep(self.retry_backoff_seconds * (attempt + 1) * (1 + random.random() * 0.25))
        raise error or GatewayError("unavailable", "bocha request failed")

    async def _post_once(self, body: dict[str, Any]) -> dict[str, Any]:
        assert self.config.api_key is not None
        kwargs: dict[str, Any] = {"timeout": self.config.timeout_seconds}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        try:
            async with httpx.AsyncClient(**kwargs) as client:
                response = await client.post(
                    self.config.endpoint,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self.config.api_key.get_secret_value()}",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            raise GatewayError("timeout", "bocha request timed out") from exc
        except httpx.HTTPError as exc:
            raise GatewayError("network", f"bocha request failed: {type(exc).__name__}") from exc
        kinds = {400: "bad_request", 401: "auth", 403: "balance", 429: "rate_limited"}
        if response.status_code != 200:
            kind = kinds.get(response.status_code, "unavailable" if response.status_code >= 500 else "protocol")
            raise GatewayError(kind, f"bocha returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise GatewayError("protocol", "bocha returned non-JSON body") from exc
        if not isinstance(payload, dict):
            raise GatewayError("protocol", "bocha returned unexpected JSON shape")
        return payload


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.in_title = False
        self.title: list[str] = []
        self.text: list[str] = []
        self.published_hint: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            values = {key.lower(): value for key, value in attrs if value is not None}
            key = (values.get("property") or values.get("name") or "").lower()
            if key in {"article:published_time", "publishdate", "publication_date", "date"}:
                self.published_hint = values.get("content") or self.published_hint
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "article", "section"}:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self.skip:
            self.skip -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title.append(data)
        elif not self.skip and data.strip():
            self.text.append(data)


class FetchOutcome(BaseModel):
    requested_url: str
    final_url: str
    accessible: bool
    inaccessible_reason: str | None = None
    status_code: int | None = None
    title: str | None = None
    text: str | None = None
    published_at: str | None = None
    content_hash: str | None = None
    bytes_read: int = 0


Resolver = Callable[[str], Awaitable[list[str]]]


async def _resolve(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(item[4][0]) for item in infos))


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%")[0])
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return bool((mapped or address).is_global and not (mapped or address).is_multicast)


class SafeFetcher:
    """SSRF-hardened, bounded HTML/text fetcher with per-hop validation."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
        max_bytes: int = 2 * 1024 * 1024,
        max_redirects: int = 3,
        total_seconds: float = 15.0,
    ) -> None:
        self.transport = transport
        self.resolver = resolver or _resolve
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.total_seconds = total_seconds
        self.robots: dict[str, RobotFileParser] = {}

    async def fetch(self, url: str) -> FetchOutcome:
        deadline = time.monotonic() + self.total_seconds
        current = url
        kwargs: dict[str, Any] = {}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        async with httpx.AsyncClient(**kwargs) as client:
            for _ in range(self.max_redirects + 1):
                reason = await self._validate(current)
                if reason:
                    return FetchOutcome(requested_url=url, final_url=current, accessible=False, inaccessible_reason=reason)
                allowed, reason = await self._robots_allowed(client, current, deadline)
                if reason or not allowed:
                    return FetchOutcome(requested_url=url, final_url=current, accessible=False, inaccessible_reason=reason or "robots_disallow")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return FetchOutcome(requested_url=url, final_url=current, accessible=False, inaccessible_reason="timeout")
                try:
                    async with client.stream("GET", current, follow_redirects=False, timeout=remaining, headers={"User-Agent": "ProspectAlphaResearchBot/1.0"}) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                return self._failure(url, current, "redirect_without_location", response.status_code)
                            current = urljoin(current, location)
                            continue
                        return await self._read(url, current, response, deadline)
                except httpx.TimeoutException:
                    return self._failure(url, current, "timeout")
                except httpx.HTTPError:
                    return self._failure(url, current, "network_error")
        return self._failure(url, current, "too_many_redirects")

    async def _validate(self, url: str) -> str | None:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return "invalid_url"
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or port not in {None, 80, 443}:
            return "unsafe_url"
        if not parsed.hostname:
            return "invalid_url"
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            try:
                addresses = await self.resolver(parsed.hostname)
            except OSError:
                return "dns_error"
            return None if addresses and all(_public_address(item) for item in addresses) else "private_address"
        return None if _public_address(parsed.hostname) else "private_address"

    async def _robots_allowed(self, client: httpx.AsyncClient, url: str, deadline: float) -> tuple[bool, str | None]:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        robot = self.robots.get(origin)
        if robot is None:
            robot = RobotFileParser()
            try:
                response = await client.get(f"{origin}/robots.txt", follow_redirects=False, timeout=max(0.1, deadline - time.monotonic()))
            except httpx.HTTPError:
                robot.allow_all = True
            else:
                if response.status_code == 200:
                    robot.parse(response.text[:65536].splitlines())
                elif response.status_code in {401, 403}:
                    robot.disallow_all = True
                else:
                    robot.allow_all = True
            self.robots[origin] = robot
        return robot.can_fetch("ProspectAlphaResearchBot/1.0", url), None

    async def _read(self, requested: str, current: str, response: httpx.Response, deadline: float) -> FetchOutcome:
        if response.status_code < 200 or response.status_code >= 300:
            return self._failure(requested, current, f"http_{response.status_code}", response.status_code)
        media = response.headers.get("content-type", "").split(";")[0].lower()
        if media not in {"text/html", "application/xhtml+xml", "text/plain"}:
            return self._failure(requested, current, "unsupported_content_type", response.status_code)
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > self.max_bytes:
                return self._failure(requested, current, "oversize", response.status_code)
            if time.monotonic() > deadline:
                return self._failure(requested, current, "timeout", response.status_code)
        decoded = bytes(body).decode(response.encoding or "utf-8", errors="replace")
        if media == "text/plain":
            title, text, published = None, decoded.strip(), None
        else:
            parser = _Extractor()
            parser.feed(decoded)
            title = re.sub(r"\s+", " ", "".join(parser.title)).strip() or None
            text = re.sub(r"\n{2,}", "\n", re.sub(r"[ \t]+", " ", "".join(parser.text))).strip()
            published = parse_published_date(parser.published_hint)
        return FetchOutcome(
            requested_url=requested,
            final_url=current,
            accessible=True,
            status_code=response.status_code,
            title=title,
            text=text[:12000],
            published_at=published,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            bytes_read=len(body),
        )

    @staticmethod
    def _failure(requested: str, current: str, reason: str, status: int | None = None) -> FetchOutcome:
        return FetchOutcome(requested_url=requested, final_url=current, accessible=False, inaccessible_reason=reason, status_code=status)


class SourceCard(BaseModel):
    source_id: str = Field(default_factory=lambda: _id("src"))
    hypothesis_id: str
    query_kind: str
    url: str
    title: str | None = None
    publisher: str | None = None
    snippet: str | None = None
    extract: str | None = None
    published_at: str | None = None
    retrieved_at: str = Field(default_factory=_now)
    content_hash: str | None = None
    accessible: bool
    inaccessible_reason: str | None = None
    eligible_pre_cutoff: bool = False


class ToolAuditRecord(BaseModel):
    call_id: str = Field(default_factory=lambda: _id("call"))
    hypothesis_id: str
    query_kind: str
    query: str
    provider: str = "bocha"
    cache_hit: bool = False
    result_count: int = 0
    outcome: Literal["ok", "error"] = "ok"
    error_kind: str | None = None
    created_at: str = Field(default_factory=_now)


class ResearchUsage(BaseModel):
    hypothesis_limit: int
    search_limit: int
    hypotheses_researched: int = 0
    searches_used: int = 0
    pages_fetched: int = 0


class ResearchResult(BaseModel):
    hypotheses: list[RiskHypothesis]
    sources: list[SourceCard]
    audit: list[ToolAuditRecord]
    usage: ResearchUsage
    limitations: list[str] = Field(default_factory=list)


class HypothesisResearchModule:
    def __init__(
        self,
        adapter: SourceAdapter | None = None,
        fetcher: SafeFetcher | None = None,
        cache: QueryCache | None = None,
    ) -> None:
        self.adapter = adapter or BochaAdapter()
        self.fetcher = fetcher or SafeFetcher()
        cache_dir = os.getenv("RESEARCH_CACHE_DIR", "").strip()
        self.cache = cache or (
            FileQueryCache(cache_dir) if cache_dir else InMemoryQueryCache()
        )

    async def run(
        self,
        *,
        company_name: str,
        hypotheses: list[RiskHypothesis],
        budget: ResearchBudget,
        data_cutoff_date: str,
    ) -> ResearchResult:
        usage = ResearchUsage(hypothesis_limit=budget.hypothesis_limit, search_limit=budget.search_limit)
        sources: list[SourceCard] = []
        audit: list[ToolAuditRecord] = []
        limitations: list[str] = []
        selected = hypotheses[: budget.hypothesis_limit]
        if not self.adapter.available():
            limitations.append("BOCHA_API_KEY 未配置，保留招股书隐性风险但未执行外部佐证。")
            return ResearchResult(hypotheses=selected, sources=sources, audit=audit, usage=usage, limitations=limitations)
        for hypothesis in selected:
            if usage.searches_used + 2 > budget.search_limit:
                break
            usage.hypotheses_researched += 1
            found_support = False
            found_counter = False
            queries = (
                ("support", f'"{company_name}" {hypothesis.risk_type} {hypothesis.title} 风险 争议'),
                ("refutation", f'"{company_name}" {hypothesis.risk_type} 澄清 改善 否认 反证'),
            )
            for query_kind, query_text in queries:
                usage.searches_used += 1
                cache_key = hashlib.sha256(f"{data_cutoff_date}:{query_text}".encode()).hexdigest()
                try:
                    response = self.cache.get(cache_key)
                    cache_hit = response is not None
                    if response is None:
                        response = await self.adapter.search(SearchQuery(text=query_text, count=10))
                        self.cache.set(cache_key, response)
                    audit.append(ToolAuditRecord(hypothesis_id=hypothesis.hypothesis_id, query_kind=query_kind, query=query_text, cache_hit=cache_hit, result_count=len(response.hits)))
                except GatewayError as exc:
                    audit.append(ToolAuditRecord(hypothesis_id=hypothesis.hypothesis_id, query_kind=query_kind, query=query_text, outcome="error", error_kind=exc.kind))
                    limitations.append(f"{hypothesis.title} 的{query_kind}检索失败：{exc.kind}")
                    continue
                for hit in response.hits[:3]:
                    outcome = await self.fetcher.fetch(hit.url)
                    usage.pages_fetched += 1
                    published = outcome.published_at or hit.published_at
                    eligible = bool(outcome.accessible and published and published <= data_cutoff_date)
                    card = SourceCard(
                        hypothesis_id=hypothesis.hypothesis_id,
                        query_kind=query_kind,
                        url=hit.url,
                        title=outcome.title or hit.title,
                        publisher=hit.site_name,
                        snippet=hit.summary or hit.snippet,
                        extract=(outcome.text or "")[:4000] or None,
                        published_at=published,
                        content_hash=outcome.content_hash,
                        accessible=outcome.accessible,
                        inaccessible_reason=outcome.inaccessible_reason,
                        eligible_pre_cutoff=eligible,
                    )
                    sources.append(card)
                    source_text = " ".join(
                        item
                        for item in (card.title, card.snippet, card.extract)
                        if item
                    )
                    relevant = _relevant_to_hypothesis(
                        company_name,
                        hypothesis,
                        source_text,
                    )
                    if eligible and relevant and query_kind == "support":
                        hypothesis.external_evidence_ids.append(card.source_id)
                        found_support = True
                    elif (
                        eligible
                        and relevant
                        and query_kind == "refutation"
                        and _contains_counterevidence(source_text)
                    ):
                        hypothesis.counter_evidence_ids.append(card.source_id)
                        found_counter = True
            hypothesis.external_evidence_ids = list(dict.fromkeys(hypothesis.external_evidence_ids))
            hypothesis.counter_evidence_ids = list(dict.fromkeys(hypothesis.counter_evidence_ids))
            if found_counter:
                hypothesis.status = "disputed"
            elif found_support:
                hypothesis.status = "corroborated"
            else:
                hypothesis.status = "reported"
            if not found_support and not found_counter:
                limitations.append(
                    f"{hypothesis.title} 连续一轮未获得新的相关正文证据，研究提前停止。"
                )
                break
        return ResearchResult(hypotheses=selected, sources=sources, audit=audit, usage=usage, limitations=list(dict.fromkeys(limitations)))

    async def research_retrospective(
        self,
        *,
        company_name: str,
        risk_titles: list[str],
        listing_date: str,
        search_limit: int = 6,
    ) -> dict[str, Any]:
        """Search post-listing events without mutating any pre-listing hypothesis or score."""

        if not self.adapter.available():
            return {
                "status": "insufficient",
                "searches_used": 0,
                "sources": [],
                "limitations": ["BOCHA_API_KEY 未配置，未执行上市后外部事件归因。"],
            }
        searches_used = 0
        sources: list[dict[str, Any]] = []
        limitations: list[str] = []
        for title in list(dict.fromkeys(item.strip() for item in risk_titles if item.strip())):
            if searches_used >= min(6, search_limit):
                break
            searches_used += 1
            query = f'"{company_name}" {title} 上市后 业绩 处罚 诉讼 澄清 事件'
            try:
                response = await self.adapter.search(SearchQuery(text=query, count=10))
            except GatewayError as exc:
                limitations.append(f"{title}：{exc.kind}")
                continue
            for hit in response.hits[:3]:
                outcome = await self.fetcher.fetch(hit.url)
                published = outcome.published_at or hit.published_at
                if not outcome.accessible or not published or published.replace("-", "") <= listing_date.replace("-", ""):
                    continue
                text = f"{outcome.title or hit.title or ''} {outcome.text or hit.summary or hit.snippet or ''}"
                counter = any(term in text for term in ("澄清", "否认", "不实", "未发生", "已改善"))
                sources.append(
                    {
                        "source_id": _id("post"),
                        "risk_title": title,
                        "url": hit.url,
                        "title": outcome.title or hit.title,
                        "published_at": published,
                        "content_hash": outcome.content_hash,
                        "classification": "counterevidence" if counter else "materialization",
                    }
                )
        if any(item["classification"] == "materialization" for item in sources):
            status = "externally_materialized"
        elif sources:
            status = "counterevidence"
        elif limitations:
            status = "insufficient"
        else:
            status = "not_observed"
        return {
            "status": status,
            "searches_used": searches_used,
            "sources": sources,
            "limitations": limitations,
        }


def _contains_counterevidence(text: str) -> bool:
    return any(term in text for term in ("澄清", "否认", "不实", "未发生", "已改善", "反证"))


def _relevant_to_hypothesis(
    company_name: str,
    hypothesis: RiskHypothesis,
    text: str,
) -> bool:
    normalized = re.sub(r"\s+", "", text).lower()
    if company_name and company_name.lower().replace(" ", "") not in normalized:
        return False
    terms = [hypothesis.risk_type, hypothesis.title]
    compact_terms = [re.sub(r"\s+", "", item).lower() for item in terms if item]
    fragments = {
        fragment
        for term in compact_terms
        for fragment in ([term] if len(term) <= 4 else [term[index : index + 4] for index in range(len(term) - 3)])
    }
    return any(fragment in normalized for fragment in fragments)
