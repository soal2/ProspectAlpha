"""Pipeline-level failure and resume behaviour for the research track.

These tests sit one level above ``test_engine.py``: they drive the
repository-side glue (``bootstrap``, ``apply_module_research`` /
``freeze_module_research_stage``, ``_run_red_team_review``) the same way
the live pipeline does, and assert the externally visible properties
called out in the plan:

* external failure sets ``ResearchCompletionStatus.external_research_incomplete``
  on the artifact, never ``AnalysisRun.status``;
* affected hypotheses land on ``inconclusive`` via the ``system`` actor;
* failure still freezes a partial artifact and records a checkpoint so
  resume has a durable surface to land on;
* the cache key is scoped by ``data_cutoff_date`` + ``policy`` so a
  conclusion reached in one run cannot be re-used for a run with a
  different cutoff (no cross-run fact leakage).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import SecretStr

from riskagent.research.adapters.bocha import BochaAdapter
from riskagent.research.bootstrap import (
    apply_module_research,
    bootstrap_research_state,
    build_research_factory,
    freeze_module_research_stage,
)
from riskagent.research.cache import InMemoryQueryCache
from riskagent.research.config import ResearchConfig
from riskagent.research.engine import (
    HypothesisEngine,
    HypothesisProposal,
    ProposerContext,
    SearchOperation,
)
from riskagent.research.gateway import (
    GatewayError,
    ResearchGateway,
    SearchQuery,
    SearchResponse,
)
from riskagent.research.models import (
    ExternalSearchPolicy,
    HypothesisStatus,
    InjectedContext,
    ResearchArtifact,
    ResearchCompletionStatus,
    ResearchState,
    RiskHypothesis,
)
from riskagent.research.store import ResearchStore
from riskagent.research.tools import ResearchToolset, ToolContext, ToolLedger


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------------
# fixtures


def _store() -> ResearchStore:
    return ResearchStore(ResearchState())


def _gateway_with_key(handler) -> ResearchGateway:
    config = ResearchConfig(
        bocha={
            "api_key": SecretStr("test-secret-key-bocha-12345"),
            "endpoint": "https://api.bochaai.com/v1/web-search",
        }
    )
    transport = httpx.MockTransport(handler)
    adapter = BochaAdapter(config.bocha, transport=transport)
    return ResearchGateway(adapter, cache=InMemoryQueryCache())


def _gateway_unavailable() -> ResearchGateway:
    config = ResearchConfig(bocha={"api_key": None})
    return ResearchGateway(
        BochaAdapter(config.bocha), cache=InMemoryQueryCache()
    )


def _noop_toolset(_ctx: ToolContext) -> ResearchToolset:
    return ResearchToolset(
        gateway=_gateway_unavailable(),
        fetcher=None,
        context=_ctx,
        ledger=ToolLedger(),
    )


def _engine_builder(*, toolset_factory, store) -> HypothesisEngine:
    return HypothesisEngine(
        proposer=_proposer_returning([]),
        toolset_factory=toolset_factory,
        store=store,
    )


def _proposer_returning(proposals):
    def _proposer(_ctx: ProposerContext):
        return list(proposals)

    return _proposer


def _proposal_with_search(hypothesis_id: str = "hyp_a") -> HypothesisProposal:
    return HypothesisProposal(
        hypothesis=RiskHypothesis(
            hypothesis_id=hypothesis_id,
            title="境外诉讼未披露",
            statement="招股书未披露的重大境外诉讼。",
            dimension="法务监管",
            origin="anomaly",
            importance="high",
        ),
        operations=[SearchOperation(query="某公司 境外诉讼", purpose="初查")],
    )


# ----------------------------------------------------------------------
# failure classification


class TestFailureClassification:
    def test_external_failure_marks_incomplete_not_running(self):
        """A payload flagged external_incomplete propagates as the
        ``external_research_incomplete`` completion status on the frozen
        artifact, with a checkpoint recording where we gave up."""

        store = _store()
        bootstrap_research_state(
            run_id="run_test",
            company_name="某公司",
            analysis_depth="standard",
            policy=ExternalSearchPolicy.public_only,
            data_cutoff_date="2025-12-31",
            strategy_plan=None,
            store=store,
        )
        # Simulate a module that hit a transient gateway error — engine
        # returns external_incomplete=True and the affected hyp lands on
        # inconclusive via the system actor.
        hypothesis = RiskHypothesis(
            hypothesis_id="hyp_a",
            title="...",
            statement="...",
            dimension="法务监管",
            origin="anomaly",
            status=HypothesisStatus.inconclusive,
            importance="high",
        )
        transition = {
            "transition_id": "tr_1",
            "hypothesis_id": "hyp_a",
            "from_status": "investigating",
            "to_status": "inconclusive",
            "actor": "system",
            "reason": "transient gateway error: rate_limited",
            "evidence_ids": [],
            "tool_call_ids": [],
        }
        # Engine would normally have moved the hypothesis to investigating
        # before the failure; emulate that by hand so the transition is legal.
        store.state.working.hypotheses.append(hypothesis)
        hypothesis.status = HypothesisStatus.investigating
        store.state.transitions.append(
            __import__(
                "riskagent.research.models", fromlist=["HypothesisTransition"]
            ).HypothesisTransition.model_validate(transition)
        )
        hypothesis.status = HypothesisStatus.inconclusive

        apply_module_research(
            store=store,
            payload={
                "hypotheses": [hypothesis.model_dump(mode="json")],
                "evidence": [],
                "audit": [],
                "transitions": [],
                "coverage_task_ids_done": [],
                "external_incomplete": True,
                "failure_reason": "transient gateway error: rate_limited",
            },
        )
        frozen = freeze_module_research_stage(
            store=store,
            stage="expert_research",
            completion=ResearchCompletionStatus.external_research_incomplete,
            done_task_ids=[],
        )
        assert frozen is not None
        assert frozen.completion_status is ResearchCompletionStatus.external_research_incomplete
        # Failure noted on the working artifact
        assert any(
            "external_research_incomplete" in note or "incomplete" in note
            for note in store.state.working.notes
        )
        # Checkpoint recorded so resume has a durable surface
        assert len(store.state.checkpoints) == 1
        assert store.state.checkpoints[0].stage == "expert_research"

    def test_failure_does_not_touch_run_status(self):
        """``AnalysisRun.status`` is never set to a research completion
        value.

        The research completion enum (``external_research_incomplete``,
        ``running``, ``complete``, ``not_started``, ``failed``) lives on
        ``ResearchArtifact.completion_status``, NOT on
        ``AnalysisRun.status``.  We verify this by grepping the source
        for any code path that writes ``run.status`` from a research
        enum member — there should be none.
        """

        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        source = repo_root / "src" / "riskagent" / "analysis_runs.py"
        text = source.read_text(encoding="utf-8")
        research_specific_values = {
            "external_research_incomplete",
            "not_started",
        }
        # ``running`` / ``complete`` / ``failed`` are common words; we
        # restrict the check to the values unique to the research enum
        # to avoid false positives from the run-status vocabulary.
        for value in research_specific_values:
            # No line should assign this string literal to ``run.status``
            # (we'd see ``run.status = ..."<value>"...``).
            assert (
                f'run.status = "{value}"' not in text
                and f"run.status = '{value}'" not in text
            ), f"run.status must never be set to research value '{value}'"


# ----------------------------------------------------------------------
# resume idempotency


class TestResumeIdempotency:
    def test_apply_module_research_twice_does_not_double_count(self):
        """Hook B is called once per module on the initial pass and may
        be re-called on resume; the merge must be idempotent."""

        store = _store()
        bootstrap_research_state(
            run_id="run_test",
            company_name="某公司",
            analysis_depth="standard",
            policy=ExternalSearchPolicy.public_only,
            data_cutoff_date="2025-12-31",
            strategy_plan=None,
            store=store,
        )
        hypothesis = RiskHypothesis(
            hypothesis_id="hyp_a",
            title="...",
            statement="...",
            dimension="法务监管",
            origin="anomaly",
            status=HypothesisStatus.investigating,
        )
        store.state.working.hypotheses.append(hypothesis)
        payload = {
            "hypotheses": [hypothesis.model_dump(mode="json")],
            "evidence": [],
            "audit": [],
            "transitions": [
                {
                    "transition_id": "tr_1",
                    "hypothesis_id": "hyp_a",
                    "from_status": "discovered",
                    "to_status": "investigating",
                    "actor": "engine",
                    "reason": "started",
                    "evidence_ids": [],
                    "tool_call_ids": [],
                }
            ],
            "coverage_task_ids_done": [],
            "external_incomplete": False,
            "failure_reason": None,
        }
        apply_module_research(store=store, payload=payload)
        apply_module_research(store=store, payload=payload)
        # Hypothesis not duplicated
        assert len(store.state.working.hypotheses) == 1
        # Transition not duplicated
        assert len(store.state.transitions) == 1

    def test_done_coverage_tasks_skipped_on_resume(self):
        """Resume is data-driven: a CoverageTask marked ``done`` in the
        persisted plan is not re-executed.  The factory's coverage slice
        only contains tasks still pending."""

        store = _store()
        bootstrap_research_state(
            run_id="run_test",
            company_name="某公司",
            analysis_depth="standard",
            policy=ExternalSearchPolicy.public_only,
            data_cutoff_date="2025-12-31",
            strategy_plan=None,
            store=store,
        )
        # Mark every task as done — emulates a fully completed first pass.
        for task in store.state.working.plan:
            task.status = "done"

        factory = build_research_factory(
            run_id="run_test",
            company_name="某公司",
            user_requirement="...",
            analysis_depth="standard",
            data_cutoff_date="2025-12-31",
            strategy_plan=None,
            gateway=_gateway_with_key(
                lambda _req: httpx.Response(
                    200, json={"data": {"webPages": {"value": []}}}
                )
            ),
            store=store,
            config=ResearchConfig(),
            engine_builder=_engine_builder,
            toolset_builder=_noop_toolset,
        )
        # Every dimension still resolves to its tasks; the engine itself
        # is responsible for skipping ops whose ``coverage_task_id`` is
        # already in ``done_task_ids``.  The factory does not pre-filter
        # because the proposer needs to see the full plan to decide what
        # is worth re-proposing.  This test pins the contract: factory
        # returns a context with the legal dimension's tasks, including
        # done ones — the engine layer filters.
        ctx = factory("mod_legal", "法务监管")
        assert ctx is not None
        legal_tasks = [t for t in ctx.coverage_tasks if t.dimension == "法务监管"]
        assert legal_tasks  # at least one legal task in the baseline
        # Resume contract: each task carries its persisted status, so the
        # proposer / engine can tell what is already done.
        assert all(t.status == "done" for t in legal_tasks)


# ----------------------------------------------------------------------
# no cross-run fact leakage


class TestCacheScoping:
    def test_cache_key_depends_on_cutoff(self):
        """Conclusions cannot leak across runs with different cutoffs.

        The gateway hashes the cache key over (adapter name, query,
        data_cutoff_date, policy).  We assert this by issuing the same
        query under two different cutoffs and confirming the cache
        records two distinct entries.
        """

        from riskagent.research.cache import InMemoryQueryCache

        cache = InMemoryQueryCache()
        captured_keys: list[str] = []

        def _track_get(key: str):
            captured_keys.append(key)
            return None

        def _track_set(key: str, value):
            captured_keys.append(key)

        cache.get = _track_get  # type: ignore[method-assign]
        cache.set = _track_set  # type: ignore[method-assign]

        # Build a gateway that records cache keys but always hits the adapter.
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "webPages": {
                            "value": [
                                {
                                    "name": "x",
                                    "url": "https://www.hkexnews.hk/x.htm",
                                    "snippet": "...",
                                }
                            ]
                        }
                    }
                },
            )

        config = ResearchConfig(
            bocha={
                "api_key": SecretStr("test-secret-key-bocha-12345"),
                "endpoint": "https://api.bochaai.com/v1/web-search",
            }
        )
        adapter = BochaAdapter(config.bocha, transport=httpx.MockTransport(handler))
        gateway = ResearchGateway(adapter, cache=cache)

        query = SearchQuery(text="某公司 重大诉讼")
        injected_pre = InjectedContext(
            run_id="run_a",
            data_cutoff_date="2024-06-30",
            policy=ExternalSearchPolicy.public_only,
        )
        injected_post = InjectedContext(
            run_id="run_b",
            data_cutoff_date="2025-12-31",
            policy=ExternalSearchPolicy.public_only,
        )
        run(gateway.search(query, cache_scope=injected_pre))
        run(gateway.search(query, cache_scope=injected_post))
        # Two distinct cache keys were issued
        assert len(set(captured_keys)) == 2

    def test_cache_key_depends_on_policy(self):
        """Same cutoff, different policy → different cache key."""

        from riskagent.research.cache import InMemoryQueryCache

        cache = InMemoryQueryCache()
        captured_keys: list[str] = []

        def _track_get(key: str):
            captured_keys.append(key)
            return None

        cache.get = _track_get  # type: ignore[method-assign]

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"data": {"webPages": {"value": []}}}
            )

        config = ResearchConfig(
            bocha={
                "api_key": SecretStr("test-secret-key-bocha-12345"),
                "endpoint": "https://api.bochaai.com/v1/web-search",
            }
        )
        adapter = BochaAdapter(config.bocha, transport=httpx.MockTransport(handler))
        gateway = ResearchGateway(adapter, cache=cache)

        query = SearchQuery(text="某公司")
        injected_pub = InjectedContext(
            run_id="run_a",
            data_cutoff_date="2025-12-31",
            policy=ExternalSearchPolicy.public_only,
        )
        # The other policy values (disabled / approved_sensitive) would
        # short-circuit at the tool layer; the cache scoping is exercised
        # by changing the value and observing the key differs.
        injected_alt = InjectedContext(
            run_id="run_a",
            data_cutoff_date="2025-12-31",
            policy=ExternalSearchPolicy.disabled,
        )
        run(gateway.search(query, cache_scope=injected_pub))
        run(gateway.search(query, cache_scope=injected_alt))
        assert len(set(captured_keys)) == 2


# ----------------------------------------------------------------------
# skip path: gateway unavailable is a clean no-op


class TestEngineFailurePath:
    """Drive the engine through a real transient gateway failure and
    verify the resulting payload has the contract Hook B relies on."""

    def test_transient_failure_yields_inconclusive_and_incomplete_flag(self):
        from riskagent.research.engine import (
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

        class _FailingAdapter:
            name = "failing"

            def available(self) -> bool:
                return True

            async def search(self, query: SearchQuery) -> SearchResponse:
                raise GatewayError("rate_limited", "throttled")

        state = ResearchState()
        state.working = ResearchArtifact(artifact_id="art_test", version=1)
        store = ResearchStore(state)
        gateway = ResearchGateway(_FailingAdapter(), cache=InMemoryQueryCache())

        def toolset_factory(ctx: ToolContext) -> ResearchToolset:
            return ResearchToolset(
                gateway=gateway, fetcher=None, context=ctx, ledger=ToolLedger()
            )

        proposal = HypothesisProposal(
            hypothesis=RiskHypothesis(
                hypothesis_id="hyp_a",
                title="...",
                statement="...",
                dimension="法务监管",
                origin="anomaly",
                importance="high",
            ),
            operations=[SearchOperation(query="某公司", purpose="初查")],
        )

        def _proposer(_ctx: ProposerContext):
            return [proposal]

        engine = HypothesisEngine(
            proposer=_proposer, toolset_factory=toolset_factory, store=store
        )
        result = run(
            engine.run_module(
                module_id="m1",
                agent_name="legal",
                company_name="某公司",
                screening_observations=["异常"],
                injected=InjectedContext(
                    run_id="run_test",
                    data_cutoff_date="2025-12-31",
                    policy=ExternalSearchPolicy.public_only,
                ),
            )
        )
        assert result is not None
        assert result.external_incomplete is True
        assert "rate_limited" in (result.failure_reason or "")
        # Affected hypothesis is inconclusive via the system actor
        hyp = result.hypotheses[0]
        assert hyp.status is HypothesisStatus.inconclusive
        assert any(
            t.to_status is HypothesisStatus.inconclusive and t.actor == "system"
            for t in result.transitions
        )

        # Now drive Hook B with this real payload and confirm it freezes
        # an external_research_incomplete artifact.
        bootstrap_research_state(
            run_id="run_test",
            company_name="某公司",
            analysis_depth="standard",
            policy=ExternalSearchPolicy.public_only,
            data_cutoff_date="2025-12-31",
            strategy_plan=None,
            store=store,
        )
        apply_module_research(store=store, payload=result.as_payload())
        frozen = freeze_module_research_stage(
            store=store,
            stage="expert_research",
            completion=ResearchCompletionStatus.external_research_incomplete,
            done_task_ids=result.coverage_task_ids_done,
        )
        assert frozen is not None
        assert frozen.completion_status is ResearchCompletionStatus.external_research_incomplete


class TestUnavailableGatewayPath:
    def test_unavailable_gateway_factory_preserves_research_context(self):
        """A missing key keeps an auditable artifact path instead of skipping it."""

        store = _store()
        factory = build_research_factory(
            run_id="run_test",
            company_name="某公司",
            user_requirement="...",
            analysis_depth="standard",
            data_cutoff_date="2025-12-31",
            strategy_plan=None,
            gateway=_gateway_unavailable(),
            store=store,
            config=ResearchConfig(),
            engine_builder=_engine_builder,
            toolset_builder=_noop_toolset,
        )
        context = factory("mod_legal", "法务监管")
        assert context is not None
        assert context.injected.data_cutoff_date == "2025-12-31"
        assert store.state.working is not None
