"""Resilience layer for LLM calls: retry with exponential backoff + circuit breaker.

Wraps :func:`riskagent.providers.llm.LLMClient.chat_json` with:

1. **Retry**: ``tenacity`` exponential backoff with jitter, only retrying
   :class:`~riskagent.providers.llm.LLMTransientError`.
2. **Circuit breaker**: process-wide singleton that trips after N failures in a
   rolling window and rejects calls without hitting the network while OPEN,
   transitioning through HALF_OPEN after a cooldown.

Both layers emit structured logs so the full chain (boundary → debate → pipeline)
can be reconstructed from logs alone.
"""

from __future__ import annotations

import logging
import os
from collections import deque
from threading import Lock
from time import monotonic
from typing import Any

from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from riskagent.providers.llm import (
    LLMError,
    LLMParseError,
    LLMTransientError,
    get_default_llm_client,
)


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


LLM_MAX_ATTEMPTS = _env_int("LLM_MAX_ATTEMPTS", 3)
LLM_RETRY_INITIAL_SECONDS = _env_float("LLM_RETRY_INITIAL_SECONDS", 1.0)
LLM_RETRY_MAX_SECONDS = _env_float("LLM_RETRY_MAX_SECONDS", 8.0)
LLM_CIRCUIT_FAILURE_THRESHOLD = _env_int("LLM_CIRCUIT_FAILURE_THRESHOLD", 5)
LLM_CIRCUIT_RECOVERY_SECONDS = _env_float("LLM_CIRCUIT_RECOVERY_SECONDS", 30.0)
LLM_CIRCUIT_WINDOW_SECONDS = _env_float("LLM_CIRCUIT_WINDOW_SECONDS", 60.0)


class CircuitBreakerOpen(LLMError):
    """Raised when the breaker is OPEN and a call is rejected pre-flight."""


class CircuitBreaker:
    """Fixed-window failure-rate breaker.

    States:
        CLOSED    — calls flow through; failures are counted in a rolling window.
        OPEN      — calls rejected without network; waits ``recovery_seconds``.
        HALF_OPEN — first probe call after cooldown is allowed; on success the
                    breaker closes, on failure it re-opens.
    """

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(
        self,
        *,
        failure_threshold: int = LLM_CIRCUIT_FAILURE_THRESHOLD,
        recovery_seconds: float = LLM_CIRCUIT_RECOVERY_SECONDS,
        window_seconds: float = LLM_CIRCUIT_WINDOW_SECONDS,
        clock: Any = monotonic,
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_seconds = recovery_seconds
        self.window_seconds = window_seconds
        self._clock = clock
        self._failures: deque[float] = deque()
        self._state = self.CLOSED
        self._opened_at = 0.0
        self._lock = Lock()

    @property
    def state(self) -> str:
        return self._state

    def allow(self) -> bool:
        with self._lock:
            now = self._clock()
            if self._state == self.OPEN:
                if now - self._opened_at >= self.recovery_seconds:
                    self._state = self.HALF_OPEN
                    logger.warning(
                        "circuit_breaker_state_change",
                        extra={"from": self.OPEN, "to": self.HALF_OPEN},
                    )
                    return True
                return False
            return True  # CLOSED or HALF_OPEN

    def record_success(self) -> None:
        with self._lock:
            if self._state == self.HALF_OPEN:
                self._state = self.CLOSED
                self._failures.clear()
                logger.warning(
                    "circuit_breaker_state_change",
                    extra={"from": self.HALF_OPEN, "to": self.CLOSED},
                )
            elif self._state == self.CLOSED:
                # Rolling window: drop stale failures on success to keep counts fresh.
                now = self._clock()
                while self._failures and now - self._failures[0] > self.window_seconds:
                    self._failures.popleft()

    def record_failure(self) -> None:
        now = self._clock()
        with self._lock:
            self._failures.append(now)
            while self._failures and now - self._failures[0] > self.window_seconds:
                self._failures.popleft()
            if self._state == self.HALF_OPEN:
                self._state = self.OPEN
                self._opened_at = now
                logger.warning(
                    "circuit_breaker_state_change",
                    extra={"from": self.HALF_OPEN, "to": self.OPEN},
                )
            elif self._state == self.CLOSED and len(self._failures) >= self.failure_threshold:
                self._state = self.OPEN
                self._opened_at = now
                logger.warning(
                    "circuit_breaker_state_change",
                    extra={
                        "from": self.CLOSED,
                        "to": self.OPEN,
                        "failures_in_window": len(self._failures),
                    },
                )

    def reset(self) -> None:
        """Test helper: force back to CLOSED."""
        with self._lock:
            self._failures.clear()
            self._state = self.CLOSED
            self._opened_at = 0.0


_DEFAULT_BREAKER: CircuitBreaker | None = None


def get_default_breaker() -> CircuitBreaker:
    """Process-wide lazy singleton. Tests can call ``reset()`` to clear state."""

    global _DEFAULT_BREAKER
    if _DEFAULT_BREAKER is None:
        _DEFAULT_BREAKER = CircuitBreaker()
    return _DEFAULT_BREAKER


def set_default_breaker(breaker: CircuitBreaker | None) -> None:
    """Test helper to inject a custom breaker or clear the singleton."""

    global _DEFAULT_BREAKER
    _DEFAULT_BREAKER = breaker


async def call_llm_with_resilience(
    *,
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    context: dict[str, Any] | None = None,
    breaker: CircuitBreaker | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """Retry transient LLM failures and apply circuit-breaker protection.

    Args:
        messages: Chat messages forwarded to ``chat_json``.
        temperature: Sampling temperature.
        context: Optional structured-log fields (e.g. ``{"phase": "critique",
            "agent_name": "..."}``); merged into every log line this call emits.
        breaker: Optional breaker override; defaults to ``get_default_breaker()``.
        client: Optional ``LLMClient`` (or test double) to call ``chat_json`` on.
            Defaults to ``get_default_llm_client()`` resolved lazily on each
            attempt so tests can swap env-loaded clients between calls.

    Raises:
        CircuitBreakerOpen: breaker is OPEN, no network call attempted.
        LLMTransientError: every retry attempt failed with a transient error.
        LLMParseError: the model returned non-JSON / non-object output (not retried).
        LLMError: any other non-transient API error.
    """

    breaker = breaker or get_default_breaker()
    log_ctx = dict(context or {})

    if not breaker.allow():
        logger.warning("circuit_breaker_open", extra=log_ctx)
        raise CircuitBreakerOpen("circuit breaker is OPEN; call rejected pre-flight")

    retrying = AsyncRetrying(
        retry=retry_if_exception_type(LLMTransientError),
        stop=stop_after_attempt(LLM_MAX_ATTEMPTS),
        wait=wait_exponential_jitter(initial=LLM_RETRY_INITIAL_SECONDS, max=LLM_RETRY_MAX_SECONDS),
        reraise=True,
    )

    async def _do_call() -> dict[str, Any]:
        resolved = client if client is not None else get_default_llm_client()
        return await resolved.chat_json(messages, temperature=temperature)

    attempt_count = 0
    last_exc: Exception | None = None
    try:
        async for attempt in retrying:
            with attempt:
                attempt_count += 1
                return await _do_call()
    except LLMTransientError as exc:
        last_exc = exc
        breaker.record_failure()
        logger.warning(
            "llm_transient_exhausted",
            extra={**log_ctx, "attempts": attempt_count, "error": str(exc)},
        )
        raise
    except LLMParseError as exc:
        last_exc = exc
        # Service responded OK; bad content is not a service-health signal.
        breaker.record_success()
        logger.warning(
            "llm_parse_failed",
            extra={**log_ctx, "attempts": attempt_count, "error": str(exc)},
        )
        raise
    except LLMError as exc:
        last_exc = exc
        # Non-transient API error (4xx). Treat as service-reachable.
        breaker.record_success()
        logger.warning(
            "llm_call_failed",
            extra={**log_ctx, "attempts": attempt_count, "error": str(exc)},
        )
        raise

    # tenacity's AsyncRetrying may raise RetryError if reraise=False; we set reraise=True
    # so the original exception surfaces. Defensive fallback:
    raise last_exc if last_exc is not None else LLMError("call_llm_with_resilience exhausted with no exception")
