"""The two controlled tools: injection, XOR binding, policy, audit, dedup."""

from __future__ import annotations

import asyncio

import httpx

from riskagent.research.config import ResearchConfig
from riskagent.research.gateway import (
    ResearchGateway,
    SearchHit,
    SearchQuery,
    SearchResponse,
)
from riskagent.research.cache import InMemoryQueryCache
from riskagent.research.models import ExternalSearchPolicy
from riskagent.research.ssrf import SafeFetcher
from riskagent.research.tools import (
    TOOL_BOCHA_WEB_SEARCH,
    TOOL_FETCH_WEB_SOURCE,
    ResearchToolset,
    ToolContext,
    ToolLedger,
)


def run(coro):
    return asyncio.run(coro)


class FakeAdapter:
    """A minimal SourceAdapter returning canned hits and counting calls."""

    name = "fake"

    def __init__(self, hits: list[SearchHit] | None = None) -> None:
        self.calls = 0
        self._hits = hits if hits is not None else [
            SearchHit(
                url="https://www.hkexnews.hk/a.htm",
                title="公告A",
                site_name="披露易",
                snippet="摘要A",
                published_at="2025-05-01",
            ),
            SearchHit(
                url="https://www.caixin.com/b.html",
                title="报道B",
                site_name="财新",
                snippet="摘要B",
                published_at=None,
            ),
        ]

    def available(self) -> bool:
        return True

    async def search(self, query: SearchQuery) -> SearchResponse:
        self.calls += 1
        return SearchResponse(hits=list(self._hits), provider=self.name)


def _context(policy=ExternalSearchPolicy.public_only, valid_h={"hyp_1"}, valid_t={"cov_1"}) -> ToolContext:
    return ToolContext(
        run_id="run-123",
        data_cutoff_date="2025-06-30",
        policy=policy,
        module_id="mod-financial",
        agent_name="agent_x",
        valid_hypothesis_ids=lambda: set(valid_h),
        valid_task_ids=lambda: set(valid_t),
    )


def _toolset(*, context=None, gateway=None, ledger=None) -> ResearchToolset:
    ctx = context or _context()
    gw = gateway or ResearchGateway(FakeAdapter(), cache=InMemoryQueryCache())
    fetcher = SafeFetcher(
        ResearchConfig(), transport=httpx.MockTransport(lambda r: httpx.Response(404))
    )
    return ResearchToolset(
        gateway=gw, fetcher=fetcher, context=ctx, ledger=ledger or ToolLedger()
    )


class TestSchema:
    def test_schema_hides_injected_params(self):
        schemas = ResearchToolset.openai_tool_schemas()
        blob = str(schemas)
        assert "run_id" not in blob
        assert "data_cutoff_date" not in blob
        assert "policy" not in blob
        names = {s["function"]["name"] for s in schemas}
        assert names == {TOOL_BOCHA_WEB_SEARCH, TOOL_FETCH_WEB_SOURCE}


class TestValidation:
    def test_purpose_required(self):
        ts = _toolset()
        result = run(
            ts.dispatch(TOOL_BOCHA_WEB_SEARCH, {"query": "x", "hypothesis_id": "hyp_1"})
        )
        assert result["ok"] is False
        assert ts.ledger.audit[-1].outcome == "rejected"
        assert ts.ledger.audit[-1].error_kind == "purpose_required"

    def test_xor_both_present_rejected(self):
        ts = _toolset()
        result = run(
            ts.dispatch(
                TOOL_BOCHA_WEB_SEARCH,
                {
                    "query": "x",
                    "purpose": "p",
                    "hypothesis_id": "hyp_1",
                    "coverage_task_id": "cov_1",
                },
            )
        )
        assert result["ok"] is False
        assert ts.ledger.audit[-1].error_kind == "target_binding_invalid"

    def test_xor_neither_present_rejected(self):
        ts = _toolset()
        result = run(ts.dispatch(TOOL_BOCHA_WEB_SEARCH, {"query": "x", "purpose": "p"}))
        assert result["ok"] is False
        assert ts.ledger.audit[-1].error_kind == "target_binding_invalid"

    def test_unknown_hypothesis_id_rejected(self):
        ts = _toolset()
        result = run(
            ts.dispatch(
                TOOL_BOCHA_WEB_SEARCH,
                {"query": "x", "purpose": "p", "hypothesis_id": "ghost"},
            )
        )
        assert result["ok"] is False
        assert ts.ledger.audit[-1].error_kind == "target_binding_invalid"


class TestInjection:
    def test_model_supplied_run_id_stripped_and_recorded(self):
        ts = _toolset()
        run(
            ts.dispatch(
                TOOL_BOCHA_WEB_SEARCH,
                {
                    "query": "x",
                    "purpose": "p",
                    "hypothesis_id": "hyp_1",
                    "run_id": "attacker-run",
                    "data_cutoff_date": "2099-01-01",
                    "policy": "approved_sensitive",
                },
            )
        )
        audit = ts.ledger.audit[-1]
        assert audit.injected.run_id == "run-123"
        assert audit.injected.data_cutoff_date == "2025-06-30"
        assert audit.injected.policy is ExternalSearchPolicy.public_only
        assert set(audit.rejected_params) == {"run_id", "data_cutoff_date", "policy"}
        assert "run_id" not in audit.requested


class TestPolicy:
    def test_disabled_rejects_everything(self):
        ts = _toolset(context=_context(policy=ExternalSearchPolicy.disabled))
        result = run(
            ts.dispatch(
                TOOL_BOCHA_WEB_SEARCH,
                {"query": "x", "purpose": "p", "hypothesis_id": "hyp_1"},
            )
        )
        assert result["ok"] is False
        assert ts.ledger.audit[-1].error_kind == "policy_disabled"

    def test_approved_sensitive_fails_closed(self):
        ts = _toolset(context=_context(policy=ExternalSearchPolicy.approved_sensitive))
        result = run(
            ts.dispatch(
                TOOL_BOCHA_WEB_SEARCH,
                {"query": "x", "purpose": "p", "hypothesis_id": "hyp_1"},
            )
        )
        assert result["ok"] is False
        assert ts.ledger.audit[-1].error_kind == "policy_not_supported"


class TestSearchBehaviour:
    def test_search_creates_cards_and_projection(self):
        ts = _toolset()
        result = run(
            ts.dispatch(
                TOOL_BOCHA_WEB_SEARCH,
                {"query": "诉讼", "purpose": "初查", "coverage_task_id": "cov_1"},
            )
        )
        assert result["ok"] is True
        assert len(result["hits"]) == 2
        assert set(result["hits"][0]) == {
            "evidence_id",
            "title",
            "url",
            "site_name",
            "snippet",
            "published_at",
        }
        assert len(ts.ledger.evidence) == 2
        assert all(c.origin == "search_snippet" for c in ts.ledger.evidence)

    def test_cache_hit_recorded_second_call(self):
        gw = ResearchGateway(FakeAdapter(), cache=InMemoryQueryCache())
        ledger = ToolLedger()
        ts = _toolset(gateway=gw, ledger=ledger)
        run(ts.dispatch(TOOL_BOCHA_WEB_SEARCH, {"query": "诉讼", "purpose": "p", "coverage_task_id": "cov_1"}))
        run(ts.dispatch(TOOL_BOCHA_WEB_SEARCH, {"query": "诉讼", "purpose": "p", "coverage_task_id": "cov_1"}))
        assert ts.ledger.audit[0].cache_hit is False
        assert ts.ledger.audit[1].cache_hit is True
        assert gw.adapter.calls == 1

    def test_cache_key_includes_cutoff(self):
        adapter = FakeAdapter()
        gw = ResearchGateway(adapter, cache=InMemoryQueryCache())
        ledger = ToolLedger()
        ts1 = _toolset(gateway=gw, ledger=ledger, context=_context())
        run(ts1.dispatch(TOOL_BOCHA_WEB_SEARCH, {"query": "q", "purpose": "p", "coverage_task_id": "cov_1"}))
        ctx2 = ToolContext(
            run_id="run-123",
            data_cutoff_date="2024-01-01",
            policy=ExternalSearchPolicy.public_only,
            module_id="m",
            agent_name="a",
            valid_hypothesis_ids=lambda: {"hyp_1"},
            valid_task_ids=lambda: {"cov_1"},
        )
        ts2 = ResearchToolset(gateway=gw, fetcher=ts1._fetcher, context=ctx2, ledger=ledger)
        run(ts2.dispatch(TOOL_BOCHA_WEB_SEARCH, {"query": "q", "purpose": "p", "coverage_task_id": "cov_1"}))
        assert adapter.calls == 2

    def test_url_dedup_within_run(self):
        adapter = FakeAdapter(hits=[SearchHit(url="https://www.hkexnews.hk/x.htm", title="A")])
        gw = ResearchGateway(adapter, cache=None)
        ledger = ToolLedger()
        ts = _toolset(gateway=gw, ledger=ledger)
        run(ts.dispatch(TOOL_BOCHA_WEB_SEARCH, {"query": "q1", "purpose": "p", "coverage_task_id": "cov_1"}))
        first_id = ledger.evidence[0].evidence_id
        run(ts.dispatch(TOOL_BOCHA_WEB_SEARCH, {"query": "q2", "purpose": "p", "coverage_task_id": "cov_1"}))
        assert len(ledger.evidence) == 1
        assert ledger.evidence[0].evidence_id == first_id


class TestAuditCompleteness:
    def test_every_call_including_rejected_is_audited(self):
        ts = _toolset()
        run(ts.dispatch(TOOL_BOCHA_WEB_SEARCH, {"query": "x"}))  # rejected: no purpose
        run(ts.dispatch(TOOL_BOCHA_WEB_SEARCH, {"query": "x", "purpose": "p", "coverage_task_id": "cov_1"}))
        assert len(ts.ledger.audit) == 2
        assert ts.ledger.audit[0].outcome == "rejected"
        assert ts.ledger.audit[1].outcome == "ok"
        assert all(a.injected.run_id == "run-123" for a in ts.ledger.audit)
