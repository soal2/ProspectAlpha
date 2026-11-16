import asyncio
import json
from types import SimpleNamespace

import httpx

from riskagent.hidden_risk import ResearchBudget, RiskHypothesis
from riskagent.prediction.scorecard import WEIGHT_PROFILE, calculate_hidden_first_score
from riskagent.reporting.flexible import build_report_document
from riskagent.research import (
    BochaAdapter,
    BochaConfig,
    FetchOutcome,
    HypothesisResearchModule,
    SafeFetcher,
    SearchHit,
    SearchResponse,
    is_pre_cutoff_evidence,
    research_ids_for_scoring,
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


def test_unknown_and_post_cutoff_sources_stay_out_of_evidence_and_score():
    class MixedAdapter:
        name = "mixed"

        def available(self) -> bool:
            return True

        async def search(self, query):
            return SearchResponse(
                provider="mixed",
                hits=[
                    SearchHit(
                        url="https://example.com/before",
                        title="测试公司经销增长可能依赖压货",
                        summary="库存",
                        published_at="2024-01-02",
                    ),
                    SearchHit(
                        url="https://example.com/after",
                        title="测试公司截止后处罚",
                        summary="处罚公告",
                        published_at="2025-02-02",
                    ),
                    SearchHit(
                        url="https://example.com/unknown",
                        title="测试公司日期不明诉讼",
                        summary="诉讼",
                        published_at=None,
                    ),
                ],
            )

    class MixedFetcher:
        async def fetch(self, url: str) -> FetchOutcome:
            published = {
                "https://example.com/before": "2024-01-02",
                "https://example.com/after": "2025-02-02",
            }.get(url)
            return FetchOutcome(
                requested_url=url,
                final_url=url,
                accessible=True,
                title=url.rsplit("/", 1)[-1],
                text="测试公司经销增长可能依赖压货，库存风险已有外部事件支持",
                published_at=published,
                content_hash=url,
                bytes_read=16,
            )

    result = asyncio.run(
        HypothesisResearchModule(MixedAdapter(), MixedFetcher()).run(
            company_name="测试公司",
            hypotheses=[_hypothesis()],
            budget=ResearchBudget.for_depth("quick"),
            data_cutoff_date="2024-12-31",
        )
    )
    hypothesis = result.hypotheses[0]
    assert hypothesis.status == "corroborated"
    admitted_ids = set(hypothesis.external_evidence_ids)
    before = [item for item in result.sources if item.url.endswith("/before")]
    after = [item for item in result.sources if item.url.endswith("/after")]
    unknown = [item for item in result.sources if item.url.endswith("/unknown")]
    assert any(item.source_id in admitted_ids and item.query_kind == "support" for item in before)
    assert all(item.source_id not in admitted_ids for item in [*after, *unknown])
    assert all(item.eligible_pre_cutoff for item in before)
    assert all(not item.eligible_pre_cutoff for item in [*after, *unknown])

    admitted = {
        "dimension_code": "EXT",
        "origin": "external",
        "severity": "high",
        "evidence_grade": "direct",
        "hypothesis_status": hypothesis.status,
        "external_evidence_ids": hypothesis.external_evidence_ids,
        "counter_evidence_ids": hypothesis.counter_evidence_ids,
    }
    withheld = {**admitted, "hypothesis_status": "reported", "external_evidence_ids": []}
    assert calculate_hidden_first_score([admitted])["breakdown"]["EXT"] > calculate_hidden_first_score([withheld])["breakdown"]["EXT"]
    assert "example.com/after" not in json.dumps(admitted)
    assert "example.com/unknown" not in json.dumps(admitted)
    assert set(research_ids_for_scoring(result.sources)) == {"https://example.com/before"}
    assert WEIGHT_PROFILE == {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0}

    document = build_report_document(
        SimpleNamespace(
            evidence=[],
            findings=[],
            hypotheses=[],
            research_sources=result.sources,
            research_limitations=[],
            company_name="测试公司",
        ),
        SimpleNamespace(risk_themes=[], executive_summary="摘要", limitations=[]),
    )
    index_blob = json.dumps(document["evidence_index"], ensure_ascii=False)
    assert "example.com/before" in index_blob
    assert "example.com/after" not in index_blob
    assert "example.com/unknown" not in index_blob
    assert "测试公司截止后处罚" not in index_blob
    assert "测试公司日期不明诉讼" not in index_blob


def test_post_listing_attribution_is_not_pre_cutoff_evidence():
    class PostAdapter:
        name = "post"

        def available(self) -> bool:
            return True

        async def search(self, query):
            return SearchResponse(
                provider="post",
                hits=[
                    SearchHit(
                        url="https://example.com/post-event",
                        title="测试公司上市后处罚",
                        published_at="2025-06-01",
                    )
                ],
            )

    class PostFetcher:
        async def fetch(self, url: str) -> FetchOutcome:
            return FetchOutcome(
                requested_url=url,
                final_url=url,
                accessible=True,
                title="上市后处罚",
                text="测试公司上市后处罚成立",
                published_at="2025-06-01",
                content_hash="post-hash",
                bytes_read=8,
            )

    result = asyncio.run(
        HypothesisResearchModule(PostAdapter(), PostFetcher()).research_retrospective(
            company_name="测试公司",
            risk_titles=["经销增长可能依赖压货"],
            listing_date="2025-01-01",
        )
    )

    assert result["sources"]
    assert all(item["attribution"] == "post_listing" for item in result["sources"])
    assert all(item["eligible_pre_cutoff"] is False for item in result["sources"])
    assert all(not is_pre_cutoff_evidence(item) for item in result["sources"])
    assert research_ids_for_scoring(result["sources"]) == []


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
