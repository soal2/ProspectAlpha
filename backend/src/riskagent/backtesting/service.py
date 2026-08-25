"""Versioned IPO outcome labels, scorecard and backtest services."""

from __future__ import annotations

import asyncio
import csv
import codecs
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from io import TextIOWrapper
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from riskagent.common.risk_levels import RiskLevel as _RISK_LEVELS_ENUM, score_to_risk_level
from riskagent.database import Base, SessionLocal, init_db
from riskagent.models import (
    BacktestCandidateSampleRow,
    BacktestCandidateSetRow,
    BacktestJobRow,
    BacktestRunRow,
    BacktestSampleRow,
    CompanyInfoRow,
    DailyPriceRow,
    IpoOutcomeLabelRow,
    MarketDatasetSnapshotRow,
    RiskScoreSnapshotRow,
    SecurityMasterRow,
)
from riskagent.research import HypothesisResearchModule
from riskagent.reporting import render_backtest_audit_html

SCORECARD_VERSION = "ipo-risk-v2-hidden-first"
WEIGHT_PROFILE = {"D1": 15.0, "D2": 30.0, "D3": 35.0, "EXT": 20.0}
SEVERITY_FACTORS = {"high": 1.0, "medium": 0.67, "low": 0.33}
EVIDENCE_FACTORS = {"direct": 1.0, "inferential": 0.75, "lead": 0.5, "contradicted": 0.0}
HORIZONS = (1, 5, 20, 60)
LISTING_DATE_TOLERANCE_DAYS = 3
_RISK_LEVELS = tuple(_RISK_LEVELS_ENUM)

logger = logging.getLogger(__name__)


class MarketImportRequest(BaseModel):
    security_csv: str
    company_csv: str
    prices_csv: str


class BacktestCreate(BaseModel):
    snapshot_id: str
    wind_code: str
    name: str = "港股 IPO T+5 风险预警回测"
    test_start_date: str | None = None
    test_end_date: str | None = None


class CandidateDecision(BaseModel):
    wind_code: str
    score_id: str | None = None
    include: bool
    exclusion_reason: str | None = None


class CandidateConfirmation(BaseModel):
    decisions: list[CandidateDecision]


class BacktestSummary(BaseModel):
    id: str
    name: str
    model: str = "可解释规则评分卡"
    modelVersion: str = SCORECARD_VERSION
    samples: int
    auc: float
    ks: float
    lift10: float
    recall: float
    createdAt: str
    status: str
    author: str = "系统"
    candidateSetId: str | None = None
    jobId: str | None = None


class BacktestJob(BaseModel):
    id: str
    backtestId: str
    kind: str
    status: str
    progress: int
    stage: str
    error: str | None = None


class MarketSnapshotOption(BaseModel):
    snapshotId: str
    createdAt: str
    priceStart: str | None = None
    priceEnd: str | None = None
    securities: int = 0
    priceRows: int = 0
    t5Labels: int = 0
    eligibleSamples: int = 0
    canCreateBacktest: bool
    unavailableReason: str | None = None
    status: str = "draft"
    qualityReport: dict = Field(default_factory=dict)


class BacktestCandidatePreview(BaseModel):
    windCode: str
    companyName: str | None = None
    listingDate: str
    scoreId: str
    sourceRunId: str
    analysisAsOfDate: str
    score: float
    riskLevel: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


EVALUATION_PROTOCOL = {
    "id": "ipo-t5-drawdown-v1",
    "horizon": 5,
    "positive": "window_min_low_vs_offer_price_lte_-10pct",
    "alert_capacity": "top_20pct",
    "scorecard_version": SCORECARD_VERSION,
}


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _csv_encoding(path: Path) -> str:
    for encoding in ("utf-8-sig", "gb18030", "gbk"):
        try:
            decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    decoder.decode(chunk)
            decoder.decode(b"", final=True)
            return encoding
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法识别 CSV 编码: {path}")


def _number(value: Any, *, required: bool = False) -> float | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise ValueError("缺少必填数值")
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"无效数值: {text}") from exc


def _code(value: str | None) -> str:
    code = str(value or "").strip().upper()
    if not code:
        raise ValueError("缺少 Wind 股票代码")
    return code if code.endswith(".HK") else f"{code.zfill(4)}.HK"


def _date_key(value: str | None) -> str:
    return re.sub(r"[^0-9]", "", str(value or ""))[:8]


def _score_bucket(item: dict[str, Any]) -> str:
    dimension = str(item.get("dimension_code") or "").upper()
    origin = str(item.get("origin") or "rule")
    if dimension == "EXT":
        return "EXT"
    if dimension == "D3" or origin == "text_signal":
        return "D3"
    if dimension == "D1" or any(term in dimension for term in ("FIN", "CASHFLOW", "ACCOUNT")):
        return "D1"
    return "D2"


def calculate_hidden_first_score(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure v2 scorecard calculation; confidence is intentionally ignored."""

    raw: dict[str, list[float]] = {key: [] for key in WEIGHT_PROFILE}
    for item in findings:
        bucket = _score_bucket(item)
        if str(item.get("hypothesis_status") or "") == "refuted":
            factor = 0.0
        elif bucket == "EXT":
            status = str(item.get("hypothesis_status") or "")
            factor = (
                1.0
                if status == "corroborated" and not item.get("counter_evidence_ids")
                else 0.5
                if status == "disputed"
                or (item.get("external_evidence_ids") and item.get("counter_evidence_ids"))
                else 0.0
            )
        else:
            factor = SEVERITY_FACTORS.get(
                str(item.get("severity") or "").lower(), 0.0
            ) * EVIDENCE_FACTORS.get(
                str(item.get("evidence_grade") or "direct"), 0.0
            )
        raw[bucket].append(factor)
    contributions = {
        bucket: round(
            weight * min(1.0, sum(sorted(raw[bucket], reverse=True)[:3]) / 3),
            2,
        )
        for bucket, weight in WEIGHT_PROFILE.items()
    }
    baseline_d1 = round(
        100 * min(1.0, sum(sorted(raw["D1"], reverse=True)[:3]) / 3),
        2,
    )
    total = round(sum(contributions.values()), 2)
    limitations = (
        ["未取得上市前独立外部共振证据；EXT 贡献为 0，不按中性值填补。"]
        if contributions["EXT"] == 0
        else []
    )
    return {
        "score": total,
        "risk_level": score_to_risk_level(total).value,
        "breakdown": {
            **contributions,
            "baseline_d1": baseline_d1,
            "without_d2": round(total - contributions["D2"], 2),
            "without_d3": round(total - contributions["D3"], 2),
            "without_ext": round(total - contributions["EXT"], 2),
        },
        "limitations": limitations,
        "scorecard_version": SCORECARD_VERSION,
        "weight_profile": WEIGHT_PROFILE,
    }


def _date_gap(start: str, end: str) -> int | None:
    """Calendar-day gap between two YYYYMMDD strings; None when unparseable."""
    try:
        begin = datetime.strptime(start, "%Y%m%d").date()
        finish = datetime.strptime(end, "%Y%m%d").date()
    except ValueError:
        return None
    return (finish - begin).days


def _hashes(paths: list[Path]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in paths:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        result[path.name] = digest.hexdigest()
    return result


def _iter_csv(path: Path):
    """Yield strict-decoded records without loading a multi-million-row CSV at once."""
    with path.open("rb") as raw:
        with TextIOWrapper(raw, encoding=_csv_encoding(path), newline="") as text:
            reader = csv.DictReader(text)
            if not reader.fieldnames:
                raise ValueError(f"CSV 缺少表头: {path}")
            yielded = False
            for row in reader:
                yielded = True
                yield row
            if not yielded:
                raise ValueError(f"CSV 没有数据行: {path}")


class BacktestService:
    def __init__(self, session_factory=SessionLocal, *, initialize: bool = True) -> None:
        self.session_factory = session_factory
        if initialize:
            bind = getattr(session_factory, "kw", {}).get("bind")
            if bind is None:
                init_db()
            else:
                Base.metadata.create_all(bind=bind)

    def import_market_data(self, request: MarketImportRequest) -> dict[str, Any]:
        paths = [Path(request.security_csv), Path(request.company_csv), Path(request.prices_csv)]
        if any(not path.is_file() for path in paths):
            raise ValueError("市场数据 CSV 文件不存在")
        hashes = _hashes(paths)
        snapshot_id = f"mkt_{hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:16]}"
        security_rows, company_rows = [], []
        seen_security, seen_company = set(), set()
        for row in _iter_csv(paths[0]):
            code = _code(row.get("S_INFO_WINDCODE"))
            if code in seen_security:
                raise ValueError(f"证券主数据重复主键: {code}")
            seen_security.add(code)
            security_rows.append({"id": f"{snapshot_id}:{code}", "snapshot_id": snapshot_id, "wind_code": code, "company_code": row.get("S_INFO_COMPCODE") or None, "name": row.get("S_INFO_NAME") or row.get("S_INFO_NAME_ENG") or None, "listing_date": row.get("S_INFO_LISTDATE") or None, "listing_price": _number(row.get("S_INFO_LISTPRICE"))})
        for row in _iter_csv(paths[1]):
            company_code = str(row.get("S_INFO_COMPCODE") or "").strip()
            if not company_code:
                continue
            if company_code in seen_company:
                raise ValueError(f"公司主数据重复主键: {company_code}")
            seen_company.add(company_code)
            company_rows.append({"id": f"{snapshot_id}:{company_code}", "snapshot_id": snapshot_id, "company_code": company_code, "name": row.get("S_INFO_COMPNAME") or None, "english_name": row.get("COMP_NAME_ENG") or None, "business_scope": row.get("BUSINESSSCOPE") or None})
        with self.session_factory() as session:
            existing = session.get(MarketDatasetSnapshotRow, snapshot_id)
            if existing:
                return {"snapshot_id": snapshot_id, "reused": True, "row_counts": existing.row_counts}
            try:
                snapshot = MarketDatasetSnapshotRow(
                    snapshot_id=snapshot_id,
                    file_hashes=hashes,
                    source_paths={"security": str(paths[0]), "company": str(paths[1]), "prices": str(paths[2])},
                    coverage={},
                    row_counts={},
                    created_at=_now(),
                )
                session.add(snapshot)
                session.flush()
                session.bulk_insert_mappings(SecurityMasterRow, security_rows)
                session.bulk_insert_mappings(CompanyInfoRow, company_rows)
                price_rows: list[dict[str, Any]] = []
                price_count = 0
                price_start: str | None = None
                price_end: str | None = None
                first_price_by_code: dict[str, dict[str, Any]] = {}
                for row in _iter_csv(paths[2]):
                    code = _code(row.get("S_INFO_WINDCODE"))
                    trade_date = str(row.get("TRADE_DT") or "").strip()
                    if not trade_date:
                        raise ValueError(f"缺少交易日期: {code}")
                    mapping = {"id": f"{snapshot_id}:{code}:{trade_date}", "snapshot_id": snapshot_id, "wind_code": code, "trade_date": trade_date, "low": _number(row.get("S_DQ_LOW"), required=True), "close": _number(row.get("S_DQ_CLOSE"), required=True), "open": _number(row.get("S_DQ_OPEN")), "high": _number(row.get("S_DQ_HIGH")), "volume": _number(row.get("S_DQ_VOLUME"))}
                    price_rows.append(mapping)
                    price_count += 1
                    price_start = trade_date if price_start is None or trade_date < price_start else price_start
                    price_end = trade_date if price_end is None or trade_date > price_end else price_end
                    current_first = first_price_by_code.get(code)
                    if current_first is None or trade_date < current_first["trade_date"]:
                        first_price_by_code[code] = mapping
                    if len(price_rows) == 5000:
                        session.bulk_insert_mappings(DailyPriceRow, price_rows)
                        session.flush()
                        price_rows.clear()
                if price_rows:
                    session.bulk_insert_mappings(DailyPriceRow, price_rows)
                if not price_count:
                    raise ValueError(f"CSV 没有数据行: {paths[2]}")
                # Codes present in the price file but absent from the security master
                # are (mostly) listings that occurred inside the price window. Their
                # listing date/price is inferred from the first traded day; codes whose
                # history starts at the very beginning of the window cannot be
                # distinguished from truncated histories and are therefore not inferred.
                inferred_rows: list[dict[str, Any]] = []
                for code, first in sorted(first_price_by_code.items()):
                    if code in seen_security:
                        continue
                    # Wind marks derivatives (warrants/CBBC) with "!" in the code,
                    # e.g. 2917!20.HK. They are not equities and must never be
                    # inferred as IPO samples.
                    if "!" in code:
                        continue
                    if price_start and first["trade_date"] <= price_start:
                        continue
                    listing_price = first.get("open") or first.get("close")
                    if not listing_price or listing_price <= 0:
                        continue
                    inferred_rows.append({"id": f"{snapshot_id}:{code}", "snapshot_id": snapshot_id, "wind_code": code, "company_code": None, "name": None, "listing_date": first["trade_date"], "listing_price": listing_price, "source": "inferred_from_prices"})
                if inferred_rows:
                    session.bulk_insert_mappings(SecurityMasterRow, inferred_rows)
                    session.flush()
                snapshot.coverage = {"price_start": price_start, "price_end": price_end}
                snapshot.row_counts = {"securities": len(security_rows) + len(inferred_rows), "securities_master": len(security_rows), "securities_inferred": len(inferred_rows), "companies": len(company_rows), "prices": price_count}
                snapshot.quality_report = {
                    "status": "passed",
                    "checks": {
                        "security_rows": len(security_rows),
                        "company_rows": len(company_rows),
                        "price_rows": price_count,
                        "inferred_securities": len(inferred_rows),
                    },
                }
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ValueError("导入被拒绝：行情数据存在重复主键或违反完整性约束") from exc
            except Exception:
                session.rollback()
                raise
        labels_created = self.generate_labels(snapshot_id)
        return {"snapshot_id": snapshot_id, "reused": False, "row_counts": {"securities": len(security_rows) + len(inferred_rows), "securities_master": len(security_rows), "securities_inferred": len(inferred_rows), "companies": len(company_rows), "prices": price_count}, "labels_created": labels_created}

    def generate_labels(self, snapshot_id: str) -> int:
        created = 0
        stats = {"securities_scanned": 0, "no_listing_data": 0, "no_price_history": 0, "date_mismatch": 0, "labels_created": 0}
        with self.session_factory() as session:
            snapshot = session.get(MarketDatasetSnapshotRow, snapshot_id)
            securities = session.scalars(select(SecurityMasterRow).where(SecurityMasterRow.snapshot_id == snapshot_id)).all()
            for security in securities:
                stats["securities_scanned"] += 1
                if not security.listing_date or not security.listing_price or security.listing_price <= 0:
                    stats["no_listing_data"] += 1
                    continue
                prices = session.scalars(select(DailyPriceRow).where(DailyPriceRow.snapshot_id == snapshot_id, DailyPriceRow.wind_code == security.wind_code, DailyPriceRow.trade_date >= security.listing_date).order_by(DailyPriceRow.trade_date)).all()
                if not prices:
                    stats["no_price_history"] += 1
                    continue
                # Tolerate non-trading listing dates and small master-data errors, but
                # never silently treat a later, truncated history as the IPO day.
                gap = _date_gap(security.listing_date, prices[0].trade_date)
                if gap is None or gap > LISTING_DATE_TOLERANCE_DAYS:
                    stats["date_mismatch"] += 1
                    continue
                for horizon in HORIZONS:
                    if len(prices) < horizon:
                        continue
                    window = prices[:horizon]
                    end_close, min_low = window[-1].close, min(item.low for item in window)
                    return_pct = (end_close / security.listing_price - 1) * 100
                    drawdown = (min_low / security.listing_price - 1) * 100
                    ident = f"{snapshot_id}:{security.wind_code}:{horizon}"
                    if session.get(IpoOutcomeLabelRow, ident):
                        continue
                    session.add(IpoOutcomeLabelRow(id=ident, snapshot_id=snapshot_id, wind_code=security.wind_code, horizon=horizon, offer_price=security.listing_price, end_close=end_close, min_low=min_low, return_pct=return_pct, max_drawdown_pct=drawdown, significant_decline=drawdown <= -10, breaking=any(item.close < security.listing_price for item in window)))
                    created += 1
            stats["labels_created"] = created
            if stats["date_mismatch"]:
                logger.warning(
                    "标签生成跳过 %d 只证券：首个交易日距主档上市日超过 %d 天（snapshot=%s）",
                    stats["date_mismatch"], LISTING_DATE_TOLERANCE_DAYS, snapshot_id,
                )
            if snapshot is not None:
                snapshot.coverage = {**(snapshot.coverage or {}), "label_stats": stats}
            session.commit()
        return created

    def score(
        self,
        *,
        run_id: str,
        wind_code: str,
        findings: list[dict[str, Any]],
        industry_profile: dict[str, Any] | None = None,
        text_signal_snapshot: dict[str, Any] | None = None,
        research_snapshot_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        result = calculate_hidden_first_score(findings)
        with self.session_factory() as session:
            session.merge(
                RiskScoreSnapshotRow(
                    score_id=f"score_{run_id}",
                    run_id=run_id,
                    wind_code=_code(wind_code),
                    scorecard_version=SCORECARD_VERSION,
                    total_score=result["score"],
                    risk_level=result["risk_level"],
                    breakdown=result["breakdown"],
                    limitations=result["limitations"],
                    analysis_as_of_date="",
                    input_hash="",
                    evidence_hash="",
                    finding_snapshot=findings,
                    industry_profile=industry_profile or {},
                    text_signal_snapshot=text_signal_snapshot or {},
                    weight_profile=WEIGHT_PROFILE,
                    research_snapshot_ids=research_snapshot_ids or [],
                    created_at=_now(),
                )
            )
            session.commit()
        return result

    def score_for_analysis_run(self, run: Any) -> dict[str, Any]:
        """Freeze a report's explainable score using only its frozen inputs/findings."""
        stock_code = (getattr(run, "input_summary", None) or {}).get("stock_code")
        if not stock_code:
            raise ValueError("分析任务缺少已确认的港股代码")
        findings = [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in getattr(run, "findings", [])
            if getattr(item, "verification_status", None)
            in {"supported", "weakly_supported"}
        ]
        industry = getattr(run, "industry_profile", None)
        text_signals = getattr(run, "text_signal_snapshot", None)
        artifacts = [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in getattr(run, "research_artifacts", [])
        ]
        research_sources = [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in getattr(run, "research_sources", [])
        ]
        result = self.score(
            run_id=str(getattr(run, "run_id")),
            wind_code=str(stock_code),
            findings=findings,
            industry_profile=industry.model_dump() if industry is not None else {},
            text_signal_snapshot=text_signals.model_dump() if text_signals is not None else {},
            research_snapshot_ids=[
                str(item.get("source_hash") or item.get("artifact_id"))
                for item in artifacts
                if item.get("source_hash") or item.get("artifact_id")
            ]
            + [
                str(item.get("content_hash") or item.get("source_id"))
                for item in research_sources
                if item.get("content_hash") or item.get("source_id")
            ],
        )
        with self.session_factory() as session:
            frozen = session.get(RiskScoreSnapshotRow, f"score_{getattr(run, 'run_id')}")
            if frozen is not None:
                frozen.analysis_as_of_date = str(getattr(run, "data_cutoff_date", ""))
                frozen.input_hash = _stable_hash(getattr(run, "input_summary", {}) or {})
                frozen.evidence_hash = _stable_hash(
                    [item.model_dump() if hasattr(item, "model_dump") else item for item in getattr(run, "evidence", [])]
                )
                session.commit()
        return result

    def create_backtest(self, request: BacktestCreate) -> BacktestSummary:
        with self.session_factory() as session:
            backtest_id = f"bt_{uuid.uuid4().hex[:12]}"
            snapshot = session.get(MarketDatasetSnapshotRow, request.snapshot_id)
            if not snapshot or snapshot.status != "published":
                raise ValueError("只能从已发布且通过质量校验的市场数据版本创建回测")
            candidates = {item.windCode: item for item in self._preview_candidates(session, request.snapshot_id)}
            selected = candidates.get(_code(request.wind_code))
            if not selected:
                raise ValueError("该公司不属于当前数据版本的正式可回测候选集")
            candidate_set_id = f"cs_{uuid.uuid4().hex[:12]}"
            session.add(BacktestRunRow(backtest_id=backtest_id, snapshot_id=request.snapshot_id, name=request.name, test_start_date=request.test_start_date, test_end_date=request.test_end_date, status="ready", metrics={}, protocol=EVALUATION_PROTOCOL, candidate_set_id=candidate_set_id, created_at=_now()))
            session.flush()
            session.add(BacktestCandidateSetRow(candidate_set_id=candidate_set_id, backtest_id=backtest_id, snapshot_id=request.snapshot_id, status="confirmed", created_at=_now()))
            session.flush()
            label = session.get(IpoOutcomeLabelRow, f"{request.snapshot_id}:{selected.windCode}:{EVALUATION_PROTOCOL['horizon']}")
            session.add(BacktestCandidateSampleRow(id=f"{candidate_set_id}:{selected.windCode}", candidate_set_id=candidate_set_id, wind_code=selected.windCode, listing_date=selected.listingDate, label_id=label.id, score_id=selected.scoreId, source_run_id=selected.sourceRunId, inclusion_status="included", exclusion_reason=None))
            session.commit()
            return self._summary(session.get(BacktestRunRow, backtest_id))

    def preview_candidates(self, snapshot_id: str) -> list[BacktestCandidatePreview]:
        with self.session_factory() as session:
            snapshot = session.get(MarketDatasetSnapshotRow, snapshot_id)
            if not snapshot or snapshot.status != "published":
                raise ValueError("只能预览已发布的市场数据版本")
            return self._preview_candidates(session, snapshot_id)

    def _preview_candidates(self, session, snapshot_id: str) -> list[BacktestCandidatePreview]:
        labels = session.scalars(select(IpoOutcomeLabelRow).where(IpoOutcomeLabelRow.snapshot_id == snapshot_id, IpoOutcomeLabelRow.horizon == EVALUATION_PROTOCOL["horizon"])).all()
        securities = {item.wind_code: item for item in session.scalars(select(SecurityMasterRow).where(SecurityMasterRow.snapshot_id == snapshot_id)).all()}
        scores = session.scalars(select(RiskScoreSnapshotRow).where(RiskScoreSnapshotRow.scorecard_version == SCORECARD_VERSION)).all()
        by_code: dict[str, list[RiskScoreSnapshotRow]] = {}
        for score in scores: by_code.setdefault(score.wind_code, []).append(score)
        output = []
        for label in labels:
            security = securities.get(label.wind_code)
            if not security or not security.listing_date: continue
            valid = [item for item in by_code.get(label.wind_code, []) if _date_key(item.analysis_as_of_date) and _date_key(item.analysis_as_of_date) <= _date_key(security.listing_date)]
            if not valid: continue
            valid.sort(key=lambda item: (item.analysis_as_of_date, item.created_at), reverse=True)
            score = valid[0]
            output.append(BacktestCandidatePreview(windCode=label.wind_code, companyName=security.name, listingDate=security.listing_date, scoreId=score.score_id, sourceRunId=score.run_id, analysisAsOfDate=score.analysis_as_of_date, score=score.total_score, riskLevel=score.risk_level))
        return sorted(output, key=lambda item: item.windCode)

    def _new_job(self, session, backtest_id: str, kind: str, stage: str) -> BacktestJobRow:
        job = BacktestJobRow(job_id=f"bj_{uuid.uuid4().hex[:12]}", backtest_id=backtest_id, kind=kind, status="queued", progress=0, stage=stage, created_at=_now())
        session.add(job)
        return job

    def run_job(self, job_id: str) -> None:
        with self.session_factory() as session:
            job = session.get(BacktestJobRow, job_id)
            if not job or job.status not in {"queued", "failed"}:
                return
            job_kind = job.kind
            backtest_id = job.backtest_id
            job.status, job.progress, job.stage, job.error = "running", 5, "正在执行", None
            session.commit()
        try:
            if job_kind == "build_candidates":
                self._build_candidates(backtest_id)
            elif job_kind == "run_backtest":
                self._run_backtest(backtest_id)
            elif job_kind == "reproduce":
                self._run_backtest(backtest_id)
            else:
                raise ValueError(f"未知回测任务类型: {job_kind}")
            with self.session_factory() as session:
                completed = session.get(BacktestJobRow, job_id)
                completed.status, completed.progress, completed.stage, completed.completed_at = "completed", 100, "完成", _now()
                session.commit()
        except Exception as exc:
            with self.session_factory() as session:
                failed = session.get(BacktestJobRow, job_id)
                failed.status, failed.stage, failed.error, failed.completed_at = "failed", "失败", str(exc), _now()
                run = session.get(BacktestRunRow, failed.backtest_id)
                if run: run.status = "failed"
                session.commit()

    def _build_candidates(self, backtest_id: str) -> None:
        with self.session_factory() as session:
            run = session.get(BacktestRunRow, backtest_id)
            candidate_set = session.get(BacktestCandidateSetRow, run.candidate_set_id)
            labels = session.scalars(select(IpoOutcomeLabelRow).where(IpoOutcomeLabelRow.snapshot_id == run.snapshot_id, IpoOutcomeLabelRow.horizon == EVALUATION_PROTOCOL["horizon"])).all()
            securities = {item.wind_code: item for item in session.scalars(select(SecurityMasterRow).where(SecurityMasterRow.snapshot_id == run.snapshot_id)).all()}
            scores = session.scalars(select(RiskScoreSnapshotRow).where(RiskScoreSnapshotRow.scorecard_version == SCORECARD_VERSION)).all()
            by_code: dict[str, list[RiskScoreSnapshotRow]] = {}
            for score in scores:
                by_code.setdefault(score.wind_code, []).append(score)
            for label in labels:
                security = securities.get(label.wind_code)
                if not security or not security.listing_date or not self._in_window(session, run.snapshot_id, label.wind_code, run.test_start_date, run.test_end_date):
                    continue
                valid = [item for item in by_code.get(label.wind_code, []) if _date_key(item.analysis_as_of_date) and _date_key(item.analysis_as_of_date) <= _date_key(security.listing_date)]
                valid.sort(key=lambda item: (item.analysis_as_of_date, item.created_at), reverse=True)
                score = valid[0] if valid else None
                status = "proposed" if score else "excluded"
                reason = None if score else "没有信息截止日不晚于上市日的冻结评分"
                session.add(BacktestCandidateSampleRow(id=f"{candidate_set.candidate_set_id}:{label.wind_code}", candidate_set_id=candidate_set.candidate_set_id, wind_code=label.wind_code, listing_date=security.listing_date, label_id=label.id, score_id=score.score_id if score else None, source_run_id=score.run_id if score else None, inclusion_status=status, exclusion_reason=reason))
            candidate_set.status, run.status = "ready", "candidate_review"
            session.commit()

    def confirm_candidates(self, backtest_id: str, payload: CandidateConfirmation) -> BacktestSummary:
        with self.session_factory() as session:
            run = session.get(BacktestRunRow, backtest_id)
            if not run or run.status != "candidate_review": raise ValueError("当前回测不在候选确认阶段")
            candidate_set = session.get(BacktestCandidateSetRow, run.candidate_set_id)
            rows = {row.wind_code: row for row in session.scalars(select(BacktestCandidateSampleRow).where(BacktestCandidateSampleRow.candidate_set_id == candidate_set.candidate_set_id)).all()}
            for decision in payload.decisions:
                row = rows.get(_code(decision.wind_code))
                if not row: raise ValueError(f"不是当前候选样本: {decision.wind_code}")
                if decision.include:
                    if decision.score_id != row.score_id: raise ValueError("只能确认系统预选且满足时点要求的评分")
                    row.inclusion_status, row.exclusion_reason = "included", None
                else:
                    if not decision.exclusion_reason: raise ValueError("排除样本必须填写原因")
                    row.inclusion_status, row.exclusion_reason = "excluded", decision.exclusion_reason
            if not any(row.inclusion_status == "included" for row in rows.values()):
                raise ValueError("至少确认一个候选样本")
            candidate_set.status, run.status = "confirmed", "ready"
            session.commit()
            return self._summary(run)

    def start_backtest(self, backtest_id: str) -> BacktestJob:
        with self.session_factory() as session:
            run = session.get(BacktestRunRow, backtest_id)
            if not run or run.status != "ready": raise ValueError("请先确认候选样本")
            run.status = "running"
            job = self._new_job(session, backtest_id, "run_backtest", "计算指标")
            session.commit()
            return self._job(job)

    def reproduce(self, backtest_id: str) -> BacktestJob:
        """Run the original immutable sample and protocol snapshot again."""
        with self.session_factory() as session:
            source = session.get(BacktestRunRow, backtest_id)
            if not source or source.status != "completed": raise ValueError("只有已完成的回测可以严格复现")
            candidate_set = session.get(BacktestCandidateSetRow, source.candidate_set_id)
            source_candidates = session.scalars(select(BacktestCandidateSampleRow).where(BacktestCandidateSampleRow.candidate_set_id == candidate_set.candidate_set_id)).all()
            clone_id, clone_set_id = f"bt_{uuid.uuid4().hex[:12]}", f"cs_{uuid.uuid4().hex[:12]}"
            session.add(BacktestRunRow(backtest_id=clone_id, snapshot_id=source.snapshot_id, name=f"{source.name}（严格复现）", test_start_date=source.test_start_date, test_end_date=source.test_end_date, status="running", metrics={}, protocol=source.protocol, candidate_set_id=clone_set_id, created_at=_now()))
            session.add(BacktestCandidateSetRow(candidate_set_id=clone_set_id, backtest_id=clone_id, snapshot_id=source.snapshot_id, status="confirmed", created_at=_now()))
            for item in source_candidates:
                session.add(BacktestCandidateSampleRow(id=f"{clone_set_id}:{item.wind_code}", candidate_set_id=clone_set_id, wind_code=item.wind_code, listing_date=item.listing_date, label_id=item.label_id, score_id=item.score_id, source_run_id=item.source_run_id, inclusion_status=item.inclusion_status, exclusion_reason=item.exclusion_reason))
            job = self._new_job(session, clone_id, "reproduce", "严格复现")
            session.commit()
            return self._job(job)

    def list_backtests(self) -> list[BacktestSummary]:
        with self.session_factory() as session:
            return [self._summary(item) for item in session.scalars(select(BacktestRunRow).order_by(BacktestRunRow.created_at.desc())).all()]

    def get_job(self, job_id: str) -> BacktestJob:
        with self.session_factory() as session:
            job = session.get(BacktestJobRow, job_id)
            if not job: raise KeyError(job_id)
            return self._job(job)

    @staticmethod
    def _job(row: BacktestJobRow) -> BacktestJob:
        return BacktestJob(id=row.job_id, backtestId=row.backtest_id, kind=row.kind, status=row.status, progress=row.progress, stage=row.stage, error=row.error)

    def list_market_snapshots(self) -> list[MarketSnapshotOption]:
        """Return versioned market datasets with server-calculated backtest readiness."""
        with self.session_factory() as session:
            snapshots = session.scalars(
                select(MarketDatasetSnapshotRow).order_by(MarketDatasetSnapshotRow.created_at.desc())
            ).all()
            frozen_codes = select(RiskScoreSnapshotRow.wind_code).distinct()
            options: list[MarketSnapshotOption] = []
            for snapshot in snapshots:
                labels = select(IpoOutcomeLabelRow.wind_code).where(
                    IpoOutcomeLabelRow.snapshot_id == snapshot.snapshot_id,
                    IpoOutcomeLabelRow.horizon == 5,
                )
                label_codes = labels.subquery()
                t5_labels = int(session.scalar(select(func.count()).select_from(label_codes)) or 0)
                eligible = int(
                    session.scalar(
                        select(func.count()).select_from(label_codes).where(
                            label_codes.c.wind_code.in_(frozen_codes)
                        )
                    )
                    or 0
                )
                reason = None
                if not t5_labels:
                    reason = "该版本尚无完整 T+5 行情标签"
                elif not eligible:
                    reason = "T+5 标签公司尚无已冻结的报告风险评分"
                coverage = snapshot.coverage or {}
                counts = snapshot.row_counts or {}
                options.append(
                    MarketSnapshotOption(
                        snapshotId=snapshot.snapshot_id,
                        createdAt=snapshot.created_at,
                        priceStart=coverage.get("price_start"),
                        priceEnd=coverage.get("price_end"),
                        securities=int(counts.get("securities", 0)),
                        priceRows=int(counts.get("prices", 0)),
                        t5Labels=t5_labels,
                        eligibleSamples=eligible,
                        canCreateBacktest=snapshot.status == "published" and eligible > 0,
                        unavailableReason=reason,
                        status=snapshot.status,
                        qualityReport=snapshot.quality_report or {},
                    )
                )
            return options

    def publish_market_snapshot(self, snapshot_id: str) -> MarketSnapshotOption:
        with self.session_factory() as session:
            snapshot = session.get(MarketDatasetSnapshotRow, snapshot_id)
            if not snapshot: raise KeyError(snapshot_id)
            labels = int(session.scalar(select(func.count()).select_from(IpoOutcomeLabelRow).where(IpoOutcomeLabelRow.snapshot_id == snapshot_id, IpoOutcomeLabelRow.horizon == EVALUATION_PROTOCOL["horizon"])) or 0)
            if not snapshot.quality_report or snapshot.quality_report.get("status") != "passed" or not labels:
                raise ValueError("数据质量校验或 T+5 标签未通过，不能发布")
            snapshot.status, snapshot.published_at = "published", _now()
            session.commit()
        return next(item for item in self.list_market_snapshots() if item.snapshotId == snapshot_id)

    def _run_backtest(self, backtest_id: str) -> None:
        with self.session_factory() as session:
            run = session.get(BacktestRunRow, backtest_id)
            candidates = session.scalars(select(BacktestCandidateSampleRow).where(BacktestCandidateSampleRow.candidate_set_id == run.candidate_set_id, BacktestCandidateSampleRow.inclusion_status == "included")).all()
            all_labels = session.scalars(select(IpoOutcomeLabelRow).where(IpoOutcomeLabelRow.snapshot_id == run.snapshot_id)).all()
            labels = {item.id: item for item in all_labels if item.horizon == EVALUATION_PROTOCOL["horizon"]}
            labels_by_code_horizon = {(item.wind_code, item.horizon): item for item in all_labels}
            scores = {item.score_id: item for item in session.scalars(select(RiskScoreSnapshotRow).where(RiskScoreSnapshotRow.scorecard_version == SCORECARD_VERSION)).all()}
            securities = {item.wind_code: item for item in session.scalars(select(SecurityMasterRow).where(SecurityMasterRow.snapshot_id == run.snapshot_id)).all()}
            eligible = [(labels[item.label_id], scores[item.score_id], item) for item in candidates if item.label_id in labels and item.score_id in scores]
            if not eligible: raise ValueError("已确认样本没有可用的标签和评分快照")
            eligible.sort(key=lambda item: item[1].total_score, reverse=True)
            full = self._variant_metrics(eligible, lambda score: score.total_score)
            baseline = self._variant_metrics(eligible, lambda score: float((score.breakdown or {}).get("baseline_d1", 0)))
            ablations = {
                name: self._variant_metrics(eligible, lambda score, key=key: float((score.breakdown or {}).get(key, 0)))
                for name, key in (("without_d2", "without_d2"), ("without_d3", "without_d3"), ("without_ext", "without_ext"))
            }
            top_count = int(full["top_count"])
            precision = float(full["precision"])
            recall = float(full["recall"])
            roc = self._roc(eligible)
            lift = self._lift(eligible)
            cohorts: dict[str, list[tuple[Any, Any, Any]]] = {}
            cohort_names: dict[str, str] = {}
            for item in eligible:
                profile = item[1].industry_profile or {}
                code = str(profile.get("code") or "UNCLASSIFIED")
                cohort_names[code] = str(profile.get("name") or "未分类")
                cohorts.setdefault(code, []).append(item)
            industry_cohorts = []
            for code, items in sorted(cohorts.items()):
                if len(items) < 10:
                    industry_cohorts.append({"code": code, "name": cohort_names[code], "samples": len(items), "status": "insufficient_sample"})
                else:
                    cohort_metrics = self._variant_metrics(items, lambda score: score.total_score)
                    industry_cohorts.append({"code": code, "name": cohort_names[code], "samples": len(items), "status": "published", **{key: cohort_metrics[key] for key in ("auc", "ks", "lift_top20", "recall")}})
            weight_comparison = {
                "d1_only": baseline,
                "hidden_first": full,
                "uplift": {key: round(float(full[key]) - float(baseline[key]), 4) for key in ("auc", "ks", "lift_top20", "recall")},
            }
            metrics = {"samples": len(eligible), "precision": precision, "recall": recall, "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0, "lift_top20": full["lift_top20"], "auc": full["auc"], "ks": full["ks"], "top_count": top_count, "snapshot_id": run.snapshot_id, "scorecard_version": SCORECARD_VERSION, "protocol": EVALUATION_PROTOCOL, "roc": roc, "lift": lift, "weight_comparison": weight_comparison, "ablation_results": ablations, "industry_cohorts": industry_cohorts, "input_hash": _stable_hash({"dataset": session.get(MarketDatasetSnapshotRow, run.snapshot_id).file_hashes, "candidates": [{"code": item.wind_code, "score": item.score_id, "label": item.label_id} for _, _, item in eligible]})}
            for label, score, candidate in eligible:
                sec = securities.get(label.wind_code)
                specific_risks = sorted(
                    [item for item in (score.finding_snapshot or []) if str(item.get("origin") or "rule") != "rule" or _score_bucket(item) in {"D2", "D3"}],
                    key=lambda item: {"high": 3, "medium": 2, "low": 1}.get(str(item.get("severity") or ""), 0),
                    reverse=True,
                )[:5]
                outcome_profile = {}
                for horizon in (5, 20, 60):
                    outcome = labels_by_code_horizon.get((label.wind_code, horizon))
                    if outcome:
                        outcome_profile[f"T+{horizon}"] = {"return_pct": outcome.return_pct, "max_drawdown_pct": outcome.max_drawdown_pct, "significant_decline": outcome.significant_decline, "breaking": outcome.breaking}
                attribution = self._retrospective_attribution(
                    company_name=sec.name if sec and sec.name else label.wind_code,
                    listing_date=sec.listing_date if sec else "",
                    specific_risks=specific_risks,
                    outcomes=outcome_profile,
                )
                session.add(BacktestSampleRow(id=f"{backtest_id}:{label.wind_code}", backtest_id=backtest_id, wind_code=label.wind_code, company_name=sec.name if sec else None, score=score.total_score, risk_level=score.risk_level, actual_decline=label.significant_decline, actual_breaking=label.breaking, return_pct=label.return_pct, max_drawdown_pct=label.max_drawdown_pct, reason=", ".join(str(item.get("title") or item.get("risk_type") or "隐性风险") for item in specific_risks) or "无可回放的公司特有风险", score_id=score.score_id, analysis_run_id=score.run_id, analysis_as_of_date=score.analysis_as_of_date, industry_profile=score.industry_profile or {}, specific_risks=specific_risks, outcome_profile=outcome_profile, retrospective_attribution=attribution))
            run.metrics, run.status = metrics, "completed"
            session.commit()
            self._write_audit_artifact(run, eligible, metrics)
            session.commit()

    @staticmethod
    def _variant_metrics(eligible, score_getter) -> dict[str, Any]:
        ordered = sorted(eligible, key=lambda item: score_getter(item[1]), reverse=True)
        top_count = max(1, (len(ordered) + 4) // 5)
        positives = sum(label.significant_decline for label, *_ in ordered)
        selected_positive = sum(label.significant_decline for label, *_ in ordered[:top_count])
        precision = selected_positive / top_count
        recall = selected_positive / positives if positives else 0.0
        base = positives / len(ordered) if ordered else 0.0
        positive_scores = [score_getter(score) for label, score, *_ in ordered if label.significant_decline]
        negative_scores = [score_getter(score) for label, score, *_ in ordered if not label.significant_decline]
        auc = 0.0 if not positive_scores or not negative_scores else sum(1 if p > n else 0.5 if p == n else 0 for p in positive_scores for n in negative_scores) / (len(positive_scores) * len(negative_scores))
        seen_p = seen_n = 0
        negatives = len(ordered) - positives
        ks = 0.0
        if positives and negatives:
            for label, *_ in ordered:
                seen_p += bool(label.significant_decline)
                seen_n += not bool(label.significant_decline)
                ks = max(ks, abs(seen_p / positives - seen_n / negatives))
        return {"samples": len(ordered), "precision": precision, "recall": recall, "lift_top20": precision / base if base else 0.0, "auc": auc, "ks": ks, "top_count": top_count}

    def _retrospective_attribution(
        self,
        *,
        company_name: str,
        listing_date: str,
        specific_risks: list[dict[str, Any]],
        outcomes: dict[str, Any],
    ) -> dict[str, Any]:
        if not specific_risks:
            return {"status": "insufficient", "searches_used": 0, "sources": [], "note": "没有可回放的公司特有风险。"}
        result = asyncio.run(
            HypothesisResearchModule().research_retrospective(
                company_name=company_name,
                risk_titles=[str(item.get("title") or item.get("risk_type") or "") for item in specific_risks],
                listing_date=_date_key(listing_date),
                search_limit=6,
            )
        )
        if result["status"] in {"not_observed", "insufficient"} and any(bool(item.get("significant_decline")) for item in outcomes.values()):
            result["status"] = "price_aligned"
        result["note"] = "价格一致性仅衡量预警方向，不证明风险陈述本身为真；上市后来源不会修改冻结分数。"
        return result
    @staticmethod
    def _roc(items):
        positives = sum(label.significant_decline for label, *_ in items)
        negatives = len(items) - positives
        points, tp, fp = [{"fpr": 0.0, "tpr": 0.0, "threshold": None}], 0, 0
        for label, score, _ in items:
            tp += bool(label.significant_decline); fp += not bool(label.significant_decline)
            points.append({"fpr": fp / negatives if negatives else 0.0, "tpr": tp / positives if positives else 0.0, "threshold": score.total_score})
        return points

    @staticmethod
    def _lift(items):
        positives = sum(label.significant_decline for label, *_ in items)
        base = positives / len(items) if items else 0
        values = []
        for pct in (10, 20, 30, 40, 50, 100):
            count = max(1, (len(items) * pct + 99) // 100)
            hit = sum(label.significant_decline for label, *_ in items[:count])
            rate = hit / count
            values.append({"p": f"Top {pct}%", "lift": rate / base if base else 0, "gain": hit / positives if positives else 0, "baseline": 1})
        return values

    def _write_audit_artifact(self, run, eligible, metrics) -> None:
        artifact_dir = Path(__file__).resolve().parents[3] / "data" / "backtests"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{run.backtest_id}.html"
        artifact_path.write_text(
            render_backtest_audit_html(self.detail(run.backtest_id)),
            encoding="utf-8",
        )
        run.artifact_path = str(artifact_path)

    def detail(self, backtest_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            run = session.get(BacktestRunRow, backtest_id)
            if not run:
                raise KeyError(backtest_id)
            candidates = []
            if run.candidate_set_id:
                candidates = session.scalars(select(BacktestCandidateSampleRow).where(BacktestCandidateSampleRow.candidate_set_id == run.candidate_set_id).order_by(BacktestCandidateSampleRow.wind_code)).all()
            if run.status in {"candidate_pending", "candidate_review", "ready", "running", "failed"}:
                jobs = session.scalars(select(BacktestJobRow).where(BacktestJobRow.backtest_id == backtest_id).order_by(BacktestJobRow.created_at.desc())).all()
                return {"summary": self._summary(run).model_dump(), "protocol": run.protocol, "candidates": [{"code": item.wind_code, "listingDate": item.listing_date, "scoreId": item.score_id, "sourceRunId": item.source_run_id, "status": item.inclusion_status, "reason": item.exclusion_reason} for item in candidates], "jobs": [self._job(item).model_dump() for item in jobs], "metrics": run.metrics, "weightComparison": run.metrics.get("weight_comparison", {}), "ablationResults": run.metrics.get("ablation_results", {}), "industryCohorts": run.metrics.get("industry_cohorts", []), "versions": {"dataset": run.snapshot_id, "scorecard": SCORECARD_VERSION}}
            rows = session.scalars(select(BacktestSampleRow).where(BacktestSampleRow.backtest_id == backtest_id).order_by(BacktestSampleRow.score.desc())).all()
            groups: dict[str, list[Any]] = {level.value: [] for level in _RISK_LEVELS}
            for row in rows:
                groups.setdefault(str(row.risk_level), []).append(row)

            def _bucket_avg(keys: tuple[str, ...]) -> float:
                pooled = [item for key in keys for item in groups.get(key, [])]
                return sum(item.return_pct for item in pooled) / len(pooled) if pooled else 0.0

            risk_groups = [{"level": key, "samples": len(items), "eventRate": sum(item.actual_decline for item in items) / len(items) if items else 0, "avgReturn": sum(item.return_pct for item in items) / len(items) if items else 0} for key, items in groups.items()]
            samples = [{"code": row.wind_code, "name": row.company_name or row.wind_code, "score": row.score, "risk": row.risk_level, "actualDecline": row.actual_decline, "breaking": row.actual_breaking, "returnPct": row.return_pct, "maxDrawdownPct": row.max_drawdown_pct, "reason": row.reason, "analysisRunId": row.analysis_run_id, "analysisAsOfDate": row.analysis_as_of_date, "industryProfile": row.industry_profile, "specificRisks": row.specific_risks, "outcomeProfile": row.outcome_profile, "retrospectiveAttribution": row.retrospective_attribution} for row in rows]
            top_count = int(run.metrics.get("top_count", 0))
            selected = samples[:top_count]
            to_error = lambda item: {"code": item["code"], "name": item["name"], "actual": item["maxDrawdownPct"], "score": item["score"], "reason": item["reason"], "risk": item["risk"]}
            precision = float(run.metrics.get("precision", 0))
            recall = float(run.metrics.get("recall", 0))
            visuals = {
                "roc": run.metrics.get("roc", []),
                "groupRet": [{"d": "T+5", "high": _bucket_avg(("very_high", "high")), "mid": _bucket_avg(("medium",)), "low": _bucket_avg(("low", "very_low")), "hsi": 0}],
                "lift": run.metrics.get("lift", []),
                "cm": [{"label": "True Positive", "n": sum(item["actualDecline"] for item in selected), "hint": "预警且 T+5 显著下跌", "tone": "ok"}, {"label": "False Positive", "n": sum(not item["actualDecline"] for item in selected), "hint": "预警但未显著下跌", "tone": "warn"}, {"label": "False Negative", "n": sum(item["actualDecline"] for item in samples if item not in selected), "hint": "漏报", "tone": "warn"}, {"label": "True Negative", "n": sum(not item["actualDecline"] for item in samples if item not in selected), "hint": "未预警且未显著下跌", "tone": "ok"}],
                "fnSamples": [to_error(item) for item in samples if item["actualDecline"] and item not in selected],
                "fpSamples": [to_error(item) for item in selected if not item["actualDecline"]],
                "palette": ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)"],
                "hoursSaved": 0,
                "factorContributions": [{"f": f"{item['level']} 风险组 T+5 事件率", "v": item["eventRate"]} for item in risk_groups],
                "precision": precision,
                "recall": recall,
                "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0,
            }
            return {"summary": self._summary(run).model_dump(), "protocol": run.protocol, "metrics": run.metrics, "weightComparison": run.metrics.get("weight_comparison", {}), "ablationResults": run.metrics.get("ablation_results", {}), "industryCohorts": run.metrics.get("industry_cohorts", []), "riskGroups": risk_groups, "samples": samples, "visuals": visuals, "versions": {"dataset": run.snapshot_id, "scorecard": SCORECARD_VERSION}, "artifact": run.artifact_path}

    @staticmethod
    def _in_window(session, snapshot_id: str, code: str, start: str | None, end: str | None) -> bool:
        security = session.scalar(select(SecurityMasterRow).where(SecurityMasterRow.snapshot_id == snapshot_id, SecurityMasterRow.wind_code == code))
        return bool(security and (not start or (security.listing_date or "") >= start) and (not end or (security.listing_date or "") <= end))

    @staticmethod
    def _auc(items: list[tuple[IpoOutcomeLabelRow, RiskScoreSnapshotRow]]) -> float:
        positives = [score.total_score for label, score, *_ in items if label.significant_decline]
        negatives = [score.total_score for label, score, *_ in items if not label.significant_decline]
        if not positives or not negatives: return 0.0
        return sum(1 if p > n else 0.5 if p == n else 0 for p in positives for n in negatives) / (len(positives) * len(negatives))

    @staticmethod
    def _ks(items: list[tuple[IpoOutcomeLabelRow, RiskScoreSnapshotRow]]) -> float:
        positives, negatives = sum(label.significant_decline for label, *_ in items), sum(not label.significant_decline for label, *_ in items)
        if not positives or not negatives: return 0.0
        seen_p = seen_n = 0; best = 0.0
        for label, *_ in items:
            seen_p += bool(label.significant_decline); seen_n += not bool(label.significant_decline)
            best = max(best, abs(seen_p / positives - seen_n / negatives))
        return best

    @staticmethod
    def _summary(row: BacktestRunRow, job_id: str | None = None) -> BacktestSummary:
        metrics = row.metrics
        labels = {"completed": "完成", "failed": "失败", "candidate_review": "待确认", "ready": "待运行", "candidate_pending": "构建候选中", "running": "运行中"}
        return BacktestSummary(id=row.backtest_id, name=row.name, samples=int(metrics.get("samples", 0)), auc=round(float(metrics.get("auc", 0)), 4), ks=round(float(metrics.get("ks", 0)), 4), lift10=round(float(metrics.get("lift_top20", 0)), 3), recall=round(float(metrics.get("recall", 0)), 4), createdAt=row.created_at, status=labels.get(row.status, row.status), candidateSetId=row.candidate_set_id, jobId=job_id)


service = BacktestService()
