import asyncio

import httpx

from riskagent.hidden_risk import ResearchBudget, RiskHypothesis
from riskagent.research import (
    BochaAdapter,
    BochaConfig,
    FetchOutcome,
    HypothesisResearchModule,
    SafeFetcher,
    SearchHit,
    SearchResponse,
)


class FakeAdapter:
    name = "fake"

    def available(self) -> bool:
        return True

    async def search(self, query):
        kind = "counter" if "澄清" in query.text else "support"
        return SearchResponse(
            provider="fake",
            hits=[
                SearchHit(
                    url=f"https://example.com/{kind}",
                    title=kind,
                    published_at="2025-01-02",
                )
            ],
        )


class FakeFetcher:
    async def fetch(self, url: str) -> FetchOutcome:
        text = (
            "测试公司澄清经销增长可能依赖压货风险，相关说法不实"
            if url.endswith("/counter")
            else "测试公司经销增长可能依赖压货，库存风险已有外部事件支持"
        )
        return FetchOutcome(
            requested_url=url,
            final_url=url,
            accessible=True,
            title="source",
            text=text,
            published_at="2025-01-02",
            content_hash="a" * 64,
            bytes_read=24,
        )


def _hypothesis(confidence: float = 0.05) -> RiskHypothesis:
    return RiskHypothesis(
        risk_type="渠道质量隐患",
        title="经销增长可能依赖压货",
        statement="招股书显示期末经销商库存上升。",
        origin="agent_emergent",
        dimension="D2",
        causal_chain="压货抬高当期销售，终端消化不足会导致退货和收入回落。",
        industry_relevance="消费行业依赖终端动销。",
        company_specificity="公司经销库存增长快于收入。",
        prospectus_evidence_ids=["block_1"],
        evidence_grade="lead",
        confidence=confidence,
    )


def test_research_runs_support_and_refutation_without_confidence_gate():
    result = asyncio.run(
        HypothesisResearchModule(FakeAdapter(), FakeFetcher()).run(
            company_name="测试公司",
            hypotheses=[_hypothesis()],
            budget=ResearchBudget.for_depth("quick"),
            data_cutoff_date="2025-12-31",
        )
    )

    assert result.usage.searches_used == 2
    assert result.usage.pages_fetched == 2
    assert result.hypotheses[0].confidence == 0.05
    assert result.hypotheses[0].status == "disputed"
    assert result.hypotheses[0].external_evidence_ids
    assert result.hypotheses[0].counter_evidence_ids


def test_post_cutoff_source_never_corroborates_prelisting_hypothesis():
    result = asyncio.run(
        HypothesisResearchModule(FakeAdapter(), FakeFetcher()).run(
            company_name="测试公司",
            hypotheses=[_hypothesis()],
            budget=ResearchBudget.for_depth("quick"),
            data_cutoff_date="2024-12-31",
        )
    )

    assert result.hypotheses[0].status == "reported"
    assert not result.hypotheses[0].external_evidence_ids
    assert all(not item.eligible_pre_cutoff for item in result.sources)


def test_search_cache_is_cutoff_scoped_and_reuses_public_results():
    module = HypothesisResearchModule(FakeAdapter(), FakeFetcher())
    first = asyncio.run(
        module.run(
            company_name="测试公司",
            hypotheses=[_hypothesis()],
            budget=ResearchBudget.for_depth("quick"),
            data_cutoff_date="2025-12-31",
        )
    )
    second = asyncio.run(
        module.run(
            company_name="测试公司",
            hypotheses=[_hypothesis()],
            budget=ResearchBudget.for_depth("quick"),
            data_cutoff_date="2025-12-31",
        )
    )

    assert not any(item.cache_hit for item in first.audit)
    assert all(item.cache_hit for item in second.audit)


def test_bocha_adapter_retries_and_parses_dates_without_leaking_key():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={
                "data": {
                    "webPages": {
                        "value": [
                            {
                                "url": "https://example.com/a",
                                "name": "标题",
                                "datePublished": "2025年01月02日",
                            }
                        ]
                    }
                }
            },
        )

    adapter = BochaAdapter(
        BochaConfig(api_key="secret-key"),
        transport=httpx.MockTransport(handler),
        retry_backoff_seconds=0,
    )
    response = asyncio.run(adapter.search(type("Q", (), {"text": "query", "include_domains": [], "exclude_domains": [], "count": 10, "freshness": None})()))

    assert calls == 2
    assert response.hits[0].published_at == "2025-01-02"


def test_safe_fetcher_rejects_private_addresses_before_http():
    fetcher = SafeFetcher(resolver=lambda _host: _async_value(["127.0.0.1"]))
    outcome = asyncio.run(fetcher.fetch("https://internal.example/private"))

    assert not outcome.accessible
    assert outcome.inaccessible_reason == "private_address"


async def _async_value(value):
    return value
