"""Sourced extraction for litigation, redemption/VAM and related parties.

A missing category stays missing. It is not converted into a low-risk score.
Dates that cannot be parsed, or that fall after the cutoff, do not become
pre-cutoff evidence.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from riskagent.research.core import eligible_pre_cutoff, parse_published_date

_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("litigation", ("诉讼", "仲裁", "纠纷", "起诉")),
    ("redemption_vam", ("对赌", "估值调整", "赎回", "回购权")),
    ("related_party", ("关联方", "关联交易", "关连交易")),
)
_DATE_RE = re.compile(r"(20\d{2}|19\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_CURRENCY_RE = re.compile(r"人民币|人民幣|港元|港币|港幣|美元")
_CURRENCY = {"人民币": "CNY", "人民幣": "CNY", "港元": "HKD", "港币": "HKD", "港幣": "HKD", "美元": "USD"}


class ProspectusBlock(Protocol):
    block_id: str
    page_number: int
    section_path: str
    text: str


def _block_value(block: Any, name: str, default: Any = "") -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def extract_legal_disclosures(
    blocks: list[Any],
    *,
    data_cutoff_date: str | None,
) -> dict[str, Any]:
    """Return sourced disclosures and an explicit list of missing categories."""

    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for block in blocks:
        text = str(_block_value(block, "text", "") or "")
        if not text.strip():
            continue
        for kind, terms in _CATEGORIES:
            if not any(term in text for term in terms):
                continue
            key = (kind, str(_block_value(block, "block_id", "")))
            if key in seen:
                continue
            seen.add(key)
            period = _period_end(text)
            currency = _currency(text)
            date_status = "known" if period else "unknown"
            eligible = bool(period) and eligible_pre_cutoff(period, data_cutoff_date or "")
            found.append(
                {
                    "kind": kind,
                    "excerpt": text.strip().replace("\n", " ")[:180],
                    "evidence_block_id": str(_block_value(block, "block_id", "")),
                    "page_number": int(_block_value(block, "page_number", 0) or 0),
                    "section_path": str(_block_value(block, "section_path", "") or ""),
                    "period_end": period,
                    "date_status": date_status,
                    "currency": currency,
                    "eligible_pre_cutoff": eligible,
                }
            )
    pre_cutoff = [item for item in found if item["eligible_pre_cutoff"]]
    undated = [item for item in found if item["date_status"] == "unknown"]
    present = {item["kind"] for item in [*pre_cutoff, *undated]}
    return {
        "pre_cutoff_disclosures": pre_cutoff,
        "undated_disclosures": undated,
        "post_cutoff_excluded_count": sum(
            item["date_status"] == "known" and not item["eligible_pre_cutoff"] for item in found
        ),
        "missing_categories": [kind for kind, _terms in _CATEGORIES if kind not in present],
        "missing_is_not_low_risk": True,
        "data_cutoff_date": data_cutoff_date or None,
    }


def _period_end(text: str) -> str | None:
    match = _DATE_RE.search(text)
    if match is None:
        return parse_published_date(text)
    return parse_published_date(f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}")


def _currency(text: str) -> str | None:
    match = _CURRENCY_RE.search(text)
    if match is None:
        return None
    return _CURRENCY.get(match.group(0))
