"""Versioned registry for immutable local Skill datasets."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DatasetDefinition:
    dataset_id: str
    version: str
    as_of_date: str
    coverage_period: str
    source: str
    field_definition: str
    currency: str | None
    adjustment_method: str | None
    path: Path

    def snapshot(self) -> dict[str, str | None]:
        return {
            "dataset_id": self.dataset_id,
            "version": self.version,
            "as_of_date": self.as_of_date,
            "coverage_period": self.coverage_period,
            "source": self.source,
            "field_definition": self.field_definition,
            "currency": self.currency,
            "adjustment_method": self.adjustment_method,
            "file_hash": hashlib.sha256(self.path.read_bytes()).hexdigest(),
            "path": str(self.path.relative_to(BACKEND_ROOT)),
        }


DATASETS: tuple[DatasetDefinition, ...] = (
    DatasetDefinition(
        dataset_id="hk-ipo-subscription-history",
        version="1.0.0",
        as_of_date="2026-05-15",
        coverage_period="2016-2026",
        source="repository static asset; upstream provenance pending confirmation",
        field_definition="港股 IPO 认购、中签、发行结构及上市表现字段",
        currency="HKD",
        adjustment_method="原始工作簿字段；未执行实时行情调整",
        path=BACKEND_ROOT / "skills" / "sentiment_scorer" / "references" / "IPO_data.xlsx",
    ),
    DatasetDefinition(
        dataset_id="cashflow-risk-thresholds",
        version="1.0.0",
        as_of_date="2026-01-01",
        coverage_period="not applicable",
        source="repository static methodology asset",
        field_definition="现金流风险指标与阈值定义",
        currency=None,
        adjustment_method=None,
        path=BACKEND_ROOT / "skills" / "cashflow_analyzer" / "references" / "Risk.xlsx",
    ),
)


def dataset_snapshot() -> dict[str, dict[str, str | None]]:
    return {
        definition.dataset_id: definition.snapshot()
        for definition in DATASETS
        if definition.path.is_file()
    }


@lru_cache(maxsize=1)
def _subscription_rows() -> tuple[dict[str, Any], ...]:
    """Read the canonical workbook once; Skill documents never select files."""

    from openpyxl import load_workbook

    definition = next(
        item for item in DATASETS if item.dataset_id == "hk-ipo-subscription-history"
    )
    workbook = load_workbook(definition.path, read_only=True, data_only=True)
    sheet = workbook["IPO数据"]
    rows = sheet.iter_rows(values_only=True)
    headers = tuple(next(rows))
    records_list: list[dict[str, Any]] = []
    for values in rows:
        record: dict[str, Any] = {}
        for header, value in zip(headers, values):
            if header is not None:
                record.setdefault(str(header), value)
        records_list.append(record)
    workbook.close()
    return tuple(records_list)


def lookup_subscription_history(
    stock_code: str,
    *,
    data_cutoff_date: str | None = None,
) -> dict[str, Any] | None:
    """Return a JSON-safe subscription record.

    Passing ``data_cutoff_date`` admits the record only when its announcement
    date is known and on or before that cutoff. Omitting the cutoff returns the
    raw record and does not mark it as pre-listing evidence.
    """

    from riskagent.research.core import eligible_pre_cutoff

    normalized = stock_code.strip().upper()
    if not normalized.endswith(".HK"):
        normalized = f"{normalized}.HK"
    for row in _subscription_rows():
        if str(row.get("代码") or "").strip().upper() != normalized:
            continue
        selected = {
            "stock_code": normalized,
            "company_name": row.get("名称"),
            "announcement_date": row.get("公告日期"),
            "applicant_count": row.get("申购人数"),
            "subscription_shares_10k": row.get("申购股份数(万股)"),
            "subscription_ratio": row.get("认购倍数"),
            "allotment_rate_percent": row.get("中签率(%)"),
            "first_lot_success_rate_percent": row.get("申购一手中签率(%)"),
        }
        record = {key: _json_value(value) for key, value in selected.items()}
        if data_cutoff_date is None:
            return record
        if not eligible_pre_cutoff(record.get("announcement_date"), data_cutoff_date):
            return None
        return {**record, "eligible_pre_cutoff": True, "attribution": "pre_listing"}
    return None


def normalize_valuation_basis(value: Any) -> str | None:
    """Map a valuation label onto ``issue`` or ``first_day``."""

    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"issue", "发行", "发行估值", "招股价", "发行价"} or "发行" in text:
        return "issue"
    if (
        text in {"first_day", "first-day", "post_listing", "上市首日", "上市首日估值口径"}
        or "首日" in text
        or "上市后" in text
    ):
        return "first_day"
    return None


def peer_as_of_date(peer: dict[str, Any], basis: str) -> Any:
    """Date that must be known before the peer can enter a pre-cutoff set.

    First-day and post-listing multiples are dated by the listing or first-day
    date. The announcement date is not a substitute, because the first-day
    price is not known at announcement.
    """

    if basis == "issue":
        return peer.get("as_of_date") or peer.get("announcement_date")
    if basis == "first_day":
        return peer.get("as_of_date") or peer.get("listing_date") or peer.get("first_day_date")
    return None


def select_valuation_peers(
    peers: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    *,
    industry: str,
    data_cutoff_date: str | None,
    basis: str,
    exclude_stock_code: str | None = None,
    limit: int = 10,
    match_industry: bool = True,
) -> tuple[dict[str, Any], ...]:
    """Keep same-basis peers whose valuation date is known and not after cutoff."""

    from riskagent.research.core import eligible_pre_cutoff

    normalized_basis = normalize_valuation_basis(basis) or basis
    wanted_industry = industry.strip()
    excluded = (exclude_stock_code or "").strip().upper()
    if excluded and not excluded.endswith(".HK"):
        excluded = f"{excluded}.HK"
    selected: list[dict[str, Any]] = []
    for peer in peers:
        if not isinstance(peer, dict):
            continue
        code = str(peer.get("stock_code") or "").strip().upper()
        if not code or code == excluded:
            continue
        if match_industry and str(peer.get("industry") or "").strip() != wanted_industry:
            continue
        peer_basis = normalize_valuation_basis(peer.get("valuation_basis"))
        if peer_basis != normalized_basis:
            continue
        as_of = peer_as_of_date(peer, normalized_basis)
        if not eligible_pre_cutoff(as_of, data_cutoff_date):
            continue
        selected.append(
            {
                **peer,
                "valuation_basis": normalized_basis,
                "as_of_date": as_of if isinstance(as_of, str) else _json_value(as_of),
                "eligible_pre_cutoff": True,
                "attribution": "pre_listing" if normalized_basis == "issue" else "post_listing",
            }
        )
    selected.sort(key=lambda item: str(item.get("as_of_date") or ""))
    if limit > 0:
        selected = selected[-limit:]
    return tuple(selected)


def _header_index(headers: tuple[str, ...], names: tuple[str, ...]) -> int | None:
    for index, header in enumerate(headers):
        if any(name in header for name in names):
            return index
    return None


def _positive_multiple(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return _json_value(value)
    return None


@lru_cache(maxsize=1)
def _valuation_peer_rows() -> tuple[dict[str, Any], ...]:
    """Read workbook peers once, keeping issue and first-day bases apart."""

    from openpyxl import load_workbook

    definition = next(
        item for item in DATASETS if item.dataset_id == "hk-ipo-subscription-history"
    )
    workbook = load_workbook(definition.path, read_only=True, data_only=True)
    rows = workbook["IPO数据"].iter_rows(values_only=True)
    header_top = tuple(next(rows))
    header_sub = tuple(next(rows))
    filled: list[str] = []
    last = ""
    width = max(len(header_top), len(header_sub))
    for index in range(width):
        top = header_top[index] if index < len(header_top) else None
        sub = header_sub[index] if index < len(header_sub) else None
        if top not in (None, ""):
            last = str(top).strip()
        sub_text = "" if sub in (None, "") else str(sub).strip()
        filled.append(f"{last} {sub_text}".strip())
    headers = tuple(filled)
    listing_idx = _header_index(headers, ("上市日期", "挂牌日期", "首日日期"))
    issue_pe_idx = _header_index(headers, ("发行市盈率", "首发市盈率"))
    issue_pb_idx = _header_index(headers, ("发行市净率", "首发市净率"))
    records: list[dict[str, Any]] = []
    for values in rows:
        code = str(values[1] or "").strip().upper()
        industry = str(values[18] or "").strip()
        if not code or not industry:
            continue
        announcement = _json_value(values[3])
        listing = _json_value(values[listing_idx]) if listing_idx is not None and listing_idx < len(values) else None
        base = {
            "stock_code": code,
            "company_name": values[2],
            "industry": industry,
            "announcement_date": announcement,
            "listing_date": listing,
            "currency": "HKD",
        }
        issue_pe = _positive_multiple(values[issue_pe_idx]) if issue_pe_idx is not None and issue_pe_idx < len(values) else None
        issue_pb = _positive_multiple(values[issue_pb_idx]) if issue_pb_idx is not None and issue_pb_idx < len(values) else None
        if issue_pe is not None or issue_pb is not None:
            records.append(
                {
                    **base,
                    "pe": issue_pe,
                    "pb": issue_pb,
                    "valuation_basis": "issue",
                    "as_of_date": announcement,
                }
            )
        first_pe = _positive_multiple(values[30]) if len(values) > 30 else None
        first_pb = _positive_multiple(values[31]) if len(values) > 31 else None
        if first_pe is not None or first_pb is not None:
            records.append(
                {
                    **base,
                    "pe": first_pe,
                    "pb": first_pb,
                    "valuation_basis": "first_day",
                    "as_of_date": listing,
                }
            )
    workbook.close()
    return tuple(records)


def lookup_valuation_peers(
    industry: str,
    *,
    exclude_stock_code: str | None = None,
    data_cutoff_date: str | None = None,
    basis: str = "issue",
    limit: int = 10,
) -> tuple[dict[str, Any], ...]:
    """Select same-basis IPO peers that are eligible at ``data_cutoff_date``.

    The default basis is the issue valuation. First-day multiples stay on the
    ``first_day`` basis and are not returned as issue comps.
    """

    return select_valuation_peers(
        _valuation_peer_rows(),
        industry=industry,
        data_cutoff_date=data_cutoff_date,
        basis=basis,
        exclude_stock_code=exclude_stock_code,
        limit=limit,
    )


def _json_value(value: Any) -> Any:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value
