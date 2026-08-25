"""Contract tests for IPO outcome label generation fixes.

Covers the three field-verified defects from the 2026-08-17 live assessment:
1. strict listing-date equality silently skipping every label;
2. price-file codes missing from the security master being dropped entirely;
3. no skip diagnostics anywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from riskagent.backtesting.service import (
    LISTING_DATE_TOLERANCE_DAYS,
    BacktestService,
    MarketImportRequest,
)
from riskagent.database import Base
from riskagent.models import IpoOutcomeLabelRow, MarketDatasetSnapshotRow, SecurityMasterRow


@pytest.fixture
def service():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return BacktestService(session_factory=sessionmaker(bind=engine, expire_on_commit=False), initialize=False)


def _import(service: BacktestService, tmp_path: Path, *, security_csv: str, prices_csv: str) -> dict:
    security = tmp_path / "security.csv"
    company = tmp_path / "company.csv"
    prices = tmp_path / "prices.csv"
    security.write_text(security_csv, encoding="utf-8")
    company.write_text("S_INFO_COMPCODE,S_INFO_COMPNAME\nCOMP1,测试公司\n", encoding="utf-8")
    prices.write_text(prices_csv, encoding="utf-8")
    return service.import_market_data(
        MarketImportRequest(security_csv=str(security), company_csv=str(company), prices_csv=str(prices))
    )


def test_label_created_with_small_listing_date_offset(service, tmp_path):
    """First trading day within the tolerance window still yields labels."""
    imported = _import(
        service,
        tmp_path,
        security_csv=(
            "S_INFO_WINDCODE,S_INFO_COMPCODE,S_INFO_NAME,S_INFO_LISTDATE,S_INFO_LISTPRICE\n"
            "0001.HK,COMP1,容忍度测试,20250102,10\n"
        ),
        prices_csv=(
            "S_INFO_WINDCODE,TRADE_DT,S_DQ_LOW,S_DQ_CLOSE,S_DQ_OPEN\n"
            "0001.HK,20250103,9.5,10.5,10\n"
            "0001.HK,20250106,9,9.8,10\n"
            "0001.HK,20250107,8.9,9.2,9.5\n"
            "0001.HK,20250108,9.3,9.6,9.4\n"
            "0001.HK,20250109,9.4,9.7,9.5\n"
            "0001.HK,20250110,9.2,9.5,9.4\n"
        ),
    )
    assert imported["labels_created"] > 0
    with service.session_factory() as session:
        horizons = session.scalars(
            select(IpoOutcomeLabelRow.horizon).where(
                IpoOutcomeLabelRow.snapshot_id == imported["snapshot_id"],
                IpoOutcomeLabelRow.wind_code == "0001.HK",
            )
        ).all()
    assert 5 in horizons


def test_truncated_history_is_skipped_and_counted(service, tmp_path):
    """A price window starting far after the listing date must not become a label."""
    imported = _import(
        service,
        tmp_path,
        security_csv=(
            "S_INFO_WINDCODE,S_INFO_COMPCODE,S_INFO_NAME,S_INFO_LISTDATE,S_INFO_LISTPRICE\n"
            "0002.HK,COMP1,历史截断测试,20180801,10\n"
        ),
        prices_csv=(
            "S_INFO_WINDCODE,TRADE_DT,S_DQ_LOW,S_DQ_CLOSE,S_DQ_OPEN\n"
            "0002.HK,20250102,9.5,10.5,10\n"
            "0002.HK,20250103,9,9.8,10\n"
            "0002.HK,20250106,8.9,9.2,9.5\n"
            "0002.HK,20250107,9.3,9.6,9.4\n"
            "0002.HK,20250108,9.4,9.7,9.5\n"
            "0002.HK,20250109,9.2,9.5,9.4\n"
        ),
    )
    with service.session_factory() as session:
        labels = session.scalars(
            select(IpoOutcomeLabelRow).where(
                IpoOutcomeLabelRow.snapshot_id == imported["snapshot_id"],
                IpoOutcomeLabelRow.wind_code == "0002.HK",
            )
        ).all()
        stats = session.get(MarketDatasetSnapshotRow, imported["snapshot_id"]).coverage["label_stats"]
    assert labels == []
    assert stats["date_mismatch"] == 1


def test_price_only_code_listed_inside_window_is_inferred(service, tmp_path):
    """Codes absent from the master but trading from mid-window are inferred as IPOs."""
    imported = _import(
        service,
        tmp_path,
        security_csv=(
            "S_INFO_WINDCODE,S_INFO_COMPCODE,S_INFO_NAME,S_INFO_LISTDATE,S_INFO_LISTPRICE\n"
            "0001.HK,COMP1,窗口起点老股,20250102,10\n"
        ),
        prices_csv=(
            "S_INFO_WINDCODE,TRADE_DT,S_DQ_LOW,S_DQ_CLOSE,S_DQ_OPEN\n"
            "0001.HK,20250102,9.5,10.5,10\n"
            "0001.HK,20250103,9,9.8,10\n"
            "9999.HK,20250310,8,8.5,9\n"
            "9999.HK,20250311,7.5,8.2,8.4\n"
            "9999.HK,20250312,7.2,8,8.1\n"
            "9999.HK,20250313,7,7.9,7.8\n"
            "9999.HK,20250314,6.9,7.8,7.7\n"
            "9999.HK,20250317,6.8,7.7,7.6\n"
        ),
    )
    counts = imported["row_counts"]
    assert counts["securities_inferred"] == 1
    with service.session_factory() as session:
        inferred = session.scalar(
            select(SecurityMasterRow).where(
                SecurityMasterRow.snapshot_id == imported["snapshot_id"],
                SecurityMasterRow.wind_code == "9999.HK",
            )
        )
        labels = session.scalars(
            select(IpoOutcomeLabelRow).where(
                IpoOutcomeLabelRow.snapshot_id == imported["snapshot_id"],
                IpoOutcomeLabelRow.wind_code == "9999.HK",
            )
        ).all()
    assert inferred.source == "inferred_from_prices"
    assert inferred.listing_date == "20250310"
    assert inferred.listing_price == 9
    assert 5 in [item.horizon for item in labels]


def test_price_only_code_at_window_start_is_not_inferred(service, tmp_path):
    """A code first trading at window start cannot be told apart from truncation."""
    imported = _import(
        service,
        tmp_path,
        security_csv=(
            "S_INFO_WINDCODE,S_INFO_COMPCODE,S_INFO_NAME,S_INFO_LISTDATE,S_INFO_LISTPRICE\n"
            "0001.HK,COMP1,窗口起点老股,20250102,10\n"
        ),
        prices_csv=(
            "S_INFO_WINDCODE,TRADE_DT,S_DQ_LOW,S_DQ_CLOSE,S_DQ_OPEN\n"
            "0001.HK,20250102,9.5,10.5,10\n"
            "7777.HK,20250102,9,9.5,9.2\n"
            "7777.HK,20250103,8.8,9.3,9.1\n"
        ),
    )
    assert imported["row_counts"]["securities_inferred"] == 0
    with service.session_factory() as session:
        assert (
            session.scalar(
                select(SecurityMasterRow).where(
                    SecurityMasterRow.snapshot_id == imported["snapshot_id"],
                    SecurityMasterRow.wind_code == "7777.HK",
                )
            )
            is None
        )


def test_tolerance_constant_matches_assessment_fix():
    assert LISTING_DATE_TOLERANCE_DAYS == 3
