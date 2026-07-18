"""The two controlled tools exposed to research LLMs.

Trust boundary rules, all enforced here in code:

* ``run_id`` / ``data_cutoff_date`` / ``external_search_policy`` are injected
  from the server-side :class:`ToolContext` only.  They never appear in the
  tool schemas, and if a model tries to pass them (or any unknown key) the
  values are stripped into ``ToolAuditRecord.rejected_params`` — there is no
  code path from raw model arguments into :class:`InjectedContext`.
* ``purpose`` is mandatory; ``hypothesis_id`` XOR ``coverage_task_id`` is
  mandatory and must reference an id the server knows about.
* policy ``disabled`` rejects every call; ``approved_sensitive`` is
  recognized but fails closed (no sensitive egress is implemented).
* Every call — including rejected ones — appends a ToolAuditRecord.
* Search results are returned to the model as a compact projection of the
  normalized domain model, never the raw provider payload.
* Fetch failures (robots, paywall, SSRF rejection, oversize, timeout …) are
  still tool successes: the model gets an ``accessible=False`` lead card.
  Provider errors (:class:`GatewayError`) are audited and re-raised so the
  engine can classify transient vs. persistent failures.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import AbstractSet, Any, Callable, Iterable, Optional
from urllib.parse import urlsplit

from .adapters.bocha import parse_published_date
from .gateway import GatewayError, ResearchGateway, SearchQuery
from .models import (
    ExternalSearchPolicy,
    InjectedContext,
    SourceCard,
    ToolAuditRecord,
    utc_now,
)
from .ssrf import SafeFetcher
from .tiering import classify_source

TOOL_BOCHA_WEB_SEARCH = "bocha_web_search"
TOOL_FETCH_WEB_SOURCE = "fetch_web_source"

_SEARCH_PARAM_KEYS = frozenset(
    {
        "query",
        "purpose",
        "hypothesis_id",
        "coverage_task_id",
        "freshness",
        "count",
        "include_domains",
        "exclude_domains",
    }
)
_FETCH_PARAM_KEYS = frozenset({"url", "purpose", "hypothesis_id", "coverage_task_id"})


def normalize_url(url: str) -> str:
    """Canonical form for same-run URL dedup (case, fragment, default port)."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return url.strip()
    host = (parts.hostname or "").lower()
    port_part = f":{port}" if port not in (None, 80, 443) else ""
    query_part = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{host}{port_part}{parts.path or '/'}{query_part}"


@dataclass(frozen=True)
class ToolContext:
    """Server-side trusted scope for one module's tool loop."""

    run_id: str
    data_cutoff_date: Optional[str]
    policy: ExternalSearchPolicy
    module_id: str
    agent_name: str
    valid_hypothesis_ids: Callable[[], AbstractSet[str]]
    valid_task_ids: Callable[[], AbstractSet[str]]

    def injected(self) -> InjectedContext:
        return InjectedContext(
            run_id=self.run_id,
            data_cutoff_date=self.data_cutoff_date,
            policy=self.policy,
        )


class ToolLedger:
    """In-memory collection of cards + audit produced by one tool loop.

    Seeded with the run's existing evidence so URL dedup spans the whole run,
    not just this module.
    """

    def __init__(self, existing_cards: Iterable[SourceCard] = ()) -> None:
        self.evidence: list[SourceCard] = []
        self.audit: list[ToolAuditRecord] = []
        self._by_url: dict[tuple[str, str], SourceCard] = {}
        for card in existing_cards:
            self._by_url.setdefault((card.origin, normalize_url(card.url)), card)

    def find_by_url(self, origin: str, url: str) -> Optional[SourceCard]:
        return self._by_url.get((origin, normalize_url(url)))

    def add_card(self, card: SourceCard) -> SourceCard:
        existing = self.find_by_url(card.origin, card.url)
        if existing is not None:
            return existing
        self.evidence.append(card)
        self._by_url[(card.origin, normalize_url(card.url))] = card
        return card

    def record(self, audit: ToolAuditRecord) -> None:
        self.audit.append(audit)


class ToolRejected(Exception):
    def __init__(self, error_kind: str, message: str) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.message = message


class ResearchToolset:
    def __init__(
        self,
        *,
        gateway: ResearchGateway,
        fetcher: SafeFetcher,
        context: ToolContext,
        ledger: Optional[ToolLedger] = None,
        ir_domains: Iterable[str] = (),
    ) -> None:
        self._gateway = gateway
        self._fetcher = fetcher
        self._context = context
        self._ir_domains = tuple(d for d in ir_domains if d)
        self.ledger = ledger if ledger is not None else ToolLedger()

    def _stamp_tier(self, card: SourceCard) -> None:
        """Stamp ``tier``/``tier_reason`` on a freshly-built card.

        The gate counts only tier1/tier2 cards; without this stamp every
        card lands as ``unknown`` and structural verification is
        impossible — so this is mandatory at every card creation site.
        """
        tier, reason = classify_source(card, ir_domains=self._ir_domains)
        card.tier = tier
        card.tier_reason = reason

    # ------------------------------------------------------------------
    # schemas

    @staticmethod
    def openai_tool_schemas() -> list[dict[str, Any]]:
        binding_props = {
            "purpose": {
                "type": "string",
                "description": (
                    "为什么需要这次调用(必填)。用于审计;标记反证搜索请以"
                    "'反证'开头。"
                ),
            },
            "hypothesis_id": {
                "type": "string",
                "description": "本次调用服务的假设 id(与 coverage_task_id 二选一)",
            },
            "coverage_task_id": {
                "type": "string",
                "description": "本次调用服务的覆盖任务 id(与 hypothesis_id 二选一)",
            },
        }
        return [
            {
                "type": "function",
                "function": {
                    "name": TOOL_BOCHA_WEB_SEARCH,
                    "description": (
                        "博查公开网页搜索。返回规范化命中列表"
                        "(evidence_id/title/url/snippet/published_at)。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索词"},
                            **binding_props,
                            "freshness": {
                                "type": "string",
                                "description": (
                                    "noLimit|oneDay|oneWeek|oneMonth|oneYear|"
                                    "YYYY-MM-DD|YYYY-MM-DD..YYYY-MM-DD"
                                ),
                            },
                            "count": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 50,
                            },
                            "include_domains": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "限定域名(以 site: 模拟,最多 5 个)",
                            },
                            "exclude_domains": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["query", "purpose"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": TOOL_FETCH_WEB_SOURCE,
                    "description": (
                        "抓取一个公开 URL 的正文作为证据卡。robots/付费墙/登录墙"
                        "不会被绕过;无法读取时返回 accessible=false 的线索卡。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "http(s) URL"},
                            **binding_props,
                        },
                        "required": ["url", "purpose"],
                    },
                },
            },
        ]

    # ------------------------------------------------------------------
    # dispatch

    async def dispatch(self, tool_name: str, raw_args: dict[str, Any]) -> dict[str, Any]:
        started_at = utc_now()
        t0 = time.monotonic()
        allowed = {
            TOOL_BOCHA_WEB_SEARCH: _SEARCH_PARAM_KEYS,
            TOOL_FETCH_WEB_SOURCE: _FETCH_PARAM_KEYS,
        }.get(tool_name)

        raw_args = dict(raw_args or {})
        if allowed is None:
            requested: dict[str, Any] = {}
            rejected_params = raw_args
        else:
            requested = {k: v for k, v in raw_args.items() if k in allowed}
            rejected_params = {k: v for k, v in raw_args.items() if k not in allowed}

        audit = ToolAuditRecord(
            tool=tool_name,
            requested=requested,
            rejected_params=rejected_params,
            injected=self._context.injected(),
            started_at=started_at,
        )

        try:
            hypothesis_id, coverage_task_id = self._validate_common(
                tool_name, allowed, requested
            )
            audit.hypothesis_id = hypothesis_id
            audit.coverage_task_id = coverage_task_id
        except ToolRejected as rejection:
            audit.outcome = "rejected"
            audit.error_kind = rejection.error_kind
            audit.duration_ms = int((time.monotonic() - t0) * 1000)
            self.ledger.record(audit)
            return {"ok": False, "error": rejection.message}

        try:
            if tool_name == TOOL_BOCHA_WEB_SEARCH:
                result = await self._run_search(requested, audit)
            else:
                result = await self._run_fetch(requested, audit)
        except GatewayError as exc:
            audit.outcome = "error"
            audit.error_kind = exc.kind  # message already key-sanitized upstream
            audit.duration_ms = int((time.monotonic() - t0) * 1000)
            self.ledger.record(audit)
            raise

        audit.duration_ms = int((time.monotonic() - t0) * 1000)
        self.ledger.record(audit)
        return result

    # ------------------------------------------------------------------
    # validation

    def _validate_common(
        self,
        tool_name: str,
        allowed: Optional[frozenset[str]],
        requested: dict[str, Any],
    ) -> tuple[Optional[str], Optional[str]]:
        if allowed is None:
            raise ToolRejected("unknown_tool", f"unknown tool: {tool_name}")

        purpose = requested.get("purpose")
        if not isinstance(purpose, str) or not purpose.strip():
            raise ToolRejected("purpose_required", "purpose is required and must be non-empty")

        hypothesis_id = requested.get("hypothesis_id") or None
        coverage_task_id = requested.get("coverage_task_id") or None
        if bool(hypothesis_id) == bool(coverage_task_id):
            raise ToolRejected(
                "target_binding_invalid",
                "exactly one of hypothesis_id or coverage_task_id is required",
            )
        if hypothesis_id and hypothesis_id not in self._context.valid_hypothesis_ids():
            raise ToolRejected(
                "target_binding_invalid", f"unknown hypothesis_id: {hypothesis_id}"
            )
        if coverage_task_id and coverage_task_id not in self._context.valid_task_ids():
            raise ToolRejected(
                "target_binding_invalid", f"unknown coverage_task_id: {coverage_task_id}"
            )

        if self._context.policy == ExternalSearchPolicy.disabled:
            raise ToolRejected(
                "policy_disabled", "external search policy is disabled for this run"
            )
        if self._context.policy == ExternalSearchPolicy.approved_sensitive:
            # Recognized configuration, deliberately fail closed in phase 1.
            raise ToolRejected(
                "policy_not_supported",
                "approved_sensitive policy is recognized but not yet supported; failing closed",
            )
        return hypothesis_id, coverage_task_id

    # ------------------------------------------------------------------
    # tools

    async def _run_search(
        self, requested: dict[str, Any], audit: ToolAuditRecord
    ) -> dict[str, Any]:
        query_text = requested.get("query")
        if not isinstance(query_text, str) or not query_text.strip():
            raise ToolRejected("bad_arguments", "query is required and must be non-empty")

        count: Optional[int] = None
        if requested.get("count") is not None:
            try:
                count = max(1, min(50, int(requested["count"])))
            except (TypeError, ValueError):
                count = None

        query = SearchQuery(
            text=query_text.strip(),
            freshness=requested.get("freshness") or None,
            count=count,
            include_domains=[
                str(d) for d in (requested.get("include_domains") or []) if d
            ],
            exclude_domains=[
                str(d) for d in (requested.get("exclude_domains") or []) if d
            ],
        )
        response, cache_hit = await self._gateway.search(
            query, cache_scope=self._context.injected()
        )
        audit.cache_hit = cache_hit
        audit.domain_filter_mode = response.domain_filter_mode

        projections: list[dict[str, Any]] = []
        for hit in response.hits:
            card = self.ledger.find_by_url("search_snippet", hit.url)
            if card is None:
                published = hit.published_at
                new_card = SourceCard(
                    url=hit.url,
                    title=hit.title,
                    site_name=hit.site_name,
                    origin="search_snippet",
                    snippet=hit.snippet or hit.summary,
                    published_at=published,
                    undated=published is None,
                    is_post_prospectus_update=self._is_post_cutoff(published),
                    tool_call_id=audit.call_id,
                    hypothesis_id=audit.hypothesis_id,
                    coverage_task_id=audit.coverage_task_id,
                )
                self._stamp_tier(new_card)
                card = self.ledger.add_card(new_card)
            projections.append(
                {
                    "evidence_id": card.evidence_id,
                    "title": card.title,
                    "url": card.url,
                    "site_name": card.site_name,
                    "snippet": card.snippet,
                    "published_at": card.published_at,
                }
            )
        audit.result_count = len(projections)
        return {"ok": True, "cache_hit": cache_hit, "hits": projections}

    async def _run_fetch(
        self, requested: dict[str, Any], audit: ToolAuditRecord
    ) -> dict[str, Any]:
        url = requested.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ToolRejected("bad_arguments", "url is required and must be non-empty")
        url = url.strip()

        existing = self.ledger.find_by_url("fetched_page", url)
        if existing is not None:
            audit.dedup_of = existing.evidence_id
            audit.final_url = existing.final_url
            audit.result_count = 1
            return {"ok": True, "deduplicated": True, **self._card_projection(existing)}

        outcome = await self._fetcher.fetch(url)
        published_at = parse_published_date(outcome.published_hint)
        new_card = SourceCard(
            url=url,
            final_url=outcome.final_url,
            title=outcome.title,
            origin="fetched_page",
            extract=outcome.text or None,
            content_hash=outcome.content_hash,
            accessible=outcome.accessible,
            inaccessible_reason=outcome.inaccessible_reason,
            published_at=published_at,
            undated=published_at is None,
            is_post_prospectus_update=self._is_post_cutoff(published_at),
            tool_call_id=audit.call_id,
            hypothesis_id=audit.hypothesis_id,
            coverage_task_id=audit.coverage_task_id,
        )
        self._stamp_tier(new_card)
        card = self.ledger.add_card(new_card)
        audit.http_status = outcome.status_code
        audit.final_url = outcome.final_url
        audit.bytes_read = outcome.bytes_read
        audit.result_count = 1
        return {"ok": True, "deduplicated": False, **self._card_projection(card)}

    def _is_post_cutoff(self, published_at: Optional[str]) -> bool:
        cutoff = self._context.data_cutoff_date
        return bool(published_at and cutoff and published_at > cutoff)

    @staticmethod
    def _card_projection(card: SourceCard) -> dict[str, Any]:
        return {
            "evidence_id": card.evidence_id,
            "url": card.url,
            "final_url": card.final_url,
            "accessible": card.accessible,
            "inaccessible_reason": card.inaccessible_reason,
            "title": card.title,
            "extract": card.extract,
            "published_at": card.published_at,
        }
