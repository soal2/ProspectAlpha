"""Knowledge library routes: archived risk reports from completed analysis runs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from riskagent.database import SessionLocal, init_db
from riskagent.models import ReportLibraryRow

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class KnowledgeReportSummary(BaseModel):
    report_id: str
    source_run_id: str
    company_name: str
    document_id: str
    rule_set_id: str
    overall_risk_level: str
    finding_count: int
    section_count: int
    created_at: str


class KnowledgeReport(KnowledgeReportSummary):
    markdown: str
    sections: list[dict[str, Any]]


def _summary(row: ReportLibraryRow) -> dict[str, Any]:
    return {
        "report_id": row.report_id,
        "source_run_id": row.source_run_id,
        "company_name": row.company_name,
        "document_id": row.document_id,
        "rule_set_id": row.rule_set_id,
        "overall_risk_level": row.overall_risk_level,
        "finding_count": row.finding_count,
        "section_count": len(row.sections or []),
        "created_at": row.created_at,
    }


@router.get("/reports", response_model=list[KnowledgeReportSummary])
def list_reports() -> list[KnowledgeReportSummary]:
    init_db()
    with SessionLocal() as session:
        rows = session.scalars(
            select(ReportLibraryRow).order_by(ReportLibraryRow.created_at.desc())
        ).all()
        return [KnowledgeReportSummary.model_validate(_summary(row)) for row in rows]


@router.get("/reports/{report_id}", response_model=KnowledgeReport)
def get_report(report_id: str) -> KnowledgeReport:
    init_db()
    with SessionLocal() as session:
        row = session.get(ReportLibraryRow, report_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Report not found: {report_id}",
            )
        return KnowledgeReport.model_validate(
            {**_summary(row), "markdown": row.markdown, "sections": row.sections or []}
        )
