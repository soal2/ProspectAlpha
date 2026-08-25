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


def lookup_subscription_history(stock_code: str) -> dict[str, Any] | None:
    """Return a JSON-safe, versioned historical subscription record by HK code."""

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
        return {key: _json_value(value) for key, value in selected.items()}
    return None


@lru_cache(maxsize=32)
def lookup_valuation_peers(
    industry: str,
    *,
    exclude_stock_code: str | None = None,
    limit: int = 10,
) -> tuple[dict[str, Any], ...]:
    """Select historical IPO peers from the same immutable workbook."""

    from openpyxl import load_workbook

    definition = next(
        item for item in DATASETS if item.dataset_id == "hk-ipo-subscription-history"
    )
    workbook = load_workbook(definition.path, read_only=True, data_only=True)
    rows = workbook["IPO数据"].iter_rows(values_only=True)
    next(rows)  # top-level headers
    next(rows)  # grouped subheaders
    excluded = (exclude_stock_code or "").upper()
    if excluded and not excluded.endswith(".HK"):
        excluded = f"{excluded}.HK"
    matches: list[dict[str, Any]] = []
    for values in rows:
        code = str(values[1] or "").strip().upper()
        if not code or code == excluded or str(values[18] or "").strip() != industry:
            continue
        pe = values[30]
        pb = values[31]
        if not any(isinstance(value, (int, float)) and value > 0 for value in (pe, pb)):
            continue
        matches.append(
            {
                "stock_code": code,
                "company_name": values[2],
                "industry": values[18],
                "announcement_date": _json_value(values[3]),
                "pe": _json_value(pe) if isinstance(pe, (int, float)) and pe > 0 else None,
                "pb": _json_value(pb) if isinstance(pb, (int, float)) and pb > 0 else None,
                "currency": "HKD",
                "valuation_basis": "上市首日估值口径",
            }
        )
    workbook.close()
    matches.sort(key=lambda item: str(item.get("announcement_date") or ""), reverse=True)
    return tuple(matches[:limit])


def _json_value(value: Any) -> Any:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value
