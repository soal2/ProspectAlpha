"""Custom exception types for the multi-agent debate phase."""

from __future__ import annotations


class DebateLLMError(RuntimeError):
    """Raised when a debate phase cannot produce usable output.

    A debate is considered failed only when *every* critique, revision, or
    arbitration attempt fails. Partial failures are tolerated and surfaced via
    ``DebatePhaseResult.partial_failure_count`` instead of raising.

    Attributes:
        phase: ``"critique" | "revision" | "arbitration"``.
        failed: Number of individual LLM calls that failed.
        total: Total number of individual LLM calls attempted in this phase.
    """

    def __init__(self, *, phase: str, failed: int, total: int, detail: str) -> None:
        self.phase = phase
        self.failed = failed
        self.total = total
        self.detail = detail
        super().__init__(f"{phase} failed {failed}/{total}: {detail}")
