"""Risk report generation boundary."""

from riskagent.reporting.flexible import build_report_document, render_report_markdown
from riskagent.reporting.html import render_backtest_audit_html, render_risk_report_html

__all__ = [
    "build_report_document",
    "render_backtest_audit_html",
    "render_report_markdown",
    "render_risk_report_html",
]
