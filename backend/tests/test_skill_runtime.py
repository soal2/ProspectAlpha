import json

import pytest

from riskagent.database import BACKEND_ROOT
from riskagent.features.financial import extract_cashflow_periods
from riskagent.prediction.scorecard import WEIGHT_PROFILE, calculate_hidden_first_score
from riskagent.skill_docs import build_skill_catalog
from riskagent.dataset_registry import lookup_subscription_history
from riskagent.skill_runtime import (
    CashflowAnalyzerHandler,
    SentimentScorerHandler,
    SkillInvocation,
    SkillRuntime,
    ValuationCheckerHandler,
    normalize_stock_code,
)
from riskagent.research import SearchHit, SearchResponse


def _invocation(skill_id: str, **updates):
    payload = {
        "run_id": "run_test",
        "assignment_id": "module_test",
        "agent_name": "qin_bingwen_financial_agent",
        "skill_id": skill_id,
        "skill_version": "1.0.0",
        "stock_code": "0700.HK",
        "company_name": "测试公司",
    }
    payload.update(updates)
    return SkillInvocation.model_validate(payload)


def test_catalog_joins_docs_and_explicit_handlers():
    catalog = build_skill_catalog()
    assert catalog.definitions["cashflow-analyzer"].handler is not None
    assert catalog.definitions["backtest"].handler is None
    assert "backtest" not in {item.name for item in catalog.runtime_skills()}


def test_stock_code_normalization_is_explicit_and_compatible():
    assert normalize_stock_code("0700") == "0700.HK"
    assert normalize_stock_code("0700.hk") == "0700.HK"
    assert normalize_stock_code(None) is None


def test_cashflow_handler_never_invents_missing_financials():
    result = CashflowAnalyzerHandler().invoke(_invocation("cashflow-analyzer"))
    assert result.status == "insufficient_data"
    assert "示例" in result.limitations[0]


class _FakeBocha:
    name = "fake"

    def available(self) -> bool:
        return True

    async def search(self, query):
        return SearchResponse(
            provider="fake",
            hits=[
                SearchHit(
                    url="https://www1.hkexnews.hk/example",
                    title="测试公司公告",
                    summary="测试摘要",
                    site_name="港交所",
                    published_at="2024-06-01",
                )
            ],
        )


def test_sentiment_freezes_bocha_results_as_research_artifacts():
    handler = SentimentScorerHandler(_FakeBocha())
    result = handler.invoke(
        _invocation(
            "sentiment-scorer",
            agent_name="lu_guanlan_market_agent",
            data_cutoff_date="2024-12-31",
            structured_inputs={
                "subscription_history": {
                    "subscription_ratio": 12.0,
                    "announcement_date": "2024-01-01",
                }
            },
        )
    )
    assert result.status == "completed"
    artifacts = result.result["research_artifacts"]
    assert len(artifacts) == 1
    assert artifacts[0]["run_id"] == "run_test"
    assert artifacts[0]["source_hash"]


_IPO_WORKBOOK = BACKEND_ROOT / "skills" / "sentiment_scorer" / "references" / "IPO_data.xlsx"
_requires_ipo_workbook = pytest.mark.skipif(
    not _IPO_WORKBOOK.is_file(),
    reason="IPO_data.xlsx is a local gitignored dataset and is not in this checkout",
)


@_requires_ipo_workbook
def test_subscription_history_uses_versioned_local_dataset():
    history = lookup_subscription_history("6871")
    assert history is not None
    assert history["stock_code"] == "6871.HK"
    assert history["subscription_ratio"] == 3713.8495


@_requires_ipo_workbook
def test_sentiment_matches_local_history_without_structured_fallback():
    handler = SentimentScorerHandler(_FakeBocha())
    result = handler.invoke(
        _invocation(
            "sentiment-scorer",
            stock_code="6871.HK",
            agent_name="lu_guanlan_market_agent",
            data_cutoff_date="2099-01-01",
        )
    )
    assert result.result["news_count"] == 1
    heat = result.result["subscription_heat"]
    if heat is not None:
        assert heat["stock_code"] == "6871.HK"
        assert heat["subscription_ratio"] == 3713.8495
        assert heat["eligible_pre_cutoff"] is True


@_requires_ipo_workbook
def test_valuation_uses_versioned_historical_peers_by_industry():
    result = ValuationCheckerHandler().invoke(
        _invocation(
            "valuation-checker",
            structured_inputs={
                "industry": "机械",
                "issuer_valuation": {"pe": 40.0, "valuation_basis": "issue"},
            },
            data_cutoff_date="2026-05-15",
        )
    )
    assert result.result["valuation_basis"] == "issue"
    assert result.result["peer_source"] == "hk-ipo-subscription-history@1.0.0"
    if result.status == "completed":
        assert result.result["issue_valuation"]["peer_count"] >= 1
        assert result.result["premium_percent"] == result.result["issue_valuation"]["premium_percent"]
    else:
        assert result.status == "insufficient_data"
        assert result.result["premium_percent"] is None
        assert result.risk_level == "unrated"


def test_runtime_normalizes_handler_failures():
    class Broken:
        def invoke(self, invocation):
            raise RuntimeError("boom")

    result = SkillRuntime({"cashflow-analyzer": Broken()}).invoke(
        _invocation("cashflow-analyzer")
    )
    assert result.status == "failed"
    assert result.limitations == ["boom"]


def test_post_cutoff_and_unknown_dates_do_not_enter_context_evidence_or_score():
    cutoff = "2024-06-30"
    peers = [
        {
            "stock_code": "1111.HK",
            "industry": "机械",
            "valuation_basis": "issue",
            "as_of_date": "2024-01-15",
            "announcement_date": "2024-01-15",
            "pe": 20,
            "company_name": "发行同行",
        },
        {
            "stock_code": "2222.HK",
            "industry": "机械",
            "valuation_basis": "issue",
            "as_of_date": "2024-12-01",
            "announcement_date": "2024-12-01",
            "pe": 80,
            "company_name": "截止后同行",
        },
        {
            "stock_code": "3333.HK",
            "industry": "机械",
            "valuation_basis": "issue",
            "pe": 5,
            "company_name": "日期不明同行",
        },
        {
            "stock_code": "4444.HK",
            "industry": "机械",
            "valuation_basis": "first_day",
            "listing_date": "2024-03-01",
            "as_of_date": "2024-03-01",
            "pe": 100,
            "company_name": "首日同行",
        },
        {
            "stock_code": "5555.HK",
            "industry": "机械",
            "valuation_basis": "first_day",
            "listing_date": "2025-01-01",
            "as_of_date": "2025-01-01",
            "pe": 200,
            "company_name": "上市后同行",
        },
        {
            "stock_code": "6666.HK",
            "industry": "机械",
            "valuation_basis": "first_day",
            "pe": 300,
            "company_name": "首日日期不明",
        },
    ]
    valuation = ValuationCheckerHandler().invoke(
        _invocation(
            "valuation-checker",
            data_cutoff_date=cutoff,
            structured_inputs={
                "industry": "机械",
                "issuer_valuation": {"pe": 30, "valuation_basis": "issue"},
                "peer_valuations": peers,
            },
        )
    )
    blob = json.dumps(valuation.model_dump(), ensure_ascii=False)
    for secret in ("2222.HK", "截止后同行", "3333.HK", "日期不明同行", "5555.HK", "上市后同行", "6666.HK", "首日日期不明"):
        assert secret not in blob
    issue_blob = json.dumps(valuation.result["issue_valuation"], ensure_ascii=False)
    assert "1111.HK" in issue_blob
    assert "4444.HK" not in issue_blob
    assert "4444.HK" in json.dumps(valuation.result["first_day_valuation"], ensure_ascii=False)
    assert valuation.result["peer_median"] == 20
    assert valuation.result["premium_percent"] == 50
    assert valuation.result["first_day_valuation"]["premium_percent"] is None
    assert valuation.evidence_refs == []

    class _MixedNews:
        name = "mixed"

        def available(self) -> bool:
            return True

        async def search(self, query):
            return SearchResponse(
                provider="mixed",
                hits=[
                    SearchHit(
                        url="https://example.com/before",
                        title="截止日前新闻",
                        summary="截止日前摘要",
                        published_at="2024-02-01",
                        site_name="新闻",
                    ),
                    SearchHit(
                        url="https://example.com/after",
                        title="截止后新闻",
                        summary="截止后摘要",
                        published_at="2024-12-01",
                        site_name="新闻",
                    ),
                    SearchHit(
                        url="https://example.com/unknown",
                        title="日期不明新闻",
                        summary="日期不明摘要",
                        site_name="新闻",
                    ),
                ],
            )

    sentiment = SentimentScorerHandler(_MixedNews()).invoke(
        _invocation(
            "sentiment-scorer",
            agent_name="lu_guanlan_market_agent",
            data_cutoff_date=cutoff,
            structured_inputs={
                "subscription_history": {
                    "stock_code": "0700.HK",
                    "announcement_date": "2024-12-31",
                    "subscription_ratio": 99,
                }
            },
        )
    )
    sentiment_blob = json.dumps(sentiment.model_dump(), ensure_ascii=False)
    assert "https://example.com/before" in sentiment_blob
    for secret in ("https://example.com/after", "截止后新闻", "截止后摘要", "日期不明新闻", "日期不明摘要"):
        assert secret not in sentiment_blob
    assert sentiment.result["subscription_heat"] is None
    assert sentiment.result["excluded_source_count"] == 2
    assert sentiment.evidence_refs
    assert all(item["eligible_pre_cutoff"] is True for item in sentiment.result["research_artifacts"])

    admitted = {
        "dimension_code": "EXT",
        "origin": "external",
        "severity": "high",
        "evidence_grade": "direct",
        "hypothesis_status": "corroborated",
        "external_evidence_ids": list(sentiment.evidence_refs),
        "counter_evidence_ids": [],
        "title": "截止日前外部共振",
    }
    withheld = {
        **admitted,
        "hypothesis_status": "reported",
        "external_evidence_ids": [],
        "title": "日期不明或截止后不得抬分",
    }
    assert calculate_hidden_first_score([admitted])["breakdown"]["EXT"] > calculate_hidden_first_score([withheld])["breakdown"]["EXT"]
    assert calculate_hidden_first_score([admitted]) == calculate_hidden_first_score(
        [{**admitted, "external_evidence_ids": [item["artifact_id"] for item in sentiment.result["research_artifacts"]]}]
    )
    assert "example.com/after" not in json.dumps(admitted)
    assert WEIGHT_PROFILE == {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0}


def test_runtime_rejects_skill_outside_frozen_agent_binding():
    snapshot = build_skill_catalog().to_run_snapshot()
    invocation = _invocation(
        "sentiment-scorer",
        agent_name="qin_bingwen_financial_agent",
    )
    try:
        SkillRuntime(skill_snapshot=snapshot).invoke(invocation)
    except ValueError as exc:
        assert "not assigned" in str(exc)
    else:
        raise AssertionError("policy violation must be rejected")
