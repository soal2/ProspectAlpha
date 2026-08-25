"""Traditional-Chinese section detection contract tests.

HK prospectuses are mostly published in traditional Chinese; the simplified-only
marker set silently routed every block into the generic fallback section
(field finding #6 of the 2026-08-17 live assessment).
"""

from __future__ import annotations

from riskagent.analysis_runs import _detect_section as _detect_section_runs
from riskagent.documents import _detect_section


def test_documents_detect_section_matches_traditional_headings():
    assert _detect_section("風險因素 Risk Factors\n", "招股书正文") == "风险因素"
    assert _detect_section("關連交易\n", "招股书正文") == "关联交易"
    assert _detect_section("財務資料\n", "招股书正文") == "财务资料"
    assert _detect_section("歷史及公司架構\n", "招股书正文") == "历史及公司架构"


def test_documents_detect_section_keeps_simplified_and_fallback():
    assert _detect_section("风险因素\n", "招股书正文") == "风险因素"
    assert _detect_section("董事及監事\n", "招股书正文") == "招股书正文"


def test_analysis_runs_detect_section_matches_traditional_headings():
    assert _detect_section_runs("風險因素") == "风险因素"
    assert _detect_section_runs("關聯交易") == "关联交易"
    assert _detect_section_runs("財務資料") == "财务资料"
    assert _detect_section_runs("未知章节") is None
