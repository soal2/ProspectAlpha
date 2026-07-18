from __future__ import annotations

import json
import socket
from itertools import count
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from pydantic import SecretStr

from riskagent.agent_experts import AgentSpec
from riskagent.agents.orchestrator import StrategyDecision
from riskagent.agents.supervisor import AgentTeamSupervisor
from riskagent.analysis_runs import AnalysisRunRepository
from riskagent.research.adapters import BochaAdapter
from riskagent.research.config import BochaConfig, ResearchConfig
from riskagent.research.ssrf import SafeFetcher
from riskagent.retrieval import ChunkSearchResult
from riskagent.risk_rules import RiskRuleRepository


def _test_agent_specs() -> list[AgentSpec]:
    return [
        AgentSpec(
            name="financial_agent",
            title="Financial specialist",
            rule_types=("quantitative_financial",),
        ),
        AgentSpec(
            name="legal_agent",
            title="Legal and compliance specialist",
            categories=("关联交易", "对赌/赎回条款", "政策监管", "诉讼与纠纷", "担保与质押", "税务风险"),
        ),
        AgentSpec(
            name="market_agent",
            title="Market and industry specialist",
            categories=("行业竞争",),
        ),
        AgentSpec(
            name="business_agent",
            title="Business and disclosure specialist",
            default=True,
        ),
    ]


def _test_chunk_retriever(*, document_id: str, **_kwargs: Any) -> list[ChunkSearchResult]:
    return [
        ChunkSearchResult(
            chunk_id=f"chunk_{document_id}_001",
            document_id=document_id,
            doc_key="test",
            chunk_type="text",
            text=(
                "风险因素：公司存在关联交易、对赌和赎回安排，经营活动现金流持续为负，"
                "并存在供应商集中度风险。"
            ),
            page_idx=1,
            source_file="test.pdf",
            source_item_indices=[0],
            bbox_refs=[{"page_idx": 1, "bbox": [1, 2, 3, 4], "source_item_index": 0}],
            merged_bboxes=[{"page_idx": 1, "bbox": [1, 2, 3, 4]}],
            image_path=None,
            mineru_img_path=None,
            table_html=None,
            metadata={"section_path": "风险因素"},
            score=0.9,
        )
    ]


def _strategy_provider(selected_agents: tuple[str, ...] | None):
    def plan(*, available_agents: list[dict[str, Any]], **_kwargs: Any) -> StrategyDecision:
        by_name = {str(item["agent_name"]): item for item in available_agents}
        selected = list(selected_agents or tuple(by_name))
        return StrategyDecision.model_validate(
            {
                "summary": "Deterministic dynamic strategy for pipeline contract tests.",
                "focus_areas": [str(by_name[name].get("role") or name) for name in selected],
                "selected_agents": selected,
                "assignments": [
                    {
                        "module_id": f"module_{name.removesuffix('_agent')}",
                        "agent_name": name,
                        "module": str(by_name[name].get("role") or by_name[name].get("title") or name),
                        "goal": f"Analyze the prospectus using {name}'s live Agent Card.",
                        "expected_outputs": [
                            str(by_name[name].get("output_schema") or "RiskFinding + RunEvidence")
                        ],
                    }
                    for name in selected
                ],
            }
        )

    return plan


def deterministic_report_provider(
    *,
    company_name: str,
    module_results: list[dict[str, Any]],
    verified_findings: list[dict[str, Any]],
    evidence_index: list[dict[str, Any]],
    **_kwargs: Any,
) -> dict[str, Any]:
    allowed_findings = {str(item["finding_id"]) for item in verified_findings}
    evidence_by_finding: dict[str, list[str]] = {}
    for item in evidence_index:
        evidence_by_finding.setdefault(str(item["finding_id"]), []).append(
            str(item["evidence_id"])
        )
    sections = []
    for module in module_results:
        finding_ids = [
            str(item)
            for item in module.get("finding_ids", [])
            if str(item) in allowed_findings
        ]
        evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for finding_id in finding_ids
                for evidence_id in evidence_by_finding.get(finding_id, [])
            )
        )
        sections.append(
            {
                "module_id": str(module["module_id"]),
                "title": str(module.get("module") or module["module_id"]),
                "analysis": str(module.get("summary") or "No material risk was identified."),
                "finding_ids": finding_ids,
                "evidence_ids": evidence_ids,
            }
        )
    return {
        "title": f"{company_name} IPO 风险分析草案",
        "executive_summary": "已整合全部动态专家模块并保留证据引用。",
        "overall_risk_level": "中",
        "sections": sections,
        "cross_module_risks": [],
        "recommendations": ["对弱证据结论执行人工复核。"],
        "limitations": [],
    }


@pytest.fixture
def analysis_repo_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sequence = count(1)
    runtime_root = tmp_path / "agent-runtime"
    monkeypatch.setenv("AGENT_RUNTIME_DATA_DIR", str(runtime_root))

    async def confirm_test_candidates(**kwargs: Any) -> list[dict[str, Any]]:
        items = []
        for candidate in kwargs["candidates"]:
            candidate.evaluation_result = "triggered"
            candidate.confidence = 0.8
            items.append(
                {
                    "rule_id": candidate.rule_id,
                    "evaluation_result": "triggered",
                    "severity": candidate.severity,
                    "confidence": 0.8,
                    "reasoning_summary": candidate.reasoning_summary,
                    "needs_more_evidence": False,
                }
            )
        return items

    monkeypatch.setattr(
        "riskagent.agents.supervisor.analyze_findings_with_llm",
        confirm_test_candidates,
    )

    async def keep_seed_queries(**kwargs: Any) -> list[str]:
        return list(kwargs["seed_queries"])

    monkeypatch.setattr(
        "riskagent.agents.supervisor.rewrite_retrieval_queries_with_llm",
        keep_seed_queries,
    )

    def build(
        *,
        rule_repository: RiskRuleRepository,
        selected_agents: tuple[str, ...] | None = None,
        strategy_provider: Any | None = None,
        path: Path | None = None,
        **repository_kwargs: Any,
    ) -> AnalysisRunRepository:
        index = next(sequence)
        chunk_retriever = repository_kwargs.pop("chunk_retriever", _test_chunk_retriever)
        supervisor = AgentTeamSupervisor(
            repository=rule_repository,
            agent_specs=_test_agent_specs(),
            strategy_provider=strategy_provider or _strategy_provider(selected_agents),
            chunk_retriever=chunk_retriever,
            runtime_root=runtime_root / str(index),
        )
        repository_kwargs.setdefault("use_database", False)
        return AnalysisRunRepository(
            path=path or tmp_path / f"runs-{index}.yaml",
            rule_repository=rule_repository,
            agent_supervisor=supervisor,
            report_provider=deterministic_report_provider,
            chunk_retriever=chunk_retriever,
            **repository_kwargs,
        )

    return build


# ---------------------------------------------------------------------------
# Research fixtures (mock HTTP only — no real Bocha calls, no network).
# These live in the top-level conftest (rather than tests/research/conftest.py)
# so there is exactly one importable ``conftest`` module: existing tests do
# ``from conftest import ...`` and a second same-named conftest would shadow it.

TEST_BOCHA_KEY = "test-secret-key-bocha-12345"

BOCHA_OK_PAYLOAD = {
    "code": 200,
    "log_id": "test-log-id",
    "msg": None,
    "data": {
        "webPages": {
            "value": [
                {
                    "id": "https://api.bochaai.com/#WebPages.0",
                    "name": "联交所披露易:发行人监管公告",
                    "url": "https://www.hkexnews.hk/listedco/doc001.htm",
                    "siteName": "披露易",
                    "siteIcon": "https://www.hkexnews.hk/favicon.ico",
                    "snippet": "发行人于2025年11月刊发的监管公告摘要。",
                    "summary": "发行人监管公告全文摘要,包含诉讼进展。",
                    "datePublished": "2025-11-03T08:00:00+08:00",
                },
                {
                    "id": "https://api.bochaai.com/#WebPages.1",
                    "name": "财新:某公司境外诉讼报道",
                    "url": "https://www.caixin.com/2025/article.html",
                    "siteName": "财新网",
                    "snippet": "据财新报道,该公司境外子公司涉诉。",
                    "summary": "财新对诉讼事件的深度报道。",
                    "datePublished": "2025/06/01",
                },
                {
                    "id": "https://api.bochaai.com/#WebPages.2",
                    "name": "论坛帖子:传闻讨论",
                    "url": "https://example-forum.com/thread/1",
                    "siteName": "某论坛",
                    "snippet": "网友讨论传闻。",
                    "summary": None,
                    "datePublished": "unknown-date",
                },
            ]
        }
    },
}


class RecordingHandler:
    """MockTransport handler that records every request it sees."""

    def __init__(self, responder: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._responder = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request)

    def body_of(self, index: int) -> dict:
        return json.loads(self.requests[index].content.decode("utf-8"))


@pytest.fixture
def bocha_ok_handler() -> RecordingHandler:
    return RecordingHandler(lambda request: httpx.Response(200, json=BOCHA_OK_PAYLOAD))


@pytest.fixture
def bocha_error_handler() -> Callable[[int], RecordingHandler]:
    def factory(status: int) -> RecordingHandler:
        return RecordingHandler(
            lambda request: httpx.Response(status, json={"code": status, "msg": "error"})
        )

    return factory


@pytest.fixture
def bocha_timeout_handler() -> RecordingHandler:
    def raise_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection timed out")

    return RecordingHandler(raise_timeout)


@pytest.fixture
def make_bocha_adapter() -> Callable[..., BochaAdapter]:
    def factory(
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        api_key: str | None = TEST_BOCHA_KEY,
        max_retries: int = 2,
    ) -> BochaAdapter:
        config = BochaConfig(api_key=SecretStr(api_key) if api_key else None)
        return BochaAdapter(
            config,
            transport=httpx.MockTransport(handler),
            max_retries=max_retries,
            retry_backoff_seconds=0.0,
        )

    return factory


PUBLIC_TEST_IP = "93.184.216.34"


@pytest.fixture
def make_safe_fetcher() -> Callable[..., tuple[SafeFetcher, RecordingHandler]]:
    """Build a SafeFetcher wired to a MockTransport route table and fake DNS.

    ``routes`` maps exact URL strings to an ``httpx.Response`` or a callable.
    Unrouted robots.txt URLs return 404 (crawl allowed).  ``dns`` maps
    hostnames to IP lists; ``None`` means resolution failure; unknown hosts
    resolve to a public IP so tests only list the interesting cases.
    """

    def factory(
        routes: dict[str, object] | None = None,
        *,
        dns: dict[str, list[str] | None] | None = None,
        **config_overrides: object,
    ) -> tuple[SafeFetcher, RecordingHandler]:
        route_table = routes or {}
        dns_map = dns or {}

        def respond(request: httpx.Request) -> httpx.Response:
            entry = route_table.get(str(request.url))
            if entry is None:
                return httpx.Response(404, text="not found")
            if callable(entry):
                return entry(request)
            return entry

        handler = RecordingHandler(respond)

        async def resolver(host: str) -> list[str]:
            if host in dns_map:
                value = dns_map[host]
                if value is None:
                    raise socket.gaierror(socket.EAI_NONAME, "name not known")
                return list(value)
            return [PUBLIC_TEST_IP]

        config = ResearchConfig(**config_overrides)
        fetcher = SafeFetcher(
            config, transport=httpx.MockTransport(handler), resolver=resolver
        )
        return fetcher, handler

    return factory
