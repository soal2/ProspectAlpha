"""Speculative finding promotion contract tests.

The promotion channel must never manufacture verified conclusions: relevance is
capped below the 0.68 strong-evidence gate and every promoted finding requires
human review.
"""

from __future__ import annotations

from riskagent.analysis_runs import (
    _speculative_findings_from_retrieval,
    _verify_findings,
)


def _events_with_chunks(*texts: str) -> list[dict]:
    return [
        {
            "event_type": "retrieval_query_completed",
            "payload": {
                "chunks": [
                    {
                        "chunk_id": f"chunk_{index}",
                        "document_id": "doc_1",
                        "page_number": index + 10,
                        "section_path": "风险因素",
                        "text": text,
                        "bbox": [1.0, 2.0, 3.0, 4.0],
                        "retrieval_score": 0.9,
                    }
                    for index, text in enumerate(texts)
                ]
            },
        }
    ]


def test_promotes_keyword_matched_candidate_to_weakly_supported_speculative_finding():
    events = _events_with_chunks("投资者可要求公司赎回股份并对赌估值调整。")

    result = _speculative_findings_from_retrieval(events, bound_block_ids=set(), budget=3)

    assert result is not None
    findings, evidence = result
    assert findings[0].requires_human_review is True
    assert findings[0].evaluation_result == "speculative_retrieval_match"
    # Relevance is capped below the 0.68 strong gate → can never be "supported".
    assert all(0.55 <= item.relevance_score <= 0.67 for item in evidence)
    verification = _verify_findings(findings, evidence)
    assert verification[0].status == "weakly_supported"


def test_skips_already_bound_blocks_and_respects_budget():
    events = _events_with_chunks("赎回条款", "关联交易披露", "前五大客户集中")

    result = _speculative_findings_from_retrieval(
        events, bound_block_ids={"chunk_0"}, budget=2
    )

    findings, _ = result
    assert [item.finding_id for item in findings] == ["finding_s001", "finding_s002"]


def test_returns_none_without_keyword_match_or_budget():
    plain = _events_with_chunks("公司业务持续增长。")
    assert _speculative_findings_from_retrieval(plain, bound_block_ids=set(), budget=3) is None

    matched = _events_with_chunks("赎回条款")
    assert (
        _speculative_findings_from_retrieval(matched, bound_block_ids=set(), budget=0)
        is None
    )
