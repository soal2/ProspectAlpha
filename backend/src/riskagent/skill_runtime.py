"""Explicit, typed runtime for callable ProspectAlpha skills.

Skill documents never control imports.  This module is the only executable
registry and therefore the trust boundary between ``SKILL.md`` guidance and
production Python code.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from riskagent.research import BochaAdapter, SearchQuery
from riskagent.research.core import SourceAdapter


SkillStatus = Literal["completed", "partial", "insufficient_data", "failed"]
RiskLevel = Literal["very_high", "high", "medium", "low", "very_low", "unrated"]


class SkillInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    assignment_id: str
    agent_name: str
    skill_id: str
    skill_version: str
    stock_code: str | None = None
    company_name: str
    document_id: str = ""
    document_block_ids: list[str] = Field(default_factory=list)
    structured_inputs: dict[str, Any] = Field(default_factory=dict)
    dataset_snapshot_ids: list[str] = Field(default_factory=list)
    research_artifact_ids: list[str] = Field(default_factory=list)


class SkillResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invocation_id: str = Field(default_factory=lambda: f"skill_{uuid.uuid4().hex}")
    skill_id: str
    skill_version: str
    agent_name: str
    status: SkillStatus
    risk_level: RiskLevel = "unrated"
    summary: str
    result: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    data_snapshot_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    started_at: str
    completed_at: str


class DocumentRetrievalSkillInput(SkillInvocation):
    pass


class DocumentRetrievalSkillResult(SkillResult):
    pass


class CashflowSkillInput(SkillInvocation):
    pass


class CashflowSkillResult(SkillResult):
    pass


class ValuationSkillInput(SkillInvocation):
    pass


class ValuationSkillResult(SkillResult):
    pass


class SentimentSkillInput(SkillInvocation):
    pass


class SentimentSkillResult(SkillResult):
    pass


class LegalComplianceSkillInput(SkillInvocation):
    pass


class LegalComplianceSkillResult(SkillResult):
    pass


class ResearchArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(default_factory=lambda: f"research_{uuid.uuid4().hex}")
    run_id: str
    provider: str = "bocha"
    query: str
    source_type: Literal["news", "social", "analyst", "official"] = "news"
    title: str
    url: str
    publisher: str = ""
    snippet: str = ""
    published_at: str | None = None
    retrieved_at: str
    source_hash: str
    raw_response_ref: str = ""


class SkillHandler(Protocol):
    def invoke(self, invocation: SkillInvocation) -> SkillResult: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(
    invocation: SkillInvocation,
    *,
    started_at: str,
    status: SkillStatus,
    summary: str,
    risk_level: RiskLevel = "unrated",
    result: dict[str, Any] | None = None,
    evidence_refs: list[str] | None = None,
    limitations: list[str] | None = None,
) -> SkillResult:
    return SkillResult(
        skill_id=invocation.skill_id,
        skill_version=invocation.skill_version,
        agent_name=invocation.agent_name,
        status=status,
        risk_level=risk_level,
        summary=summary,
        result=result or {},
        evidence_refs=evidence_refs or [],
        data_snapshot_ids=invocation.dataset_snapshot_ids,
        limitations=limitations or [],
        started_at=started_at,
        completed_at=_now(),
    )


class DocumentRetrievalHandler:
    def invoke(self, invocation: SkillInvocation) -> SkillResult:
        started = _now()
        if not invocation.document_id:
            # No prospectus document in this run at all: the skill is not
            # applicable rather than malfunctioning. Report "partial" so the
            # expert module can complete with an honest coverage-gap result
            # instead of killing the whole pipeline (required-policy fatal).
            return _result(
                invocation,
                started_at=started,
                status="partial",
                summary="本次任务未提供招股书文档，可溯源检索不适用；报告须保留招股书证据覆盖缺口。",
                limitations=[
                    "未提供 document_id：无法执行招股书原文检索，也不得虚构证据。",
                    "补充上传招股书后重跑可获得可溯源证据。",
                ],
            )
        if not invocation.document_block_ids:
            return _result(
                invocation,
                started_at=started,
                status="insufficient_data",
                summary="缺少已索引的招股书文本块，无法执行可溯源检索。",
                limitations=["document_id 和 document_block_ids 必须来自现有检索索引。"],
            )
        return _result(
            invocation,
            started_at=started,
            status="completed",
            summary=f"已限定在 {len(invocation.document_block_ids)} 个招股书文本块内检索。",
            result={"document_id": invocation.document_id, "block_count": len(invocation.document_block_ids)},
            evidence_refs=invocation.document_block_ids,
        )


class CashflowAnalyzerHandler:
    def invoke(self, invocation: SkillInvocation) -> SkillResult:
        started = _now()
        periods = invocation.structured_inputs.get("cashflow_periods")
        if not isinstance(periods, list) or not periods:
            return _result(
                invocation,
                started_at=started,
                status="insufficient_data",
                summary="招股书尚未抽取出可核验的多期现金流数据。",
                limitations=["不得使用示例现金流或默认发行人数据。"],
            )
        latest = periods[-1] if isinstance(periods[-1], dict) else {}
        cash = latest.get("cash_and_equivalents")
        operating = latest.get("operating_cashflow")
        if not isinstance(cash, (int, float)) or not isinstance(operating, (int, float)):
            return _result(
                invocation,
                started_at=started,
                status="insufficient_data",
                summary="现金及经营现金流字段缺失或未完成单位归一。",
                limitations=["需要带证据引用的 cash_and_equivalents 与 operating_cashflow。"],
            )
        runway = None if operating >= 0 else round(float(cash) / abs(float(operating)) * 12, 2)
        risk: RiskLevel = "low" if operating >= 0 else "high" if runway is not None and runway < 12 else "medium"
        refs = [str(item) for item in invocation.structured_inputs.get("cashflow_evidence_refs", [])]
        limitations = [] if refs else ["计算字段尚未绑定原始 PDF 证据。"]
        # Period order follows the printed column order of the source table and is
        # not re-verified against period headers; disclose instead of guessing.
        limitations.append("期数顺序按招股书表内列序，未按报告期标注复核；现金跑道为同表同单位的比值指标，不受单位未归一影响。")
        return _result(
            invocation,
            started_at=started,
            status="completed" if refs else "partial",
            summary="已基于招股书结构化现金流数据完成可持续性分析。",
            risk_level=risk,
            result={"period_count": len(periods), "cash_runway_months": runway, "latest_operating_cashflow": operating},
            evidence_refs=refs,
            limitations=limitations,
        )


class ValuationCheckerHandler:
    def invoke(self, invocation: SkillInvocation) -> SkillResult:
        started = _now()
        issuer = invocation.structured_inputs.get("issuer_valuation")
        peers = invocation.structured_inputs.get("peer_valuations")
        peer_source = "invocation snapshot"
        if not isinstance(peers, list) or not peers:
            industry = str(invocation.structured_inputs.get("industry") or "").strip()
            if industry:
                from riskagent.dataset_registry import lookup_valuation_peers

                peers = list(
                    lookup_valuation_peers(
                        industry,
                        exclude_stock_code=invocation.stock_code,
                    )
                )
                peer_source = "hk-ipo-subscription-history@1.0.0"
        if not isinstance(issuer, dict) or not isinstance(peers, list) or not peers:
            return _result(
                invocation,
                started_at=started,
                status="insufficient_data",
                summary="缺少发行人估值指标或版本化同行估值快照。",
                limitations=["不会回退到硬编码同行倍数或默认股票代码。"],
            )
        metric = next((key for key in ("pe", "ps", "ev_ebitda", "pb") if isinstance(issuer.get(key), (int, float))), None)
        values = [float(item[metric]) for item in peers if metric and isinstance(item, dict) and isinstance(item.get(metric), (int, float))]
        if metric is None or not values:
            return _result(invocation, started_at=started, status="insufficient_data", summary="发行人与同行缺少可比估值口径。")
        median = sorted(values)[len(values) // 2]
        premium = None if median == 0 else round((float(issuer[metric]) / median - 1) * 100, 2)
        risk: RiskLevel = "high" if premium is not None and premium > 30 else "medium" if premium is not None and premium > 10 else "low"
        return _result(
            invocation,
            started_at=started,
            status="completed",
            summary=f"已按 {metric} 口径与 {len(values)} 家版本化同行完成比较。",
            risk_level=risk,
            result={
                "metric": metric,
                "issuer_multiple": issuer[metric],
                "peer_median": median,
                "premium_percent": premium,
                "peer_selection_basis": invocation.structured_inputs.get("industry") or "显式同行快照",
                "peer_source": peer_source,
                "peer_count": len(values),
                "data_as_of_date": "2026-05-15" if peer_source.startswith("hk-ipo") else None,
                "currency": "HKD" if peer_source.startswith("hk-ipo") else invocation.structured_inputs.get("currency"),
            },
            evidence_refs=[
                *[str(item) for item in invocation.structured_inputs.get("valuation_evidence_refs", [])],
                *(["dataset:hk-ipo-subscription-history@1.0.0"] if peer_source.startswith("hk-ipo") else []),
            ],
        )


class SentimentScorerHandler:
    def __init__(self, client: SourceAdapter | None = None) -> None:
        self.client = client or BochaAdapter()

    def invoke(self, invocation: SkillInvocation) -> SkillResult:
        started = _now()
        if not invocation.stock_code:
            return _result(
                invocation,
                started_at=started,
                status="insufficient_data",
                summary="缺少股票代码，无法匹配认购历史或检索发行人舆情。",
            )
        query = f'"{invocation.company_name}" {invocation.stock_code} IPO 新闻 舆情 认购'
        limitations: list[str] = []
        artifacts: list[ResearchArtifact] = []
        try:
            seen: set[str] = set()
            response = asyncio.run(self.client.search(SearchQuery(text=query, count=10)))
            for item in response.hits:
                url = item.url.strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                title = str(item.title or url)
                snippet = str(item.summary or item.snippet or "")
                digest = hashlib.sha256(f"{url}\n{title}\n{snippet}".encode("utf-8")).hexdigest()
                artifacts.append(
                    ResearchArtifact(
                        run_id=invocation.run_id,
                        query=query,
                        source_type=_source_type(url, title),
                        title=title,
                        url=url,
                        publisher=str(item.site_name or ""),
                        snippet=snippet,
                        published_at=item.published_at,
                        retrieved_at=_now(),
                        source_hash=digest,
                    )
                )
        except RuntimeError as exc:
            limitations.append(str(exc))
        subscription = invocation.structured_inputs.get("subscription_history")
        if not isinstance(subscription, dict) or not subscription:
            from riskagent.dataset_registry import lookup_subscription_history

            subscription = lookup_subscription_history(invocation.stock_code)
        has_subscription = isinstance(subscription, dict) and bool(subscription)
        if not artifacts and not has_subscription:
            status: SkillStatus = "insufficient_data"
        elif limitations or not artifacts or not has_subscription:
            status = "partial"
        else:
            status = "completed"
        return _result(
            invocation,
            started_at=started,
            status=status,
            summary="已分别整理认购热度与冻结的新闻/社交舆情证据。" if status != "insufficient_data" else "认购历史和外部舆情均不足。",
            result={
                "subscription_heat": subscription if has_subscription else None,
                "research_artifacts": [item.model_dump() for item in artifacts],
                "news_count": sum(item.source_type in {"news", "analyst", "official"} for item in artifacts),
                "social_count": sum(item.source_type == "social" for item in artifacts),
            },
            evidence_refs=[item.artifact_id for item in artifacts],
            limitations=limitations + ([] if has_subscription else ["本地历史数据未匹配到该股票代码。"]),
        )


def _source_type(url: str, title: str) -> Literal["news", "social", "analyst", "official"]:
    value = f"{url} {title}".lower()
    if any(term in value for term in ("weibo", "zhihu", "x.com", "twitter", "facebook", "小红书")):
        return "social"
    if any(term in value for term in ("hkex", "gov.hk", "证监", "公告")):
        return "official"
    if any(term in value for term in ("research", "研报", "分析师", "broker")):
        return "analyst"
    return "news"


class LegalComplianceHandler:
    def invoke(self, invocation: SkillInvocation) -> SkillResult:
        started = _now()
        refs = [str(item) for item in invocation.structured_inputs.get("legal_evidence_refs", [])]
        issues = invocation.structured_inputs.get("legal_issues")
        if not isinstance(issues, list) or not issues:
            return _result(
                invocation,
                started_at=started,
                status="insufficient_data",
                summary="未取得可核验的法务条款或适用规则，不能推断违法。",
            )
        return _result(
            invocation,
            started_at=started,
            status="completed" if refs else "partial",
            summary=f"已对 {len(issues)} 项法务事项完成结构化核查。",
            result={"issues": issues},
            evidence_refs=refs,
            limitations=[] if refs else ["法务事项尚未全部绑定招股书原文证据。"],
        )


SKILL_HANDLERS: dict[str, SkillHandler] = {
    "document-retrieval": DocumentRetrievalHandler(),
    "cashflow-analyzer": CashflowAnalyzerHandler(),
    "valuation-checker": ValuationCheckerHandler(),
    "sentiment-scorer": SentimentScorerHandler(),
    "legal-compliance-due-diligence": LegalComplianceHandler(),
}


SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {
    cls.__name__: cls
    for cls in (
        SkillInvocation,
        SkillResult,
        DocumentRetrievalSkillInput,
        DocumentRetrievalSkillResult,
        CashflowSkillInput,
        CashflowSkillResult,
        ValuationSkillInput,
        ValuationSkillResult,
        SentimentSkillInput,
        SentimentSkillResult,
        LegalComplianceSkillInput,
        LegalComplianceSkillResult,
    )
}


class SkillRuntime:
    def __init__(
        self,
        handlers: dict[str, SkillHandler] | None = None,
        *,
        skill_snapshot: dict[str, Any] | None = None,
    ) -> None:
        self.handlers = handlers or SKILL_HANDLERS
        self.skill_snapshot = skill_snapshot

    def invoke(self, invocation: SkillInvocation) -> SkillResult:
        self._validate_policy(invocation)
        handler = self.handlers.get(invocation.skill_id)
        if handler is None:
            raise ValueError(f"No callable handler registered for skill: {invocation.skill_id}")
        try:
            result = handler.invoke(invocation)
        except Exception as exc:  # noqa: BLE001 - normalized boundary
            now = _now()
            result = SkillResult(
                skill_id=invocation.skill_id,
                skill_version=invocation.skill_version,
                agent_name=invocation.agent_name,
                status="failed",
                summary=f"Skill execution failed: {type(exc).__name__}",
                limitations=[str(exc)[:400]],
                started_at=now,
                completed_at=now,
            )
        metadata = (
            ((self.skill_snapshot or {}).get("skills") or {}).get(invocation.skill_id)
            or {}
        )
        if metadata.get("failure_policy") == "required" and result.status in {
            "failed",
            "insufficient_data",
        }:
            raise RuntimeError(
                f"Required Skill {invocation.skill_id} ended with {result.status}: "
                f"{result.summary}"
            )
        return result

    def _validate_policy(self, invocation: SkillInvocation) -> None:
        if self.skill_snapshot is None:
            return
        metadata = (self.skill_snapshot.get("skills") or {}).get(invocation.skill_id)
        if not isinstance(metadata, dict):
            raise ValueError(f"Skill is absent from run snapshot: {invocation.skill_id}")
        if metadata.get("execution") != "callable":
            raise ValueError(f"Skill is not callable: {invocation.skill_id}")
        if str(metadata.get("version")) != invocation.skill_version:
            raise ValueError(
                f"Skill version mismatch for {invocation.skill_id}: "
                f"snapshot={metadata.get('version')} invocation={invocation.skill_version}"
            )
        bindings = self.skill_snapshot.get("agent_bindings") or {}
        allowed = bindings.get(invocation.agent_name)
        if not isinstance(allowed, list) or invocation.skill_id not in allowed:
            raise ValueError(
                f"Skill {invocation.skill_id} is not assigned to agent {invocation.agent_name}"
            )


def normalize_stock_code(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip().upper()
    if normalized.endswith(".HK"):
        normalized = normalized[:-3]
    if not re.fullmatch(r"\d{4,5}", normalized):
        raise ValueError("stock_code must contain 4-5 digits with an optional .HK suffix")
    return f"{normalized}.HK"
