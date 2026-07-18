"""Bootstrap glue tests: factory, Hook A idempotency, Hook B merge."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from riskagent.research.bootstrap import (
    apply_module_research,
    bootstrap_research_state,
    build_research_factory,
    freeze_module_research_stage,
)
from riskagent.research.cache import InMemoryQueryCache
from riskagent.research.config import ResearchConfig
from riskagent.research.engine import HypothesisEngine
from riskagent.research.gateway import ResearchGateway
from riskagent.research.models import (
    ExternalSearchPolicy,
    HypothesisStatus,
    InjectedContext,
    ResearchCompletionStatus,
    ResearchState,
    RiskHypothesis,
    SourceCard,
    SourceTier,
)
from riskagent.research.planner import build_baseline_plan
from riskagent.research.store import ResearchStore
from riskagent.research.tools import ResearchToolset, ToolContext, ToolLedger


def _gateway_unavailable() -> ResearchGateway:
    """Build a gateway whose adapter reports ``available() is False``.

    With no Bocha key in the config the adapter short-circuits before any
    HTTP attempt — the D7 skip path.  We construct it explicitly so the
    test does not depend on the env.
    """

    from riskagent.research.adapters.bocha import BochaAdapter

    config = ResearchConfig(bocha={"api_key": None})
    adapter = BochaAdapter(config.bocha)
    return ResearchGateway(adapter, cache=InMemoryQueryCache())


def _gateway_available(handler) -> ResearchGateway:
    """Build a gateway backed by a MockTransport handler."""

    from riskagent.research.adapters.bocha import BochaAdapter
    from pydantic import SecretStr

    config = ResearchConfig(
        bocha={"api_key": SecretStr("test-secret-key-bocha-12345"), "endpoint": "https://api.bochaai.com/v1/web-search"},
    )
    transport = httpx.MockTransport(handler)
    adapter = BochaAdapter(config.bocha, transport=transport)
    return ResearchGateway(adapter, cache=InMemoryQueryCache())


def _store() -> ResearchStore:
    return ResearchStore(ResearchState())


def _toolset_factory(_ctx: ToolContext) -> ResearchToolset:
    """Toolset factory that returns a fresh empty toolset.

    The research hooks under test do not actually dispatch tools — we only
    need a builder that satisfies the callable contract.
    """

    return ResearchToolset(
        gateway=_gateway_unavailable(),
        fetcher=None,
        context=_ctx,
        ledger=ToolLedger(),
    )


def _engine_builder(*, toolset_factory, store) -> HypothesisEngine:
    def _proposer(_ctx):
        return []

    return HypothesisEngine(
        proposer=_proposer,
        toolset_factory=toolset_factory,
        store=store,
    )


class TestHookA:
    def test_bootstrap_creates_v1_working_artifact(self):
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
        assert store.state.working is not None
        assert store.state.working.version == 1
        assert store.state.working.frozen is False
        # Nine baseline dimensions covered for standard depth
        assert len(store.state.working.plan) == 9
        # Policy + analysis_depth land on the state
        assert store.state.policy == ExternalSearchPolicy.public_only
        assert store.state.analysis_depth == "standard"

    def test_bootstrap_quick_mode_has_empty_plan(self):
        store = _store()
        bootstrap_research_state(
            run_id="run_test",
            company_name="某公司",
            analysis_depth="quick",
            policy=ExternalSearchPolicy.public_only,
            data_cutoff_date="2025-12-31",
            strategy_plan=None,
            store=store,
        )
        assert store.state.working.plan == []

    def test_bootstrap_idempotent(self):
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
        first_working = store.state.working
        # Second call must not replace the working artifact
        bootstrap_research_state(
            run_id="run_test",
            company_name="某公司",
            analysis_depth="standard",
            policy=ExternalSearchPolicy.public_only,
            data_cutoff_date="2025-12-31",
            strategy_plan=None,
            store=store,
        )
        assert store.state.working is first_working

    def test_bootstrap_with_assignments_maps_module_ids(self):
        store = _store()
        plan = {
            "assignments": [
                {"module_id": "mod_a", "module": "法务监管", "goal": "..."},
                {"module_id": "mod_b", "module": "财务与会计", "goal": "..."},
            ],
        }
        bootstrap_research_state(
            run_id="run_test",
            company_name="某公司",
            analysis_depth="standard",
            policy=ExternalSearchPolicy.public_only,
            data_cutoff_date="2025-12-31",
            strategy_plan=plan,
            store=store,
        )
        by_dim = {t.dimension: t for t in store.state.working.plan}
        assert by_dim["法务监管"].module_id == "mod_a"
        assert by_dim["财务"].module_id == "mod_b"


class TestResearchFactory:
    def test_factory_preserves_context_when_gateway_unavailable(self):
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
            toolset_builder=_toolset_factory,
        )
        # Missing credentials must remain observable in the resulting research
        # artifact rather than silently skipping the investigation.
        context = factory("mod_a", "法务监管")
        assert context is not None
        assert context.injected.data_cutoff_date == "2025-12-31"
        assert any(task.dimension == "法务监管" for task in context.coverage_tasks)

    def test_factory_quick_mode_returns_none_for_unmapped_modules(self):
        store = _store()

        def _ok_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "webPages": {
                            "value": [
                                {
                                    "name": "测试",
                                    "url": "https://www.hkexnews.hk/x.htm",
                                    "siteName": "披露易",
                                    "snippet": "...",
                                    "summary": "...",
                                }
                            ]
                        }
                    }
                },
            )

        factory = build_research_factory(
            run_id="run_test",
            company_name="某公司",
            user_requirement="...",
            analysis_depth="quick",
            data_cutoff_date="2025-12-31",
            strategy_plan=None,
            gateway=_gateway_available(_ok_handler),
            store=store,
            config=ResearchConfig(),
            engine_builder=_engine_builder,
            toolset_builder=_toolset_factory,
        )
        # Quick depth + baseline-empty + unmapped module → skip
        assert factory("mod_x", "技术栈") is None

    def test_factory_standard_mode_returns_context_for_legal_module(self):
        store = _store()

        def _ok_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"webPages": {"value": []}}})

        factory = build_research_factory(
            run_id="run_test",
            company_name="某公司",
            user_requirement="...",
            analysis_depth="standard",
            data_cutoff_date="2025-12-31",
            strategy_plan=None,
            gateway=_gateway_available(_ok_handler),
            store=store,
            config=ResearchConfig(),
            engine_builder=_engine_builder,
            toolset_builder=_toolset_factory,
        )
        ctx = factory("mod_legal", "法务监管")
        assert ctx is not None
        assert ctx.engine is not None
        # Legal module → 法务监管 dimension tasks
        assert any(t.dimension == "法务监管" for t in ctx.coverage_tasks)
        # Injected context carries the run_id and cutoff
        assert ctx.injected.run_id == "run_test"
        assert ctx.injected.data_cutoff_date == "2025-12-31"
        assert ctx.injected.policy == ExternalSearchPolicy.public_only


class TestHookB:
    def test_apply_module_research_merges_payload(self):
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
            title="境外诉讼",
            statement="未披露的境外诉讼",
            dimension="法务监管",
            origin="anomaly",
            rule_ids=[],
            importance="high",
        )
        card = SourceCard(
            evidence_id="ev_1",
            url="https://www.hkexnews.hk/x.htm",
            title="公告",
            site_name="披露易",
            origin="fetched_page",
            snippet="...",
            accessible=True,
            tier=SourceTier.tier1,
            tier_reason="regulator domain",
            language="zh-Hans",
        )
        payload = {
            "hypotheses": [hypothesis.model_dump(mode="json")],
            "evidence": [card.model_dump(mode="json")],
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
        assert store.state.working.get_hypothesis("hyp_a") is not None
        assert any(c.evidence_id == "ev_1" for c in store.state.working.evidence)
        assert len(store.state.transitions) == 1

    def test_apply_module_research_idempotent(self):
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
            rule_ids=[],
            importance="medium",
        )
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
        # Same hypothesis not double-added
        assert len(store.state.working.hypotheses) == 1
        # Same transition not double-added
        assert len(store.state.transitions) == 1

    def test_apply_module_research_handles_external_incomplete(self):
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
        apply_module_research(
            store=store,
            payload={
                "hypotheses": [],
                "evidence": [],
                "audit": [],
                "transitions": [],
                "coverage_task_ids_done": [],
                "external_incomplete": True,
                "failure_reason": "rate_limited",
            },
        )
        # A note was appended to the working artifact
        assert any(
            "external_research_incomplete" in note
            for note in store.state.working.notes
        )

    def test_freeze_closes_version_and_checkpoints(self):
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
        frozen = freeze_module_research_stage(
            store=store,
            stage="expert_research",
            completion=ResearchCompletionStatus.complete,
            done_task_ids=["task_1"],
        )
        assert frozen is not None
        assert frozen.frozen is True
        # Next working version bumped
        assert store.state.working is not None
        assert store.state.working.version == 2
        assert store.state.working.based_on_version == 1
        # Checkpoint recorded
        assert len(store.state.checkpoints) == 1
        assert store.state.checkpoints[0].stage == "expert_research"
