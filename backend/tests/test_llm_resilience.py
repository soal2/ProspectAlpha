"""Tests for the LLM resilience layer: exception classification, retry, circuit breaker."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from riskagent.agents import llm_resilience as resilience
from riskagent.agents.llm_resilience import (
    CircuitBreaker,
    CircuitBreakerOpen,
    call_llm_with_resilience,
)
from riskagent.providers.llm import (
    LLMError,
    LLMParseError,
    LLMTransientError,
    _classify_openai_error,
)


class _FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class _FlakyLLM:
    """Returns canned responses in order, raising the listed exception first."""

    def __init__(self, *, responses: list[Any]):
        self.responses = list(responses)
        self.call_count = 0

    async def chat_json(self, messages, *, temperature: float = 0.2):
        self.call_count += 1
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _make_transient_exc() -> Exception:
    """A raw openai-shaped exception used to test _classify_openai_error / chat_json."""

    class FakeTimeout(Exception):
        pass

    FakeTimeout.__name__ = "APITimeoutError"
    return FakeTimeout("simulated timeout")


def _make_transient_llm_error() -> LLMTransientError:
    """An already-classified transient error used to test the resilience layer directly.

    After chat_json classifies SDK errors, the resilience layer only ever sees
    LLMTransientError / LLMParseError / LLMError. Test doubles should mirror that.
    """

    err = LLMTransientError("simulated timeout")
    err.__cause__ = _make_transient_exc()
    return err


# --- Test 1: classifier maps openai SDK exception names onto the right hierarchy ---


def test_classify_openai_error_distinguishes_transient_from_persistent():
    transient = _make_transient_exc()
    classified_t = _classify_openai_error(transient)
    assert isinstance(classified_t, LLMTransientError)
    assert isinstance(classified_t, LLMError)
    assert classified_t.__cause__ is transient

    class Fake4xx(Exception):
        pass

    Fake4xx.__name__ = "BadRequestError"
    classified_p = _classify_openai_error(Fake4xx("bad"))
    assert isinstance(classified_p, LLMError)
    assert not isinstance(classified_p, LLMTransientError)


def test_chat_json_wraps_openai_sdk_errors_as_llm_errors(monkeypatch):
    """chat_json should classify SDK errors instead of leaking the raw exception type."""

    import sys

    from riskagent.providers import llm as llm_module

    class _FakeOpenAI:
        class completions:  # noqa: N801 - mimic SDK shape
            @staticmethod
            async def create(**_kwargs):
                raise _make_transient_exc()

    class _FakeAsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = _FakeOpenAI()

    # AsyncOpenAI is imported lazily inside LLMClient.__init__; inject a fake
    # `openai` module so the import resolves to our double.
    fake_module = type(sys)("openai")
    fake_module.AsyncOpenAI = _FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    client = llm_module.LLMClient(base_url="http://x", api_key="k", model="m")
    with pytest.raises(LLMTransientError):
        asyncio.run(client.chat_json([{"role": "user", "content": "hi"}]))


# --- Test 2: resilience retries transient then succeeds ---


def test_resilience_retries_transient_then_succeeds(monkeypatch):
    # Speed up the test: disable real backoff
    monkeypatch.setattr(resilience, "LLM_RETRY_INITIAL_SECONDS", 0.0)
    monkeypatch.setattr(resilience, "LLM_RETRY_MAX_SECONDS", 0.0)

    llm = _FlakyLLM(responses=[_make_transient_llm_error(), {"ok": True}])
    breaker = CircuitBreaker(failure_threshold=99, recovery_seconds=99)
    monkeypatch.setattr(resilience, "get_default_llm_client", lambda: llm)

    result = asyncio.run(
        call_llm_with_resilience(
            messages=[{"role": "user", "content": "hi"}],
            client=llm,
            breaker=breaker,
            context={"phase": "test"},
        )
    )
    assert result == {"ok": True}
    assert llm.call_count == 2
    assert breaker.state == CircuitBreaker.CLOSED


# --- Test 3: parse errors are not retried ---


def test_resilience_does_not_retry_parse_error(monkeypatch):
    monkeypatch.setattr(resilience, "LLM_RETRY_INITIAL_SECONDS", 0.0)
    monkeypatch.setattr(resilience, "LLM_RETRY_MAX_SECONDS", 0.0)

    llm = _FlakyLLM(responses=[LLMParseError("bad json")])
    breaker = CircuitBreaker(failure_threshold=99)
    monkeypatch.setattr(resilience, "get_default_llm_client", lambda: llm)

    with pytest.raises(LLMParseError):
        asyncio.run(
            call_llm_with_resilience(
                messages=[{"role": "user", "content": "hi"}],
                client=llm,
                breaker=breaker,
            )
        )
    # Parse error means the service was reachable; breaker should remain CLOSED.
    assert llm.call_count == 1
    assert breaker.state == CircuitBreaker.CLOSED


# --- Test 4: circuit breaker trips after threshold failures ---


def test_circuit_breaker_trips_after_threshold_failures():
    clock = _FakeClock()
    cb = CircuitBreaker(failure_threshold=3, recovery_seconds=30.0, window_seconds=60.0, clock=clock)
    assert cb.state == CircuitBreaker.CLOSED
    assert cb.allow() is True

    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitBreaker.CLOSED  # not yet

    cb.record_failure()
    assert cb.state == CircuitBreaker.OPEN
    assert cb.allow() is False  # rejects while OPEN


# --- Test 5: circuit breaker recovers after cooldown ---


def test_circuit_breaker_recovers_after_cooldown():
    clock = _FakeClock()
    cb = CircuitBreaker(failure_threshold=1, recovery_seconds=10.0, window_seconds=60.0, clock=clock)

    cb.record_failure()
    assert cb.state == CircuitBreaker.OPEN
    assert cb.allow() is False  # still in cooldown

    clock.advance(9.0)
    assert cb.allow() is False  # 9s < 10s recovery

    clock.advance(2.0)  # total 11s, crosses recovery boundary
    assert cb.allow() is True  # HALF_OPEN lets a probe through
    assert cb.state == CircuitBreaker.HALF_OPEN

    cb.record_success()
    assert cb.state == CircuitBreaker.CLOSED


def test_call_rejected_when_breaker_open(monkeypatch):
    monkeypatch.setattr(resilience, "LLM_RETRY_INITIAL_SECONDS", 0.0)
    monkeypatch.setattr(resilience, "LLM_RETRY_MAX_SECONDS", 0.0)

    llm = _FlakyLLM(responses=[{"ok": True}])
    clock = _FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=99.0, clock=clock)
    breaker.record_failure()
    assert breaker.state == CircuitBreaker.OPEN

    monkeypatch.setattr(resilience, "get_default_llm_client", lambda: llm)
    with pytest.raises(CircuitBreakerOpen):
        asyncio.run(
            call_llm_with_resilience(
                messages=[{"role": "user", "content": "hi"}],
                client=llm,
                breaker=breaker,
            )
        )
    # Crucially, the underlying LLM was never contacted.
    assert llm.call_count == 0
