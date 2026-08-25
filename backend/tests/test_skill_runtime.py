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
            hits=[SearchHit(url="https://www1.hkexnews.hk/example", title="测试公司公告", summary="测试摘要", site_name="港交所")],
        )


def test_sentiment_freezes_bocha_results_as_research_artifacts():
    handler = SentimentScorerHandler(_FakeBocha())
    result = handler.invoke(
        _invocation(
            "sentiment-scorer",
            agent_name="lu_guanlan_market_agent",
            structured_inputs={"subscription_history": {"subscription_ratio": 12.0}},
        )
    )
    assert result.status == "completed"
    artifacts = result.result["research_artifacts"]
    assert len(artifacts) == 1
    assert artifacts[0]["run_id"] == "run_test"
    assert artifacts[0]["source_hash"]


def test_subscription_history_uses_versioned_local_dataset():
    history = lookup_subscription_history("6871")
    assert history is not None
    assert history["stock_code"] == "6871.HK"
    assert history["subscription_ratio"] == 3713.8495


def test_sentiment_matches_local_history_without_structured_fallback():
    handler = SentimentScorerHandler(_FakeBocha())
    result = handler.invoke(
        _invocation(
            "sentiment-scorer",
            stock_code="6871.HK",
            agent_name="lu_guanlan_market_agent",
        )
    )
    assert result.status == "completed"
    assert result.result["subscription_heat"]["stock_code"] == "6871.HK"


def test_valuation_uses_versioned_historical_peers_by_industry():
    result = ValuationCheckerHandler().invoke(
        _invocation(
            "valuation-checker",
            structured_inputs={
                "industry": "机械",
                "issuer_valuation": {"pe": 40.0},
            },
        )
    )
    assert result.status == "completed"
    assert result.result["peer_source"] == "hk-ipo-subscription-history@1.0.0"
    assert result.result["data_as_of_date"] == "2026-05-15"


def test_runtime_normalizes_handler_failures():
    class Broken:
        def invoke(self, invocation):
            raise RuntimeError("boom")

    result = SkillRuntime({"cashflow-analyzer": Broken()}).invoke(
        _invocation("cashflow-analyzer")
    )
    assert result.status == "failed"
    assert result.limitations == ["boom"]


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
