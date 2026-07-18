"""Engine tests: skip-when-empty, verify path, weak evidence, transient failure."""

from __future__ import annotations

import asyncio
from typing import Sequence

import httpx
import pytest

from riskagent.research.engine import (
    FetchOperation,
    HypothesisEngine,
    HypothesisProposal,
    ProposerContext,
    SearchOperation,
)
from riskagent.research.gateway import (
    GatewayError,
    ResearchGateway,
    SearchHit,
    SearchQuery,
    SearchResponse,
)
from riskagent.research.cache import InMemoryQueryCache
from riskagent.research.config import ResearchConfig
from riskagent.research.models import (
    ExternalSearchPolicy,
    HypothesisStatus,
    InjectedContext,
    ResearchArtifact,
    ResearchState,
    RiskHypothesis,
    SourceTier,
)
from riskagent.research.ssrf import SafeFetcher
from riskagent.research.store import ResearchStore
from riskagent.research.tools import ResearchToolset, ToolContext, ToolLedger


def run(coro):
    return asyncio.run(coro)


class _ScriptedAdapter:
    """SourceAdapter that returns scripted responses or raises GatewayError."""

    name = "scripted"

    def __init__(self, scripts):
        self._scripts = list(scripts)
        self.calls = 0

    def available(self) -> bool:
        return True

    async def search(self, query: SearchQuery) -> SearchResponse:
        self.calls += 1
        if not self._scripts:
            return SearchResponse(hits=[], provider=self.name)
        item = self._scripts.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _hit(
    url: str = "https://www.hkexnews.hk/x.htm",
    title: str = "监管公告",
    published: str = "2025-03-01",
) -> SearchHit:
    return SearchHit(
        url=url, title=title, site_name="披露易", snippet="...", published_at=published
    )


def _injected(cutoff: str = "2025-12-31") -> InjectedContext:
    return InjectedContext(
        run_id="run_test",
        data_cutoff_date=cutoff,
        policy=ExternalSearchPolicy.public_only,
    )


def _state() -> ResearchState:
    state = ResearchState()
    state.working = ResearchArtifact(artifact_id="art_test", version=1)
    return state


def _build_engine(
    adapter,
    *,
    store=None,
    fetch_routes: dict[str, httpx.Response] | None = None,
):
    state = store.state if store is not None else _state()
    store = store or ResearchStore(state)
    gateway = ResearchGateway(adapter, cache=InMemoryQueryCache())
    routes = fetch_routes or {}

    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url), httpx.Response(404, text="not found"))

    async def fake_resolver(host: str) -> list[str]:
        # Always resolve to a public IP so SSRF private-IP checks pass.
        return ["93.184.216.34"]

    fetcher = SafeFetcher(
        ResearchConfig(),
        transport=httpx.MockTransport(handler),
        resolver=fake_resolver,
    )

    def toolset_factory(ctx: ToolContext) -> ResearchToolset:
        return ResearchToolset(
            gateway=gateway, fetcher=fetcher, context=ctx, ledger=ToolLedger()
        )

    return store, toolset_factory


def _proposer_returning(proposals):
    def _proposer(ctx: ProposerContext):
        return list(proposals)

    return _proposer


def _proposal(hypothesis_id: str = "hyp_a", ops=()):
    hyp = RiskHypothesis(
        hypothesis_id=hypothesis_id,
        title="境外诉讼未披露",
        statement="招股书未披露的重大境外诉讼。",
        dimension="法务监管",
        origin="anomaly",
        rule_ids=[],
        importance="high",
    )
    return HypothesisProposal(hypothesis=hyp, operations=list(ops))


class TestSkipBehaviour:
    def test_returns_none_when_no_signals(self):
        adapter = _ScriptedAdapter([])
        store, factory = _build_engine(adapter)
        engine = HypothesisEngine(
            proposer=_proposer_returning([]), toolset_factory=factory, store=store
        )
        result = run(
            engine.run_module(
                module_id="m1",
                agent_name="legal",
                company_name="某公司",
                injected=_injected(),
            )
        )
        assert result is None

    def test_returns_none_when_proposer_returns_empty(self):
        adapter = _ScriptedAdapter([])
        store, factory = _build_engine(adapter)
        engine = HypothesisEngine(
            proposer=_proposer_returning([]), toolset_factory=factory, store=store
        )
        result = run(
            engine.run_module(
                module_id="m1",
                agent_name="legal",
                company_name="某公司",
                coverage_tasks=[],
                screening_observations=["异常: 境外诉讼"],
                injected=_injected(),
            )
        )
        assert result is None


class TestVerifyPath:
    def test_refutation_search_marked_in_audit(self):
        adapter = _ScriptedAdapter(
            [
                SearchResponse(hits=[_hit()], provider="scripted"),
                SearchResponse(hits=[], provider="scripted"),
            ]
        )
        store, factory = _build_engine(
            adapter,
            fetch_routes={
                "https://www.hkexnews.hk/x.htm": httpx.Response(
                    200, html="<html><body>公告正文</body></html>"
                )
            },
        )
        proposal = _proposal(
            ops=[
                SearchOperation(query="某公司 境外诉讼", purpose="初查"),
                FetchOperation(url="https://www.hkexnews.hk/x.htm", purpose="抓取 tier1 公告"),
                SearchOperation(
                    query="某公司 境外诉讼 反驳",
                    purpose="反证检索",
                    refutation=True,
                ),
            ]
        )
        engine = HypothesisEngine(
            proposer=_proposer_returning([proposal]),
            toolset_factory=factory,
            store=store,
        )
        result = run(
            engine.run_module(
                module_id="m1",
                agent_name="legal",
                company_name="某公司",
                screening_observations=["异常"],
                injected=_injected(),
            )
        )
        assert result is not None
        refutation_audits = [
            a
            for a in result.audit
            if a.tool == "bocha_web_search"
            and "反证" in (a.requested.get("purpose") or "")
        ]
        assert len(refutation_audits) == 1
        # Both search calls + fetch call audited
        assert len([a for a in result.audit if a.tool == "bocha_web_search"]) == 2
        assert len([a for a in result.audit if a.tool == "fetch_web_source"]) == 1
        # A fetched card exists (the SafeFetcher returned 200 text/html)
        fetched = [c for c in result.evidence if c.origin == "fetched_page"]
        assert len(fetched) == 1
        assert fetched[0].accessible is True


class TestWeakEvidence:
    def test_search_only_does_not_verify(self):
        adapter = _ScriptedAdapter(
            [SearchResponse(hits=[_hit()], provider="scripted")]
        )
        store, factory = _build_engine(adapter)
        proposal = _proposal(ops=[SearchOperation(query="某公司", purpose="初查")])
        engine = HypothesisEngine(
            proposer=_proposer_returning([proposal]),
            toolset_factory=factory,
            store=store,
        )
        result = run(
            engine.run_module(
                module_id="m1",
                agent_name="legal",
                company_name="某公司",
                screening_observations=["异常"],
                injected=_injected(),
            )
        )
        assert result is not None
        # No fetch operations → only search_snippet cards
        assert all(c.origin == "search_snippet" for c in result.evidence)
        hyp = result.hypotheses[0]
        # No fetched evidence → inconclusive (gate cannot pass)
        assert hyp.status == HypothesisStatus.inconclusive
        assert hyp.gate is not None
        assert hyp.gate.passed is False
        # Engine recorded final transition
        assert any(
            t.to_status == HypothesisStatus.inconclusive and t.actor == "engine"
            for t in result.transitions
        )


class TestTransientFailure:
    def test_transient_error_marks_external_incomplete(self):
        adapter = _ScriptedAdapter(
            [GatewayError("rate_limited", "throttled")]
        )
        store, factory = _build_engine(adapter)
        proposal = _proposal(ops=[SearchOperation(query="某公司", purpose="初查")])
        engine = HypothesisEngine(
            proposer=_proposer_returning([proposal]),
            toolset_factory=factory,
            store=store,
        )
        result = run(
            engine.run_module(
                module_id="m1",
                agent_name="legal",
                company_name="某公司",
                screening_observations=["异常"],
                injected=_injected(),
            )
        )
        assert result is not None
        assert result.external_incomplete is True
        assert result.failure_reason is not None
        assert "rate_limited" in result.failure_reason
        # Hypothesis transitioned to inconclusive by system actor
        assert any(
            t.to_status == HypothesisStatus.inconclusive and t.actor == "system"
            for t in result.transitions
        )


class TestAuditAndTransitionsRecorded:
    def test_audit_covers_every_call(self):
        adapter = _ScriptedAdapter(
            [
                SearchResponse(hits=[_hit()], provider="scripted"),
                SearchResponse(hits=[], provider="scripted"),
            ]
        )
        store, factory = _build_engine(adapter)
        proposal = _proposal(
            ops=[
                SearchOperation(query="q1", purpose="p1"),
                SearchOperation(query="q2", purpose="p2", refutation=True),
            ]
        )
        engine = HypothesisEngine(
            proposer=_proposer_returning([proposal]),
            toolset_factory=factory,
            store=store,
        )
        result = run(
            engine.run_module(
                module_id="m1",
                agent_name="legal",
                company_name="某公司",
                screening_observations=["异常"],
                injected=_injected(),
            )
        )
        assert result is not None
        search_audits = [a for a in result.audit if a.tool == "bocha_web_search"]
        assert len(search_audits) == 2
        statuses = [(t.from_status, t.to_status) for t in result.transitions]
        assert (HypothesisStatus.discovered, HypothesisStatus.investigating) in statuses
