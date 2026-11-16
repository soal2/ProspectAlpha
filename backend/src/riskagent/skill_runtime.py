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
from riskagent.research.core import SourceAdapter, eligible_pre_cutoff, parse_published_date


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
    data_cutoff_date: str = ""
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
    eligible_pre_cutoff: bool = False
    attribution: Literal["pre_listing", "post_listing"] = "pre_listing"


class SkillHandler(Protocol):
    def invoke(self, invocation: SkillInvocation) -> SkillResult: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def invocation_cutoff(invocation: SkillInvocation) -> str:
    """Cutoff carried by the invocation, otherwise an explicit structured copy."""

    if invocation.data_cutoff_date.strip():
        return invocation.data_cutoff_date.strip()
    raw = invocation.structured_inputs.get("data_cutoff_date")
    return str(raw).strip() if raw else ""


_RUNWAY_DURATIONS = {3, 6, 9, 12}


def _runway_months(cash: float, operating: float, duration_months: int) -> float | None:
    """Cash runway in months for one unambiguous period.

    Positive operating cashflow is not a burn, so the runway is absent.
    Zero cash is kept and produces a zero runway. Negative cash is rejected.
    """

    if duration_months not in _RUNWAY_DURATIONS:
        return None
    if operating >= 0:
        return None
    if cash < 0:
        return None
    return round(cash / abs(operating) * duration_months, 2)


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
        rows = [item for item in periods if isinstance(item, dict)]
        if len(rows) != len(periods):
            return _result(
                invocation,
                started_at=started,
                status="insufficient_data",
                summary="现金流期间记录格式不明确，未计算现金跑道。",
                limitations=["含糊或非结构化期间不会被编造成数值。"],
            )
        refs = [str(item) for item in invocation.structured_inputs.get("cashflow_evidence_refs", [])]
        limitations = [str(item) for item in invocation.structured_inputs.get("cashflow_limitations", []) if str(item).strip()]
        if not refs:
            limitations.append("计算字段尚未绑定原始 PDF 证据。")
        if any(row.get("unit_consistent") is False or row.get("ambiguous") or not row.get("period_end") for row in rows):
            limitations.append("存在无法解析的报告期、比较列或互相冲突的单位，已拒绝输出现金跑道。")
            return _result(
                invocation,
                started_at=started,
                status="insufficient_data",
                summary="现金流期间或单位含糊，未计算现金跑道。",
                result={
                    "period_count": len(rows),
                    "currency": rows[-1].get("currency"),
                    "unit": rows[-1].get("unit"),
                    "unit_consistent": all(row.get("unit_consistent", True) for row in rows),
                },
                evidence_refs=refs,
                limitations=limitations,
            )
        dated = sorted(rows, key=lambda row: str(row.get("period_end")))
        latest = dated[-1]
        cash = latest.get("cash_and_equivalents")
        operating = latest.get("operating_cashflow")
        duration = latest.get("duration_months")
        if (
            not isinstance(cash, (int, float))
            or isinstance(cash, bool)
            or not isinstance(operating, (int, float))
            or isinstance(operating, bool)
            or not isinstance(duration, int)
            or isinstance(duration, bool)
        ):
            return _result(
                invocation,
                started_at=started,
                status="insufficient_data",
                summary="现金、经营现金流或期间长度缺失，未计算现金跑道。",
                limitations=["需要带证据引用的 cash_and_equivalents、operating_cashflow 与 duration_months。"],
                evidence_refs=refs,
            )
        if cash < 0 or duration not in _RUNWAY_DURATIONS:
            limitations.append("现金为负或期间长度不是年、半年、三季或季度，已拒绝输出现金跑道。")
            return _result(
                invocation,
                started_at=started,
                status="insufficient_data",
                summary="最新报告期无法支持现金跑道计算。",
                evidence_refs=refs,
                limitations=limitations,
            )
        runway = _runway_months(float(cash), float(operating), duration)
        risk: RiskLevel = "low" if operating >= 0 else "high" if runway is not None and runway < 12 else "medium"
        return _result(
            invocation,
            started_at=started,
            status="completed" if refs else "partial",
            summary="已按报告期截止日期与期间长度计算现金跑道。",
            risk_level=risk,
            result={
                "period_count": len(dated),
                "latest_period_end": latest.get("period_end"),
                "duration_months": duration,
                "cash_runway_months": runway,
                "latest_operating_cashflow": operating,
                "latest_cash_and_equivalents": cash,
                "currency": latest.get("currency"),
                "unit": latest.get("unit"),
                "unit_consistent": True,
                "periods": dated,
            },
            evidence_refs=refs,
            limitations=limitations,
        )


def _peer_rows(peers: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    return [
        {
            "stock_code": item.get("stock_code"),
            "as_of_date": item.get("as_of_date") or item.get("listing_date") or item.get("announcement_date"),
            "valuation_basis": item.get("valuation_basis"),
            metric: item.get(metric),
        }
        for item in peers
        if isinstance(item, dict)
        and isinstance(item.get(metric), (int, float))
        and not isinstance(item.get(metric), bool)
    ]


def _peer_snapshot(peers: list[dict[str, Any]]) -> dict[str, Any] | None:
    """First-day peer levels without an issue-valuation premium."""

    metric = next(
        (
            key
            for key in ("pe", "ps", "ev_ebitda", "pb")
            if any(
                isinstance(item.get(key), (int, float)) and not isinstance(item.get(key), bool)
                for item in peers
                if isinstance(item, dict)
            )
        ),
        None,
    )
    if metric is None:
        return None
    values = [float(item[metric]) for item in _peer_rows(peers, metric)]
    if not values:
        return None
    return {
        "valuation_basis": "first_day",
        "metric": metric,
        "peer_median": sorted(values)[len(values) // 2],
        "premium_percent": None,
        "peer_count": len(values),
        "peers": _peer_rows(peers, metric),
    }


def _comparison(issuer: dict[str, Any], peers: list[dict[str, Any]]) -> dict[str, Any] | None:
    metric = next(
        (
            key
            for key in ("pe", "ps", "ev_ebitda", "pb")
            if isinstance(issuer.get(key), (int, float)) and not isinstance(issuer.get(key), bool)
        ),
        None,
    )
    if metric is None:
        return None
    values = [
        float(item[metric])
        for item in peers
        if isinstance(item, dict)
        and isinstance(item.get(metric), (int, float))
        and not isinstance(item.get(metric), bool)
    ]
    if not values:
        return None
    median = sorted(values)[len(values) // 2]
    premium = None if median == 0 else round((float(issuer[metric]) / median - 1) * 100, 2)
    return {
        "metric": metric,
        "issuer_multiple": issuer[metric],
        "peer_median": median,
        "premium_percent": premium,
        "peer_count": len(values),
        "peers": _peer_rows(peers, metric),
    }


class ValuationCheckerHandler:
    def invoke(self, invocation: SkillInvocation) -> SkillResult:
        started = _now()
        from riskagent.dataset_registry import (
            lookup_valuation_peers,
            normalize_valuation_basis,
            select_valuation_peers,
        )

        cutoff = invocation_cutoff(invocation)
        issuer = invocation.structured_inputs.get("issuer_valuation")
        if not isinstance(issuer, dict):
            return _result(
                invocation,
                started_at=started,
                status="insufficient_data",
                summary="缺少发行人估值指标。",
                limitations=["不会回退到硬编码同行倍数或默认股票代码。"],
            )
        issuer_basis = normalize_valuation_basis(issuer.get("valuation_basis")) or "issue"
        explicit = invocation.structured_inputs.get("peer_valuations")
        industry = str(invocation.structured_inputs.get("industry") or "").strip()
        peer_source = "invocation snapshot"
        if isinstance(explicit, list) and explicit:
            pool = [item for item in explicit if isinstance(item, dict)]
            issue_peers = list(
                select_valuation_peers(
                    pool,
                    industry=industry,
                    data_cutoff_date=cutoff,
                    basis="issue",
                    exclude_stock_code=invocation.stock_code,
                    limit=10,
                    match_industry=False,
                )
            )
            first_day_peers = list(
                select_valuation_peers(
                    pool,
                    industry=industry,
                    data_cutoff_date=cutoff,
                    basis="first_day",
                    exclude_stock_code=invocation.stock_code,
                    limit=10,
                    match_industry=False,
                )
            )
        else:
            issue_peers = []
            first_day_peers = []
            if industry:
                issue_peers = list(
                    lookup_valuation_peers(
                        industry,
                        exclude_stock_code=invocation.stock_code,
                        data_cutoff_date=cutoff,
                        basis="issue",
                    )
                )
                first_day_peers = list(
                    lookup_valuation_peers(
                        industry,
                        exclude_stock_code=invocation.stock_code,
                        data_cutoff_date=cutoff,
                        basis="first_day",
                    )
                )
                peer_source = "hk-ipo-subscription-history@1.0.0"
        supplied = explicit if isinstance(explicit, list) else []
        excluded_count = max(len([item for item in supplied if isinstance(item, dict)]) - len(issue_peers) - len(first_day_peers), 0)
        issue_view = _comparison(issuer, issue_peers) if issuer_basis == "issue" else None
        first_day_view = _comparison(issuer, first_day_peers) if issuer_basis == "first_day" else _peer_snapshot(first_day_peers)
        active = issue_view if issuer_basis == "issue" else first_day_view
        limitations = [
            "上市首日及上市后估值与发行估值分开保存，不进入发行估值溢价。"
        ]
        if excluded_count:
            limitations.append(
                f"已排除 {excluded_count} 条晚于 data_cutoff_date、日期不明或口径不明的同行，未进入上市前证据。"
            )
        if issuer_basis == "issue" and issue_view is None:
            return _result(
                invocation,
                started_at=started,
                status="insufficient_data",
                summary="缺少截止日前可核验的发行估值同行，未用上市首日倍数代替。",
                risk_level="unrated",
                result={
                    "valuation_basis": "issue",
                    "issue_valuation": None,
                    "first_day_valuation": first_day_view,
                    "post_listing_valuation": first_day_view,
                    "excluded_peer_count": excluded_count,
                    "data_cutoff_date": cutoff or None,
                    "peer_source": peer_source,
                },
                limitations=limitations + ["不会把上市首日市盈率或市净率计入发行估值比较。"],
            )
        if active is None:
            return _result(
                invocation,
                started_at=started,
                status="insufficient_data",
                summary="发行人与同行缺少同一估值口径。",
                limitations=limitations,
            )
        premium = active["premium_percent"]
        risk: RiskLevel = "unrated"
        evidence: list[str] = []
        if issuer_basis == "issue":
            risk = "high" if premium is not None and premium > 30 else "medium" if premium is not None and premium > 10 else "low"
            evidence = [str(item) for item in invocation.structured_inputs.get("valuation_evidence_refs", [])]
            if peer_source.startswith("hk-ipo") and issue_peers:
                evidence.append("dataset:hk-ipo-subscription-history@1.0.0")
        return _result(
            invocation,
            started_at=started,
            status="completed",
            summary=(
                f"已按发行估值 {active['metric']} 与 {active['peer_count']} 家截止日前同行完成比较。"
                if issuer_basis == "issue"
                else f"已单独整理上市首日 {active['metric']} 比较，未并入发行估值。"
            ),
            risk_level=risk,
            result={
                "metric": active["metric"] if issuer_basis == "issue" else None,
                "issuer_multiple": active["issuer_multiple"] if issuer_basis == "issue" else None,
                "peer_median": active["peer_median"] if issuer_basis == "issue" else None,
                "premium_percent": premium if issuer_basis == "issue" else None,
                "peer_count": active["peer_count"] if issuer_basis == "issue" else 0,
                "valuation_basis": issuer_basis,
                "issue_valuation": issue_view,
                "first_day_valuation": first_day_view,
                "post_listing_valuation": first_day_view,
                "peer_selection_basis": industry or "显式同行快照",
                "peer_source": peer_source,
                "excluded_peer_count": excluded_count,
                "data_cutoff_date": cutoff or None,
                "currency": issuer.get("currency") or ("HKD" if peer_source.startswith("hk-ipo") else None),
            },
            evidence_refs=evidence,
            limitations=limitations,
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
        cutoff = invocation_cutoff(invocation)
        limitations: list[str] = []
        artifacts: list[ResearchArtifact] = []
        excluded_sources = 0
        try:
            seen: set[str] = set()
            response = asyncio.run(self.client.search(SearchQuery(text=query, count=10)))
            for item in response.hits:
                url = item.url.strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                published = parse_published_date(item.published_at)
                if not eligible_pre_cutoff(published, cutoff):
                    excluded_sources += 1
                    continue
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
                        published_at=published,
                        retrieved_at=_now(),
                        source_hash=digest,
                        eligible_pre_cutoff=True,
                        attribution="pre_listing",
                    )
                )
        except RuntimeError as exc:
            limitations.append(str(exc))
        if excluded_sources:
            limitations.append(
                f"已排除 {excluded_sources} 条发布日期晚于 data_cutoff_date 或日期不明的新闻检索结果，未进入上市前证据与评分。"
            )
        subscription = invocation.structured_inputs.get("subscription_history")
        if not isinstance(subscription, dict) or not subscription:
            from riskagent.dataset_registry import lookup_subscription_history

            subscription = lookup_subscription_history(
                invocation.stock_code,
                data_cutoff_date=cutoff,
            )
        elif not eligible_pre_cutoff(subscription.get("announcement_date"), cutoff):
            subscription = None
        else:
            subscription = {**subscription, "eligible_pre_cutoff": True, "attribution": "pre_listing"}
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
            summary="已分别整理认购热度与截止日前的新闻/社交舆情证据。" if status != "insufficient_data" else "认购历史和截止日前外部舆情均不足。",
            result={
                "data_cutoff_date": cutoff or None,
                "subscription_heat": subscription if has_subscription else None,
                "research_artifacts": [item.model_dump() for item in artifacts],
                "news_count": sum(item.source_type in {"news", "analyst", "official"} for item in artifacts),
                "social_count": sum(item.source_type == "social" for item in artifacts),
                "excluded_source_count": excluded_sources,
            },
            evidence_refs=[item.artifact_id for item in artifacts],
            limitations=limitations + ([] if has_subscription else ["截止日前没有可核验的认购记录。"]),
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
