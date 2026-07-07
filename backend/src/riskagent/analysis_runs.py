"""Synchronous analysis-run service for rule-backed prospectus checks."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from riskagent.risk_rules import RiskRuleRepository, get_default_repository
from riskagent.skills.risk_extract import RuleEvaluation, evaluate_text_against_rules


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANALYSIS_RUNS_PATH = BACKEND_ROOT / "data" / "processed" / "analysis_runs.yaml"


class AnalysisRunCreate(BaseModel):
    company_name: str = "待确认公司"
    document_text: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    max_findings: int = 30


class RiskFinding(BaseModel):
    finding_id: str
    agent_name: str = "rule_agent"
    rule_set_id: str
    rule_version: str
    rule_id: str
    dimension_code: str
    rule_type: str
    category: str
    title: str
    severity: str
    confidence: float
    trigger_condition: str
    evaluation_result: str
    reasoning_summary: str
    evidence_snippets: list[str] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)
    requires_human_review: bool = False


class AnalysisRun(BaseModel):
    run_id: str
    company_name: str
    status: str
    current_stage: str
    created_at: str
    completed_at: str | None = None
    rule_snapshot: dict[str, Any]
    input_summary: dict[str, Any]
    findings: list[RiskFinding] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)


class AnalysisRunRepository:
    def __init__(
        self,
        path: Path | str | None = None,
        rule_repository: RiskRuleRepository | None = None,
    ) -> None:
        configured = path or os.getenv("ANALYSIS_RUNS_PATH") or DEFAULT_ANALYSIS_RUNS_PATH
        self.path = Path(configured)
        self.rule_repository = rule_repository or get_default_repository()

    def create_and_run(self, payload: AnalysisRunCreate) -> AnalysisRun:
        now = _now()
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        rule_snapshot = self.rule_repository.snapshot()
        events = [
            _event("stage_started", "analysis_run", "开始规则驱动分析", "固化规则快照并启动 rule_agent。"),
            _event(
                "rule_snapshot_created",
                "risk_rules",
                "规则快照已固化",
                f"{rule_snapshot['totals']['rule_count']} 条规则进入本次分析快照。",
            ),
        ]
        evaluations = evaluate_text_against_rules(
            _merge_text_and_data(payload.document_text, payload.structured_data),
            repository=self.rule_repository,
        )
        findings = self._findings_from_evaluations(evaluations, payload.max_findings)
        events.append(
            _event(
                "agent_completed",
                "rule_agent",
                "规则 Agent 分析完成",
                f"生成 {len(findings)} 条规则发现。",
            )
        )
        run = AnalysisRun(
            run_id=run_id,
            company_name=payload.company_name,
            status="completed",
            current_stage="completed",
            created_at=now,
            completed_at=_now(),
            rule_snapshot=rule_snapshot,
            input_summary={
                "document_text_chars": len(payload.document_text),
                "structured_data_keys": sorted(payload.structured_data.keys()),
            },
            findings=findings,
            events=events,
        )
        self._save_run(run)
        return run

    def get(self, run_id: str) -> AnalysisRun:
        data = self._load_all()
        if run_id not in data:
            raise KeyError(run_id)
        return AnalysisRun.model_validate(data[run_id])

    def list_runs(self) -> list[AnalysisRun]:
        return [AnalysisRun.model_validate(item) for item in self._load_all().values()]

    def _findings_from_evaluations(
        self,
        evaluations: list[RuleEvaluation],
        max_findings: int,
    ) -> list[RiskFinding]:
        triggered = [item for item in evaluations if item.evaluation_result == "triggered"]
        findings = []
        for idx, item in enumerate(triggered[:max_findings], start=1):
            findings.append(
                RiskFinding(
                    finding_id=f"finding_{idx:03d}",
                    rule_set_id=item.rule_set_id,
                    rule_version=item.rule_version,
                    rule_id=item.rule_id,
                    dimension_code=item.dimension_code,
                    rule_type=item.rule_type,
                    category=item.category,
                    title=item.title,
                    severity=_severity(item),
                    confidence=min(0.95, 0.55 + len(item.matched_terms) * 0.1),
                    trigger_condition=item.trigger_condition,
                    evaluation_result=item.evaluation_result,
                    reasoning_summary=item.reasoning_summary,
                    evidence_snippets=item.evidence_snippets,
                    matched_terms=item.matched_terms,
                    requires_human_review=not item.evidence_snippets,
                )
            )
        return findings

    def _save_run(self, run: AnalysisRun) -> None:
        data = self._load_all()
        data[run.run_id] = run.model_dump()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def _load_all(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}


def _merge_text_and_data(document_text: str, structured_data: dict[str, Any]) -> str:
    if not structured_data:
        return document_text
    data_lines = ["结构化数据："]
    for key, value in sorted(structured_data.items()):
        data_lines.append(f"{key}: {value}")
    return f"{document_text}\n\n" + "\n".join(data_lines)


def _severity(item: RuleEvaluation) -> str:
    high_terms = {"对赌", "赎回", "持续经营", "诉讼", "担保", "现金流", "资产负债率"}
    if high_terms.intersection(item.matched_terms):
        return "high"
    if len(item.matched_terms) >= 2:
        return "medium"
    return "low"


def _event(event_type: str, actor_name: str, title: str, summary: str) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "actor_name": actor_name,
        "title": title,
        "summary": summary,
        "created_at": _now(),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_default_analysis_repository() -> AnalysisRunRepository:
    return AnalysisRunRepository()
