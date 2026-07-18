import asyncio

import pytest
from pydantic import SecretStr

from riskagent.research.adapters.bocha import BochaAdapter, parse_published_date
from riskagent.research.cache import FileQueryCache, InMemoryQueryCache
from riskagent.research.config import BochaConfig
from riskagent.research.gateway import (
    GatewayError,
    ResearchGateway,
    SearchQuery,
    SearchResponse,
    cache_key_for,
)
from riskagent.research.models import ExternalSearchPolicy, InjectedContext

# Keep this test's sentinel independent of pytest's per-directory conftest
# import resolution, which changes when reporting and research tests run
# together.
TEST_BOCHA_KEY = "test-secret-key-bocha-12345"


def run(coro):
    return asyncio.run(coro)


def assert_secret_absent(exc: BaseException) -> None:
    assert TEST_BOCHA_KEY not in repr(exc)
    assert TEST_BOCHA_KEY not in str(exc)
    for arg in exc.args:
        assert TEST_BOCHA_KEY not in str(arg)


class TestNormalSearch:
    def test_hits_normalized(self, make_bocha_adapter, bocha_ok_handler):
        adapter = make_bocha_adapter(bocha_ok_handler)
        response = run(adapter.search(SearchQuery(text="某公司 境外诉讼")))
        assert response.provider == "bocha"
        assert response.domain_filter_mode == "none"
        assert [h.url for h in response.hits] == [
            "https://www.hkexnews.hk/listedco/doc001.htm",
            "https://www.caixin.com/2025/article.html",
            "https://example-forum.com/thread/1",
        ]
        first = response.hits[0]
        assert first.title == "联交所披露易:发行人监管公告"
        assert first.site_name == "披露易"
        assert first.snippet and first.summary
        assert first.published_at == "2025-11-03"
        assert response.hits[1].published_at == "2025-06-01"
        assert response.hits[2].published_at is None

    def test_body_uses_only_documented_params(self, make_bocha_adapter, bocha_ok_handler):
        adapter = make_bocha_adapter(bocha_ok_handler)
        run(adapter.search(SearchQuery(text="q", freshness="oneMonth", count=5)))
        body = bocha_ok_handler.body_of(0)
        assert set(body) <= {"query", "freshness", "count", "summary"}
        assert body["query"] == "q"
        assert body["freshness"] == "oneMonth"
        assert body["count"] == 5
        assert body["summary"] is True
        auth = bocha_ok_handler.requests[0].headers.get("authorization")
        assert auth == f"Bearer {TEST_BOCHA_KEY}"

    def test_count_clamped(self, make_bocha_adapter, bocha_ok_handler):
        adapter = make_bocha_adapter(bocha_ok_handler)
        run(adapter.search(SearchQuery(text="q", count=500)))
        run(adapter.search(SearchQuery(text="q", count=0)))
        assert bocha_ok_handler.body_of(0)["count"] == 50
        assert bocha_ok_handler.body_of(1)["count"] == 1

    def test_date_parse_lenient(self):
        assert parse_published_date("2025-11-03T08:00:00+08:00") == "2025-11-03"
        assert parse_published_date("2025-11-03T00:00:00Z") == "2025-11-03"
        assert parse_published_date("2025/06/01") == "2025-06-01"
        assert parse_published_date("2025年6月1日") == "2025-06-01"
        assert parse_published_date("no idea") is None
        assert parse_published_date(None) is None


class TestErrors:
    def test_no_key_raises_before_http(self, make_bocha_adapter, bocha_ok_handler):
        adapter = make_bocha_adapter(bocha_ok_handler, api_key=None)
        assert adapter.available() is False
        with pytest.raises(GatewayError) as exc_info:
            run(adapter.search(SearchQuery(text="q")))
        assert exc_info.value.kind == "auth"
        assert bocha_ok_handler.requests == []

    def test_timeout_bounded_retries(self, make_bocha_adapter, bocha_timeout_handler):
        adapter = make_bocha_adapter(bocha_timeout_handler, max_retries=2)
        with pytest.raises(GatewayError) as exc_info:
            run(adapter.search(SearchQuery(text="q")))
        assert exc_info.value.kind == "timeout"
        assert exc_info.value.transient is True
        assert len(bocha_timeout_handler.requests) == 3
        assert_secret_absent(exc_info.value)

    def test_403_balance_no_retry(self, make_bocha_adapter, bocha_error_handler):
        handler = bocha_error_handler(403)
        adapter = make_bocha_adapter(handler)
        with pytest.raises(GatewayError) as exc_info:
            run(adapter.search(SearchQuery(text="q")))
        assert exc_info.value.kind == "balance"
        assert exc_info.value.transient is False
        assert len(handler.requests) == 1
        assert_secret_absent(exc_info.value)

    def test_401_auth_no_retry(self, make_bocha_adapter, bocha_error_handler):
        handler = bocha_error_handler(401)
        adapter = make_bocha_adapter(handler)
        with pytest.raises(GatewayError) as exc_info:
            run(adapter.search(SearchQuery(text="q")))
        assert exc_info.value.kind == "auth"
        assert len(handler.requests) == 1
        assert_secret_absent(exc_info.value)

    def test_400_bad_request_no_retry(self, make_bocha_adapter, bocha_error_handler):
        handler = bocha_error_handler(400)
        adapter = make_bocha_adapter(handler)
        with pytest.raises(GatewayError) as exc_info:
            run(adapter.search(SearchQuery(text="q")))
        assert exc_info.value.kind == "bad_request"
        assert len(handler.requests) == 1

    def test_429_retried_then_rate_limited(self, make_bocha_adapter, bocha_error_handler):
        handler = bocha_error_handler(429)
        adapter = make_bocha_adapter(handler, max_retries=2)
        with pytest.raises(GatewayError) as exc_info:
            run(adapter.search(SearchQuery(text="q")))
        assert exc_info.value.kind == "rate_limited"
        assert len(handler.requests) == 3
        assert_secret_absent(exc_info.value)

    def test_5xx_retried_then_unavailable(self, make_bocha_adapter, bocha_error_handler):
        handler = bocha_error_handler(502)
        adapter = make_bocha_adapter(handler, max_retries=1)
        with pytest.raises(GatewayError) as exc_info:
            run(adapter.search(SearchQuery(text="q")))
        assert exc_info.value.kind == "unavailable"
        assert len(handler.requests) == 2

    def test_secret_not_in_config_repr(self):
        config = BochaConfig(api_key=SecretStr(TEST_BOCHA_KEY))
        assert TEST_BOCHA_KEY not in repr(config)
        assert TEST_BOCHA_KEY not in str(config)
        assert TEST_BOCHA_KEY not in config.model_dump_json()


class TestDomainFilterEmulation:
    def test_include_uses_site_operator_and_post_filter(
        self, make_bocha_adapter, bocha_ok_handler
    ):
        adapter = make_bocha_adapter(bocha_ok_handler)
        response = run(
            adapter.search(SearchQuery(text="监管公告", include_domains=["hkexnews.hk"]))
        )
        body = bocha_ok_handler.body_of(0)
        assert "site:hkexnews.hk" in body["query"]
        assert response.domain_filter_mode == "emulated"
        assert [h.url for h in response.hits] == [
            "https://www.hkexnews.hk/listedco/doc001.htm"
        ]

    def test_include_capped_at_five_domains(self, make_bocha_adapter, bocha_ok_handler):
        adapter = make_bocha_adapter(bocha_ok_handler)
        domains = [f"d{i}.example.com" for i in range(7)]
        run(adapter.search(SearchQuery(text="q", include_domains=domains)))
        body = bocha_ok_handler.body_of(0)
        assert body["query"].count("site:") == 5

    def test_exclude_is_post_filter_only(self, make_bocha_adapter, bocha_ok_handler):
        adapter = make_bocha_adapter(bocha_ok_handler)
        response = run(
            adapter.search(SearchQuery(text="q", exclude_domains=["example-forum.com"]))
        )
        body = bocha_ok_handler.body_of(0)
        assert "site:" not in body["query"]
        assert response.domain_filter_mode == "emulated"
        assert all("example-forum.com" not in h.url for h in response.hits)
        assert len(response.hits) == 2


def make_scope(cutoff: str | None = "2025-12-31") -> InjectedContext:
    return InjectedContext(
        run_id="run_test", data_cutoff_date=cutoff, policy=ExternalSearchPolicy.public_only
    )


class TestGatewayCache:
    def test_cache_hit_on_repeat_query(self, make_bocha_adapter, bocha_ok_handler):
        gateway = ResearchGateway(
            make_bocha_adapter(bocha_ok_handler), cache=InMemoryQueryCache()
        )
        query = SearchQuery(text="重复查询")
        first, hit1 = run(gateway.search(query, cache_scope=make_scope()))
        second, hit2 = run(gateway.search(query, cache_scope=make_scope()))
        assert (hit1, hit2) == (False, True)
        assert len(bocha_ok_handler.requests) == 1
        assert second == first

    def test_cache_key_includes_cutoff(self, make_bocha_adapter, bocha_ok_handler):
        gateway = ResearchGateway(
            make_bocha_adapter(bocha_ok_handler), cache=InMemoryQueryCache()
        )
        query = SearchQuery(text="同一查询")
        run(gateway.search(query, cache_scope=make_scope("2025-12-31")))
        _, hit2 = run(gateway.search(query, cache_scope=make_scope("2026-06-30")))
        assert hit2 is False
        assert len(bocha_ok_handler.requests) == 2
        key_a = cache_key_for("bocha", query, make_scope("2025-12-31"))
        key_b = cache_key_for("bocha", query, make_scope("2026-06-30"))
        assert key_a != key_b

    def test_cache_key_includes_policy(self):
        query = SearchQuery(text="q")
        scope_a = InjectedContext(run_id="r", policy=ExternalSearchPolicy.public_only)
        scope_b = InjectedContext(run_id="r", policy=ExternalSearchPolicy.disabled)
        assert cache_key_for("bocha", query, scope_a) != cache_key_for("bocha", query, scope_b)


class TestFileQueryCache:
    def test_roundtrip(self, tmp_path):
        cache = FileQueryCache(tmp_path, ttl_seconds=3600)
        response = SearchResponse(hits=[], provider="bocha", domain_filter_mode="none")
        cache.set("k" * 64, response)
        assert cache.get("k" * 64) == response

    def test_ttl_expiry(self, tmp_path, monkeypatch):
        cache = FileQueryCache(tmp_path, ttl_seconds=100)
        cache.set("a" * 64, SearchResponse(hits=[], provider="bocha"))
        import riskagent.research.cache as cache_module

        real_time = cache_module.time.time()
        monkeypatch.setattr(cache_module.time, "time", lambda: real_time + 101)
        assert cache.get("a" * 64) is None

    def test_corrupt_entry_dropped(self, tmp_path):
        cache = FileQueryCache(tmp_path, ttl_seconds=3600)
        cache.set("b" * 64, SearchResponse(hits=[], provider="bocha"))
        path = cache._path_for("b" * 64)
        path.write_text("{not json", encoding="utf-8")
        assert cache.get("b" * 64) is None
        assert not path.exists()
