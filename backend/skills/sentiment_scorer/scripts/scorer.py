"""
Sentiment Scorer Skill — 市场情绪 / 舆情评分
============================================

5 维加权评分模型 + 结论式输出 + 置信度评分 + 历史准确率统计，
基于 2016–2026 年港股 IPO 历史数据。

独立运行::

    python scorer.py <ticker>
    python scorer.py 6871.HK --check
    python scorer.py 6871.HK --verbose --check
    python scorer.py --batch 6871.HK 7666.HK --rank
    python scorer.py --accuracy
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Optional Anthropic SDK
# ---------------------------------------------------------------------------
try:
    from anthropic import Anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False
    Anthropic = None  # type: ignore

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SKILL_DIR = Path(__file__).resolve().parents[1]  # skill3_sentiment_scorer/
_REFS_DIR = _SKILL_DIR / "references"
_DATA_CANDIDATES = [
    _REFS_DIR / "IPO_data.xlsx",
    _REFS_DIR / "ipo_basic_info.xlsx",
    _SKILL_DIR.parent / "references" / "IPO_data.xlsx",
    _SKILL_DIR.parent / "data" / "IPO_data.xlsx",
    Path("references/IPO_data.xlsx"),
    Path("data/IPO_data.xlsx"),
    Path("IPO_data.xlsx"),
]


def _find_data_file() -> Path | None:
    for p in _DATA_CANDIDATES:
        if p.exists():
            return p
    return None


# ======================================================================
# Sponsor / Underwriter Tier Classification
# ======================================================================

SPONSOR_TIER_1: set[str] = {
    "中国国际金融香港证券有限公司",
    "高盛(亚洲)有限责任公司", "摩根士丹利亚洲有限公司", "摩根大通",
    "J.P. Morgan Securities (Far East) Limited", "J.P. Morgan",
    "Merrill Lynch Far East Limited", "Merrill Lynch",
    "UBS Securities Hong Kong Limited", "UBS",
    "Citigroup Global Markets Asia Limited", "Citigroup", "花旗",
}

SPONSOR_TIER_2: set[str] = {
    "中信证券(香港)有限公司", "华泰国际", "华泰金融控股(香港)有限公司",
    "海通国际证券有限公司", "海通国际", "国泰君安融资有限公司", "国泰君安",
    "招商证券(香港)有限公司", "招商国际",
    "瑞士信贷(香港)有限公司", "Credit Suisse",
    "德意志银行", "Deutsche Bank", "申万宏源", "广发证券", "中银国际",
}

# Cornerstone / institutional quality boost (top-tier cornerstone investors)
_CORNERSTONE_TIER_1: set[str] = {
    "高瓴", "高瓴资本", "淡马锡", "GIC", "红杉", "红杉资本",
    "Hillhouse", "Temasek", "Sequoia", "中国国有企业结构调整基金",
    "全国社保基金", "中国人寿", "中国平安",
}


def _classify_sponsor(sponsors_str: str | None) -> tuple[int, float]:
    """Return (tier, score) for the highest-tier sponsor found."""
    if not sponsors_str or pd.isna(sponsors_str):
        return (4, -0.30)
    names = [n.strip() for n in str(sponsors_str).split(",") if n.strip()]
    if not names:
        return (4, -0.30)
    for name in names:
        if name in SPONSOR_TIER_1:
            return (1, 1.00)
    for name in names:
        if name in SPONSOR_TIER_2:
            return (2, 0.60)
    return (3, 0.00)


def _detect_cornerstone(sponsors_str: str | None) -> float:
    """Return +0.05 bonus if top-tier cornerstone investor detected."""
    if not sponsors_str or pd.isna(sponsors_str):
        return 0.0
    for name in _CORNERSTONE_TIER_1:
        if name in str(sponsors_str):
            return 0.05
    return 0.0


# ======================================================================
# SentimentScorer
# ======================================================================


class SentimentScorer:
    """5-dimension weighted sentiment scorer with conclusion output,
    confidence scoring, industry overlay, and historical accuracy tracking."""

    POSITIVE_TERMS: list[str] = [
        "增持", "买入", "跑赢大市", "看好", "行业龙头",
        "overweight", "buy", "outperform", "positive", "leader",
    ]
    NEGATIVE_TERMS: list[str] = [
        "减持", "卖出", "跑输大市", "看淡", "监管风险",
        "underweight", "sell", "underperform", "negative", "risk",
    ]

    DIMENSION_WEIGHTS: dict[str, float] = {
        "subscription_ratio": 0.42,
        "lottery_rate": 0.21,
        "participation": 0.16,
        "sponsor_quality": 0.11,
        "deal_structure": 0.10,
    }

    # ------------------------------------------------------------------
    def __init__(self, data_path: str | Path | None = None) -> None:
        self._df: pd.DataFrame | None = None
        self._data_loaded: bool = False
        self._data_error: str | None = None
        self._sector_stats: dict[str, dict[str, float]] = {}
        self._global_stats: dict[str, float] = {}
        self.dimension_weights = dict(self.DIMENSION_WEIGHTS)

        if data_path is not None:
            resolved = Path(data_path)
        else:
            resolved = _find_data_file()

        if resolved is None:
            self._data_error = (
                "Excel 数据文件未找到。请将 IPO_data.xlsx 放入 references/ 目录。"
            )
            return

        try:
            raw = pd.read_excel(resolved, dtype=str)
        except Exception as exc:
            self._data_error = f"读取 Excel 失败: {exc}"
            return

        code_col = self._resolve_column(raw, ["代码", "股票代码"])
        if code_col is None:
            self._data_error = "Excel 中未找到 '代码' 或 '股票代码' 列"
            return

        mask = raw[code_col].notna() & (raw[code_col].str.strip() != "")
        self._df = raw[mask].copy()
        self._df[code_col] = self._df[code_col].str.strip().str.upper()

        self._col_map: dict[str, str | None] = {
            "代码": code_col,
            "名称": self._resolve_column(raw, ["名称", "股票名称"]),
            "认购倍数": self._resolve_column(raw, ["认购倍数"]),
            "中签率(%)": self._resolve_column(raw, ["中签率(%)", "中签率"]),
            "所属行业(HS)": self._resolve_column(raw, ["所属行业(HS)", "所属行业"]),
            "所属Wind行业": self._resolve_column(raw, ["所属Wind行业"]),
            "保荐人": self._resolve_column(raw, ["保荐人"]),
            "主承销商": self._resolve_column(raw, ["主承销商"]),
            "申购一手中签率(%)": self._resolve_column(raw, ["申购一手中签率(%)"]),
            "申购人数": self._resolve_column(raw, ["申购人数"]),
            "申购人数.1": self._resolve_column(raw, ["申购人数.1"]),
            "申购人数.2": self._resolve_column(raw, ["申购人数.2"]),
            "申购股份数(万股)": self._resolve_column(raw, ["申购股份数(万股)"]),
            "申购股份数(万股).1": self._resolve_column(raw, ["申购股份数(万股).1"]),
            "申购股份数(万股).2": self._resolve_column(raw, ["申购股份数(万股).2"]),
            "上市首日.涨跌幅(%)": self._resolve_column(raw, ["上市首日.3"]),
            "上市首日.换手率(%)": self._resolve_column(raw, ["上市首日.4"]),
            "上市首日.开盘涨跌幅(%)": self._resolve_column(raw, ["上市首日.5"]),
            "上市首日.收益率(后复权)": self._resolve_column(raw, ["上市首日.2"]),
            "上市首日.市盈率": self._resolve_column(raw, ["上市首日.7"]),
            "上市首日.市净率": self._resolve_column(raw, ["上市首日.8"]),
            "上市天数": self._resolve_column(raw, ["Unnamed: 20"]),
            "发行价格": self._resolve_column(raw, ["Unnamed: 21"]),
            "上市板": self._resolve_column(raw, ["Unnamed: 22"]),
            "首发数量(百万股)": self._resolve_column(raw, ["首发数量(百万股)"]),
            "实际募资总额(百万港元)": self._resolve_column(raw, ["实际募资总额(百万港元)"]),
            "稳价一手最低申购股数(股)": self._resolve_column(raw, ["稳价一手最低申购股数(股)"]),
        }

        # Pre-compute sector statistics for fallback & overlay
        self._build_sector_stats()
        self._data_loaded = True

    # ------------------------------------------------------------------
    # Sector statistics (for fallback + industry overlay)
    # ------------------------------------------------------------------

    def _build_sector_stats(self) -> None:
        """Pre-compute sector medians and recent performance."""
        if self._df is None:
            return
        industry_col = self._col_map["所属行业(HS)"]
        sub_col = self._col_map["认购倍数"]
        first_day_col = self._col_map["上市首日.涨跌幅(%)"]

        if industry_col is None:
            return

        # Global median subscription ratio
        if sub_col:
            subs = pd.to_numeric(self._df[sub_col], errors="coerce")
            self._global_stats["median_sub"] = float(subs.median()) if subs.notna().any() else 50.0
            self._global_stats["mean_sub"] = float(subs.mean()) if subs.notna().any() else 100.0

        # Per-sector stats
        for sector, group in self._df.groupby(industry_col):
            stats: dict[str, float] = {}
            if sub_col:
                s = pd.to_numeric(group[sub_col], errors="coerce")
                stats["median_sub"] = float(s.median()) if s.notna().any() else 50.0
                stats["mean_sub"] = float(s.mean()) if s.notna().any() else 100.0
                stats["count"] = len(s.dropna())
            if first_day_col:
                fd = pd.to_numeric(group[first_day_col], errors="coerce")
                stats["mean_first_day"] = float(fd.mean()) if fd.notna().any() else 0.0
                stats["recent_first_day"] = float(fd.tail(20).mean()) if len(fd) >= 20 else stats["mean_first_day"]
            self._sector_stats[str(sector).strip()] = stats

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        return self._data_loaded

    @property
    def data_error(self) -> str | None:
        return self._data_error

    # ------------------------------------------------------------------
    # Public API — Check sentiment (NEW: conclusion-style output)
    # ------------------------------------------------------------------

    def check_sentiment(self, ticker: str) -> dict[str, Any]:
        """Run sentiment scoring and return a clear conclusion.

        Returns dict with ``has_signal``, ``conclusion`` (中文结论),
        ``recommendation``, ``score``, ``signal``, ``confidence``,
        and full dimension details.
        """
        result = self.get_sentiment_score(ticker)
        score = result["score"]
        signal = result["signal"]
        dims = result.get("dimensions") or {}
        ref = result.get("reference")

        # --- Confidence ---
        confidence = self._compute_confidence(dims, ref)

        # --- Industry overlay (NEW) ---
        industry_bonus = 0.0
        industry_note = ""
        if ref and ref.get("industry_hs"):
            industry_bonus, industry_note = self._get_industry_overlay(
                ref["industry_hs"]
            )
            if industry_bonus != 0.0:
                score = round(max(-1.0, min(1.0, score + industry_bonus)), 4)
                signal = self._signal(score)

        # --- Conclusion & recommendation ---
        conclusion, recommendation = self._make_conclusion(score, signal, confidence)

        return {
            "ticker": ticker.upper().strip(),
            "has_signal": signal not in ("neutral",),
            "conclusion": conclusion,
            "recommendation": recommendation,
            "score": score,
            "signal": signal,
            "confidence": confidence,
            "source": result.get("source", "unknown"),
            "industry_overlay": {
                "bonus": industry_bonus,
                "note": industry_note,
            } if industry_bonus != 0.0 else None,
            "dimensions": dims,
            "active_weights": result.get("active_weights"),
            "reference": ref,
        }

    # ------------------------------------------------------------------
    # Public API — Historical accuracy (NEW)
    # ------------------------------------------------------------------

    def get_accuracy_stats(self) -> dict[str, Any]:
        """Compute how well the sentiment model predicts actual first-day returns.

        Scans all tickers in the dataset that have both subscription data
        (for scoring) and first-day returns (for validation).
        """
        if not self._data_loaded or self._df is None:
            return {"error": "数据未加载"}

        first_day_col = self._col_map["上市首日.涨跌幅(%)"]
        if first_day_col is None:
            return {"error": "缺少首日涨跌幅列"}

        # Build scored dataset
        scores: list[dict[str, Any]] = []
        code_col = self._col_map["代码"]

        for _, rec in self._df.iterrows():
            ticker = str(rec[code_col]).strip()
            if not ticker:
                continue
            result = self.get_sentiment_score(ticker)
            if result.get("source") == "placeholder_hash":
                continue  # skip hash-based scores

            try:
                first_day = float(rec[first_day_col])
            except (ValueError, TypeError):
                continue
            if math.isnan(first_day):
                continue

            scores.append({
                "ticker": ticker,
                "score": result["score"],
                "signal": result["signal"],
                "first_day_chg": first_day,
            })

        if not scores:
            return {"error": "无可用验证数据", "count": 0}

        # Signal-tier accuracy
        tier_stats: dict[str, dict[str, Any]] = {}
        for tier in ["strongly_bullish", "bullish", "neutral", "bearish", "strongly_bearish"]:
            subset = [s for s in scores if s["signal"] == tier]
            if not subset:
                tier_stats[tier] = {"count": 0, "avg_first_day": 0, "up_ratio": 0}
                continue
            chgs = [s["first_day_chg"] for s in subset]
            up_count = sum(1 for c in chgs if c > 0)
            tier_stats[tier] = {
                "count": len(subset),
                "avg_first_day": round(sum(chgs) / len(chgs), 2),
                "up_ratio": round(up_count / len(chgs) * 100, 1),
                "median_first_day": round(sorted(chgs)[len(chgs) // 2], 2),
                "min_chg": round(min(chgs), 2),
                "max_chg": round(max(chgs), 2),
            }

        # Correlation
        s_vals = [s["score"] for s in scores]
        f_vals = [s["first_day_chg"] for s in scores]
        correlation = self._pearson_r(s_vals, f_vals)

        # Score distribution
        all_scores = [s["score"] for s in scores]
        all_chgs = [s["first_day_chg"] for s in scores]

        return {
            "total_validated": len(scores),
            "correlation_with_first_day": round(correlation, 4),
            "score_distribution": {
                "mean": round(sum(all_scores) / len(all_scores), 4),
                "median": round(sorted(all_scores)[len(all_scores) // 2], 4),
                "min": round(min(all_scores), 4),
                "max": round(max(all_scores), 4),
            },
            "first_day_distribution": {
                "mean": round(sum(all_chgs) / len(all_chgs), 2),
                "median": round(sorted(all_chgs)[len(all_chgs) // 2], 2),
            },
            "tier_accuracy": tier_stats,
            "signal_distribution": {
                tier: tier_stats[tier]["count"] for tier in tier_stats
            },
        }

    # ------------------------------------------------------------------
    # Public API — Reference data retrieval
    # ------------------------------------------------------------------

    def get_reference_data(self, ticker: str) -> dict[str, Any] | None:
        if not self._data_loaded or self._df is None:
            return None
        code_col = self._col_map["代码"]
        row = self._df[self._df[code_col] == ticker.upper().strip()]
        if row.empty:
            return None
        rec = row.iloc[0]

        def _f(key: str) -> float | None:
            col = self._col_map.get(key)
            if col is None:
                return None
            try:
                return float(rec[col])
            except (ValueError, TypeError):
                return None

        def _s(key: str) -> str | None:
            col = self._col_map.get(key)
            if col is None:
                return None
            val = rec.get(col)
            if pd.isna(val) or str(val).strip() == "":
                return None
            return str(val).strip()

        total_applicants = _f("申购人数")
        group_a_applicants = _f("申购人数.1")
        group_b_applicants = _f("申购人数.2")
        total_sub_shares = _f("申购股份数(万股)")
        group_b_sub_shares = _f("申购股份数(万股).2")

        inst_ratio = None
        if total_sub_shares and group_b_sub_shares and total_sub_shares > 0:
            inst_ratio = round(group_b_sub_shares / total_sub_shares, 4)

        return {
            "ticker": ticker.upper().strip(),
            "name": _s("名称"),
            "subscription_ratio": _f("认购倍数"),
            "lottery_rate": _f("中签率(%)"),
            "one_hand_lottery_rate": _f("申购一手中签率(%)"),
            "total_applicants": total_applicants,
            "group_a_applicants": group_a_applicants,
            "group_b_applicants": group_b_applicants,
            "total_sub_shares": total_sub_shares,
            "group_b_sub_ratio": inst_ratio,
            "sponsors": _s("保荐人"),
            "underwriters": _s("主承销商"),
            "industry_hs": _s("所属行业(HS)"),
            "industry_wind": _s("所属Wind行业"),
            "board": _s("上市板"),
            "issue_price": _f("发行价格"),
            "ipo_shares": _f("首发数量(百万股)"),
            "total_raised": _f("实际募资总额(百万港元)"),
            "min_lot_shares": _f("稳价一手最低申购股数(股)"),
            "first_day_chg": _f("上市首日.涨跌幅(%)"),
            "first_day_turnover": _f("上市首日.换手率(%)"),
            "first_day_open_chg": _f("上市首日.开盘涨跌幅(%)"),
            "first_day_return": _f("上市首日.收益率(后复权)"),
            "first_day_pe": _f("上市首日.市盈率"),
            "first_day_pb": _f("上市首日.市净率"),
            "listing_days": _f("上市天数"),
        }

    # ------------------------------------------------------------------
    # Public API — Sentiment scoring
    # ------------------------------------------------------------------

    def get_sentiment_score(self, ticker: str) -> dict[str, Any]:
        ref = self.get_reference_data(ticker)
        if ref is None:
            return self._placeholder_score(ticker)
        if ref.get("subscription_ratio") is None:
            return self._placeholder_score(ticker)

        dims = self._score_all_dimensions(ref)
        total, active_weights = self._compute_weighted_total(dims)

        return {
            "ticker": ticker.upper().strip(),
            "score": total,
            "signal": self._signal(total),
            "source": "reference_data",
            "dimensions": dims,
            "active_weights": active_weights,
            "reference": ref,
        }

    def analyze(self, ticker: str) -> dict[str, Any]:
        base = self.get_sentiment_score(ticker)
        ref = base.get("reference")

        result: dict[str, Any] = {
            "ticker": ticker.upper().strip(),
            "reference_data": ref,
            "score": base["score"],
            "signal": base["signal"],
            "source": base["source"],
            "dimensions": base.get("dimensions"),
            "active_weights": base.get("active_weights"),
        }

        if _HAS_ANTHROPIC and os.getenv("ANTHROPIC_API_KEY") and ref:
            llm = self._llm_analyze(ticker, ref, base)
            if llm and llm.get("score") is not None:
                result["llm_analysis"] = llm
                result["score"] = round(base["score"] * 0.7 + llm["score"] * 0.3, 2)
                result["signal"] = self._signal(result["score"])
                result["source"] = "reference_data + llm"

        return result

    def score_text(self, text: str) -> float:
        text_lower = text.lower()
        pos = sum(1 for t in self.POSITIVE_TERMS if t.lower() in text_lower)
        neg = sum(1 for t in self.NEGATIVE_TERMS if t.lower() in text_lower)
        total = pos + neg
        if total == 0:
            return 0.0
        return round((pos - neg) / total, 2)

    # ==================================================================
    # Dimension scorers
    # ==================================================================

    def _score_all_dimensions(self, ref: dict[str, Any]) -> dict[str, Any]:
        dims: dict[str, dict[str, Any]] = {}

        dims["subscription_ratio"] = {
            "score": self._score_sub_ratio(ref["subscription_ratio"]),
            "weight": self.dimension_weights["subscription_ratio"],
            "value": ref["subscription_ratio"],
            "status": "ok",
        }

        lr = ref.get("lottery_rate")
        dims["lottery_rate"] = {
            "score": self._score_lottery_rate(lr),
            "weight": self.dimension_weights["lottery_rate"],
            "value": lr,
            "status": "ok" if lr is not None else "missing",
        }

        dims["participation"] = self._score_participation(ref)
        dims["sponsor_quality"] = self._score_sponsor_quality(ref)
        dims["deal_structure"] = self._score_deal_structure(ref)

        return dims

    # D1
    @staticmethod
    def _score_sub_ratio(sub: float | None) -> float:
        if sub is None:
            return 0.0
        if sub >= 1000: return 1.00
        if sub >= 500:  return 0.85
        if sub >= 100:  return 0.70
        if sub >= 50:   return 0.50
        if sub >= 10:   return 0.20
        if sub >= 1:    return -0.10
        return -0.50

    # D2
    @staticmethod
    def _score_lottery_rate(lr: float | None) -> float:
        if lr is None: return 0.0
        if lr < 0.05: return 1.00
        if lr < 0.1:  return 0.80
        if lr < 1:    return 0.60
        if lr < 10:   return 0.30
        if lr < 50:   return 0.00
        if lr < 80:   return -0.30
        return -0.60

    # D3
    def _score_participation(self, ref: dict[str, Any]) -> dict[str, Any]:
        total = ref.get("total_applicants")
        inst_ratio = ref.get("group_b_sub_ratio")

        if total is None and inst_ratio is None:
            return {"score": None, "weight": self.dimension_weights["participation"],
                    "value": None, "status": "missing", "detail": "无申购人数数据"}

        score_a = 0.0
        if total is not None:
            if total > 100_000: score_a = 1.00
            elif total >= 50_000: score_a = 0.70
            elif total >= 10_000: score_a = 0.40
            elif total >= 5_000: score_a = 0.10
            elif total >= 1_000: score_a = -0.10
            else: score_a = -0.40

        score_b = 0.0
        if inst_ratio is not None:
            if inst_ratio > 0.80: score_b = 1.00
            elif inst_ratio >= 0.60: score_b = 0.60
            elif inst_ratio >= 0.40: score_b = 0.20
            elif inst_ratio >= 0.20: score_b = 0.00
            else: score_b = -0.40

        if total is None: final = score_b
        elif inst_ratio is None: final = score_a
        else: final = 0.5 * score_a + 0.5 * score_b

        return {
            "score": round(final, 2),
            "weight": self.dimension_weights["participation"],
            "value": {"total_applicants": total, "group_b_sub_ratio": inst_ratio,
                      "sub_score_applicants": score_a if total is not None else None,
                      "sub_score_inst_ratio": score_b if inst_ratio is not None else None},
            "status": "ok",
        }

    # D4
    def _score_sponsor_quality(self, ref: dict[str, Any]) -> dict[str, Any]:
        sponsors_str = ref.get("sponsors")
        tier, score = _classify_sponsor(sponsors_str)
        cornerstone_bonus = _detect_cornerstone(sponsors_str)

        tier_label = {1: "Tier 1 (顶级国际投行)", 2: "Tier 2 (一线)",
                       3: "Tier 3 (二线/区域)", 4: "Tier 4 (小型/非知名)"}

        status = "ok" if sponsors_str else "missing"
        final_score = round(min(1.0, score + cornerstone_bonus), 2) if status == "ok" else None
        return {
            "score": final_score,
            "weight": self.dimension_weights["sponsor_quality"],
            "value": sponsors_str,
            "tier": tier,
            "tier_label": tier_label.get(tier, "Unknown"),
            "cornerstone_bonus": cornerstone_bonus,
            "status": status,
        }

    # D5
    def _score_deal_structure(self, ref: dict[str, Any]) -> dict[str, Any]:
        board = ref.get("board")
        raised = ref.get("total_raised")

        board_score = 0.0
        board_status = "missing"
        if board:
            board_status = "ok"
            if "主板" in str(board): board_score = 0.20
            elif "创业" in str(board) or "GEM" in str(board).upper(): board_score = -0.50

        size_score = 0.0
        size_status = "missing"
        if raised is not None:
            size_status = "ok"
            if raised > 10000: size_score = 0.80
            elif raised >= 1000: size_score = 0.40
            elif raised >= 100: size_score = 0.00
            else: size_score = -0.40

        if board_status == "missing" and size_status == "missing":
            return {"score": None, "weight": self.dimension_weights["deal_structure"],
                    "value": None, "status": "missing"}

        if board_status == "missing": final = size_score
        elif size_status == "missing": final = board_score
        else: final = 0.6 * board_score + 0.4 * size_score

        return {
            "score": round(final, 2),
            "weight": self.dimension_weights["deal_structure"],
            "value": {"board": board, "total_raised_hkd_mn": raised},
            "sub_score_board": board_score if board_status == "ok" else None,
            "sub_score_size": size_score if size_status == "ok" else None,
            "status": "ok",
        }

    # ==================================================================
    # Weighted total with dynamic redistribution
    # ==================================================================

    def _compute_weighted_total(
        self, dims: dict[str, dict[str, Any]]
    ) -> tuple[float, dict[str, float]]:
        available: dict[str, float] = {}
        missing_weight = 0.0
        for key, d in dims.items():
            if d["score"] is not None:
                available[key] = d["weight"]
            else:
                missing_weight += d["weight"]

        if not available:
            return 0.0, {}

        total_available = sum(available.values())
        active_weights: dict[str, float] = {}
        for key, w in available.items():
            active_weights[key] = round(w + missing_weight * (w / total_available), 4)

        total = 0.0
        for key, w in active_weights.items():
            total += dims[key]["score"] * w

        return round(max(-1.0, min(1.0, total)), 4), active_weights

    # ==================================================================
    # Confidence scoring (NEW)
    # ==================================================================

    @staticmethod
    def _compute_confidence(
        dims: dict[str, dict[str, Any]] | None,
        ref: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Calculate confidence level based on data completeness."""
        if dims is None:
            return {"level": "none", "label": "无数据", "reason": "ticker不在数据集中，使用占位分数"}
        if ref is None:
            return {"level": "none", "label": "无数据", "reason": "缺少参考数据"}

        available = sum(1 for d in dims.values() if d["score"] is not None)
        total = len(dims)
        missing_dims = [k for k, d in dims.items() if d["score"] is None]

        if available >= 4:
            return {"level": "high", "label": "高置信度",
                    "available_dimensions": available, "total_dimensions": total,
                    "missing_dimensions": missing_dims}
        elif available >= 2:
            return {"level": "medium", "label": "中等置信度",
                    "available_dimensions": available, "total_dimensions": total,
                    "missing_dimensions": missing_dims,
                    "reason": f"仅 {available}/{total} 维度可用，权重重分配可能放大偏差"}
        elif available >= 1:
            return {"level": "low", "label": "低置信度",
                    "available_dimensions": available, "total_dimensions": total,
                    "missing_dimensions": missing_dims,
                    "reason": f"仅 {available}/{total} 维度可用，评分可靠性低"}
        else:
            return {"level": "none", "label": "无数据", "reason": "所有维度缺失"}

    # ==================================================================
    # Industry sentiment overlay (NEW)
    # ==================================================================

    def _get_industry_overlay(self, industry: str) -> tuple[float, str]:
        """Compute a bonus/penalty based on recent sector IPO performance.

        Returns (bonus: float, note: str).
        Bonus range: [-0.10, +0.10].
        """
        sector_stats = self._sector_stats.get(industry, {})
        if not sector_stats:
            return (0.0, "")

        recent = sector_stats.get("recent_first_day")
        mean_fd = sector_stats.get("mean_first_day")
        if recent is None or mean_fd is None:
            return (0.0, "")

        # Recent deviation from sector mean
        deviation = recent - mean_fd
        if deviation > 15:
            return (0.08, f"{industry}近期IPO首日表现强劲（均{recent:.1f}%），行业情绪正面")
        elif deviation > 5:
            return (0.04, f"{industry}近期IPO表现温和偏强（均{recent:.1f}%）")
        elif deviation < -15:
            return (-0.08, f"{industry}近期IPO首日表现疲弱（均{recent:.1f}%），行业情绪偏冷")
        elif deviation < -5:
            return (-0.04, f"{industry}近期IPO表现偏弱（均{recent:.1f}%）")
        return (0.0, "")

    # ==================================================================
    # Conclusion builder (NEW)
    # ==================================================================

    @staticmethod
    def _make_conclusion(
        score: float, signal: str, confidence: dict[str, Any]
    ) -> tuple[str, str]:
        """Return (conclusion_text, recommendation_text)."""
        conf_label = confidence.get("label", "")
        conf_suffix = f"（{conf_label}）" if confidence.get("level") in ("medium", "low") else ""

        if signal == "strongly_bullish":
            return (
                f"申购情绪极热{conf_suffix}，市场追捧度极高",
                "强烈建议申购"
            )
        elif signal == "bullish":
            return (
                f"申购情绪偏热{conf_suffix}，市场需求旺盛",
                "建议申购"
            )
        elif signal == "neutral":
            if score >= 0:
                return (
                    f"申购情绪中性偏暖{conf_suffix}，市场反应平淡",
                    "可选择性参与"
                )
            return (
                f"申购情绪中性偏冷{conf_suffix}，缺乏明确方向",
                "谨慎参与"
            )
        elif signal == "bearish":
            return (
                f"申购情绪偏冷{conf_suffix}，市场关注度低",
                "谨慎参与"
            )
        else:  # strongly_bearish
            return (
                f"申购情绪极冷{conf_suffix}，市场冷淡",
                "建议回避"
            )

    # ==================================================================
    # Helpers
    # ==================================================================

    @staticmethod
    def _signal(score: float) -> str:
        if score > 0.50: return "strongly_bullish"
        if score > 0.25: return "bullish"
        if score >= -0.25: return "neutral"
        if score >= -0.50: return "bearish"
        return "strongly_bearish"

    def _placeholder_score(self, ticker: str) -> dict[str, Any]:
        """Use sector median as fallback instead of pure hash."""
        # Try to use global median sub_ratio as baseline
        median_sub = self._global_stats.get("median_sub", 50.0)
        # Normalize: map the median to a neutral-ish score
        # If median is > 100 (hot market), give slight positive bias
        if median_sub > 100:
            pseudo = 0.15
        elif median_sub > 50:
            pseudo = 0.05
        elif median_sub > 10:
            pseudo = 0.0
        elif median_sub > 1:
            pseudo = -0.10
        else:
            pseudo = -0.30

        return {
            "ticker": ticker.upper().strip(),
            "score": pseudo,
            "signal": self._signal(pseudo),
            "source": "sector_median_fallback",
            "dimensions": None,
            "active_weights": None,
            "reference": None,
        }

    @staticmethod
    def _resolve_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
        for c in candidates:
            if c in df.columns:
                return c
        return None

    @staticmethod
    def _pearson_r(x: list[float], y: list[float]) -> float:
        """Compute Pearson correlation coefficient."""
        n = len(x)
        if n < 3:
            return 0.0
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(xi * yi for xi, yi in zip(x, y))
        sum_x2 = sum(xi * xi for xi in x)
        sum_y2 = sum(yi * yi for yi in y)
        num = n * sum_xy - sum_x * sum_y
        den = math.sqrt((n * sum_x2 - sum_x * sum_x) * (n * sum_y2 - sum_y * sum_y))
        if den == 0:
            return 0.0
        return num / den

    # ==================================================================
    # LLM integration
    # ==================================================================

    def _llm_analyze(
        self, ticker: str, ref: dict[str, Any], base: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not _HAS_ANTHROPIC:
            return None
        ref_text = self._build_ref_text(ref)
        dims_text = self._build_dims_text(base.get("dimensions", {}))
        prompt_template = _load_prompt()

        system_prompt = (
            "你是一位港股 IPO 分析专家。请基于提供的参考数据和规则引擎评分结果，"
            "对该新股的申购热度进行独立评分。重点关注保荐人阵容强弱、基石投资者"
            "质量、行业当前情绪等规则引擎未完全覆盖的定性因素。"
        )
        user_message = (
            f"请分析港股新股 {ticker} 的市场情绪。\n\n"
            f"{ref_text}\n\n【规则引擎评分结果】\n{dims_text}\n\n"
            + prompt_template
        )

        try:
            client = Anthropic()
            resp = client.messages.create(
                model="claude-sonnet-5",
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            text = resp.content[0].text
            score = self._extract_score(text) if text else None
            return {
                "score": score,
                "signal": self._signal(score) if score is not None else None,
                "raw_response": text,
            }
        except Exception as exc:
            return {"score": None, "signal": None, "error": str(exc)}

    def _build_ref_text(self, ref: dict[str, Any]) -> str:
        parts = []
        if ref.get("name"): parts.append(f"公司名称：{ref['name']}")
        if ref.get("industry_hs"): parts.append(f"所属行业(HS)：{ref['industry_hs']}")
        if ref.get("industry_wind"): parts.append(f"所属行业(Wind)：{ref['industry_wind']}")
        if ref.get("subscription_ratio") is not None:
            parts.append(f"认购倍数：{ref['subscription_ratio']:.1f} 倍")
        if ref.get("lottery_rate") is not None:
            parts.append(f"中签率：{ref['lottery_rate']:.4f}%")
        if ref.get("one_hand_lottery_rate") is not None:
            parts.append(f"一手中签率：{ref['one_hand_lottery_rate']:.2f}%")
        if ref.get("total_applicants") is not None:
            parts.append(f"申购人数：{ref['total_applicants']:.0f}")
        if ref.get("group_b_sub_ratio") is not None:
            parts.append(f"乙组占比：{ref['group_b_sub_ratio']*100:.1f}%")
        if ref.get("sponsors"): parts.append(f"保荐人：{ref['sponsors']}")
        if ref.get("underwriters"): parts.append(f"主承销商：{ref['underwriters']}")
        if ref.get("board"): parts.append(f"上市板：{ref['board']}")
        if ref.get("total_raised") is not None:
            parts.append(f"募资总额：{ref['total_raised']:.0f} 百万港元")
        if ref.get("first_day_chg") is not None:
            parts.append(f"上市首日涨跌幅：{ref['first_day_chg']:.2f}%")
        return "参考数据：" + "，".join(parts) + "。" if parts else ""

    def _build_dims_text(self, dims: dict[str, Any]) -> str:
        if not dims: return "(无)"
        lines = []
        for key, d in dims.items():
            if d["score"] is not None:
                lines.append(f"  {key}: score={d['score']:.2f}  weight={d['weight']:.0%}  value={d.get('value')}")
            else:
                lines.append(f"  {key}: (缺失, weight={d['weight']:.0%})")
        return "\n".join(lines)

    @staticmethod
    def _extract_score(text: str) -> float | None:
        for pattern in [
            r"(?:score|评分|得分)[\s:：]*([+-]?\d+\.?\d*)",
            r"(?:sentiment|情绪)[\s:：]*([+-]?\d+\.?\d*)",
        ]:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                val = float(m.group(1))
                if abs(val) > 1.5:
                    val = max(-1.0, min(1.0, val / 100.0))
                return round(val, 2)
        bullish = sum(1 for w in ["极热", "强烈看多", "bullish", "强烈推荐"] if w in text)
        bearish = sum(1 for w in ["冷门", "看空", "bearish", "回避"] if w in text)
        if bullish > bearish: return 0.6
        elif bearish > bullish: return -0.4
        return None


# ======================================================================
# Prompt loader
# ======================================================================


def _load_prompt() -> str:
    candidates = [
        Path(__file__).resolve().parents[1] / "references" / "sentiment_prompt.md",
        Path("references/sentiment_prompt.md"),
        Path("prompts/sentiment.md"),
    ]
    for p in candidates:
        if p.exists():
            return p.read_text(encoding="utf-8")
    return (
        "请基于参考数据给出评分（-1.0 到 +1.0）。\n"
        "评分规则：\n"
        "- 认购倍数 > 1000 倍 → 极热，score > 0.9\n"
        "- 认购倍数 > 100 倍 → 热门，score > 0.7\n"
        "- 认购倍数 50-100 倍 → 温和，score 0.4-0.7\n"
        "- 认购倍数 10-50 倍 → 偏冷，score 0.0-0.4\n"
        "- 认购倍数 < 1 倍 → 不足额，score < 0\n"
        "- 保荐人为顶级机构（中金、高盛、摩根士丹利等）额外加分\n"
        "- 中签率极低（< 0.1%）额外加分\n"
        '请以 JSON 格式输出：{"score": 0.75, "rationale": "..."}'
    )


# ======================================================================
# Standalone CLI
# ======================================================================


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SentimentScorer — 5维加权市场舆情评分 + 结论输出 + 置信度 + 历史准确率",
    )
    parser.add_argument("ticker", nargs="?", default=None,
                        help="Stock ticker (e.g. 6871.HK)")
    parser.add_argument("--check", action="store_true",
                        help="Conclusion-style output with recommendation")
    parser.add_argument("--analyze", "-a", action="store_true",
                        help="Full analysis with LLM (requires ANTHROPIC_API_KEY)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show per-dimension scoring breakdown with natural language")
    parser.add_argument("--text", "-t", default=None,
                        help="Score a custom text snippet with the lexicon scorer")
    parser.add_argument("--batch", "-b", nargs="+", default=None,
                        help="Score multiple tickers at once")
    parser.add_argument("--rank", action="store_true",
                        help="Rank batch results by score (requires --batch)")
    parser.add_argument("--accuracy", action="store_true",
                        help="Show historical accuracy statistics (model validation)")
    parser.add_argument("--json", "-j", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--ref-only", "-r", action="store_true",
                        help="Only show reference data, no scoring")
    parser.add_argument("--data", "-d", default=None,
                        help="Path to Excel data file (default: auto-detect)")
    return parser


_SIGNAL_TAG = {
    "strongly_bullish": "[HOT]",
    "bullish": "[LONG]",
    "neutral": "[----]",
    "bearish": "[SHORT]",
    "strongly_bearish": "[COLD]",
}


def main() -> None:
    parser = _build_cli()
    args = parser.parse_args()

    scorer = SentimentScorer(data_path=args.data)

    if not scorer.is_ready:
        print(f"WARNING: {scorer.data_error}", file=sys.stderr)
        if not args.ticker and not args.text and not args.batch and not args.accuracy:
            sys.exit(1)

    # --accuracy
    if args.accuracy:
        stats = scorer.get_accuracy_stats()
        if args.json:
            print(json.dumps(stats, ensure_ascii=False, indent=2))
        else:
            _print_accuracy(stats)
        return

    # --text mode
    if args.text:
        score = scorer.score_text(args.text)
        if args.json:
            print(json.dumps({"text": args.text, "score": score}, ensure_ascii=False))
        else:
            print(f"\n  Text: \"{args.text[:80]}{'...' if len(args.text)>80 else ''}\"")
            print(f"  Sentiment score: {score:+.2f}")
        return

    # --batch or single ticker
    tickers = args.batch if args.batch else ([args.ticker] if args.ticker else None)
    if not tickers:
        parser.print_help()
        print("\nERROR: provide a ticker, --text, --batch, or --accuracy", file=sys.stderr)
        sys.exit(1)

    results: list[dict[str, Any]] = []
    for t in tickers:
        if args.ref_only:
            ref = scorer.get_reference_data(t)
            results.append({"ticker": t.upper(), "reference": ref})
        elif args.check:
            results.append(scorer.check_sentiment(t))
        elif args.analyze:
            results.append(scorer.analyze(t))
        else:
            results.append(scorer.get_sentiment_score(t))

    # Ranking (for batch)
    if args.rank and len(results) > 1:
        results.sort(key=lambda r: r.get("score", -999), reverse=True)

    if args.json:
        _print_json(results)
    elif args.check:
        _print_check_results(results, verbose=args.verbose)
    else:
        _print_text(results, verbose=args.verbose, show_llm=args.analyze)


# ======================================================================
# Pretty-print
# ======================================================================


def _print_json(results: list[dict[str, Any]]) -> None:
    print(json.dumps(results, ensure_ascii=False, indent=2))


def _print_check_results(
    results: list[dict[str, Any]], *, verbose: bool = False
) -> None:
    for r in results:
        sig = r.get("signal", "?")
        tag = _SIGNAL_TAG.get(sig, "[?]")
        conf = r.get("confidence", {})
        conf_tag = {"high": "", "medium": " [!]", "low": " [!!]", "none": " [???]"}.get(
            conf.get("level", ""), ""
        )
        overlay = r.get("industry_overlay")

        print(f"\n{'='*60}")
        print(f"  情绪评分报告 — {r['ticker']}")
        print(f"{'='*60}")
        print(f"  {tag} {r['conclusion']}")
        print(f"  操作建议：{r['recommendation']}")
        print(f"  分数：{r['score']:+.2f}  |  信号：{sig}  |  置信度：{conf.get('label', '?')}{conf_tag}")
        if conf.get("reason"):
            print(f"  置信度说明：{conf['reason']}")
        if overlay:
            print(f"  行业情绪叠加：{overlay['bonus']:+.2f} — {overlay['note']}")

        if verbose and r.get("dimensions"):
            print(f"\n  {'─'*50}")
            print(f"  维度得分明细：")
            dims = r["dimensions"]
            active = r.get("active_weights", {})
            dim_names = {
                "subscription_ratio": "认购倍数", "lottery_rate": "中签率",
                "participation": "申购参与度", "sponsor_quality": "保荐人质量",
                "deal_structure": "发行规模与结构",
            }
            for key, d in dims.items():
                name = dim_names.get(key, key)
                aw = active.get(key, 0)
                if d["score"] is not None:
                    rationale = _dim_rationale(key, d["score"])
                    print(f"  {name:<10s}  score={d['score']:+.2f}  "
                          f"w={d['weight']:.0%}->{aw:.0%}  {rationale}  "
                          f"v={_fmt_val(d.get('value'))}")
                else:
                    print(f"  {name:<10s}  (缺失)         "
                          f"w={d['weight']:.0%}->0%  {d.get('detail', '')}")
            print(f"  {'─'*50}")

        if r.get("reference"):
            ref = r["reference"]
            if ref.get("name"):
                print(f"\n  公司：{ref['name']}")
            if ref.get("industry_hs"):
                print(f"  行业：{ref['industry_hs']}")
            if ref.get("sponsors"):
                print(f"  保荐人：{ref['sponsors']}")

    print(f"\n{'='*60}")


def _dim_rationale(key: str, score: float) -> str:
    """Return a one-word natural language description of a dimension score."""
    if key == "subscription_ratio":
        if score >= 0.85: return "极热"
        if score >= 0.70: return "热门"
        if score >= 0.50: return "偏热"
        if score >= 0.20: return "温和"
        if score >= -0.10: return "偏冷"
        return "冷门"
    if key == "lottery_rate":
        if score >= 0.80: return "极低"
        if score >= 0.60: return "很低"
        if score >= 0.30: return "较低"
        if score >= 0.00: return "正常"
        return "偏高"
    if key == "sponsor_quality":
        if score >= 1.00: return "顶级"
        if score >= 0.60: return "一线"
        if score >= 0.00: return "二线"
        return "偏弱"
    if key == "deal_structure":
        if score >= 0.50: return "大盘"
        if score >= 0.00: return "正常"
        return "偏弱"
    if key == "participation":
        if score >= 0.60: return "踊跃"
        if score >= 0.20: return "正常"
        if score >= 0.00: return "清淡"
        return "冷清"
    return ""


def _print_text(
    results: list[dict[str, Any]], *, verbose: bool = False, show_llm: bool = False
) -> None:
    for r in results:
        sig = r.get("signal", "?")
        tag = _SIGNAL_TAG.get(sig, "[?]")
        ref = r.get("reference") or r.get("reference_data")
        sub_str = f"  sub: {ref['subscription_ratio']:.0f}x" if ref and ref.get("subscription_ratio") else ""
        ind_str = f"  [{ref['industry_hs']}]" if ref and ref.get("industry_hs") else ""
        src = r.get("source", "")
        print(f"  {tag} {r['ticker']:<10}  score: {r['score']:+.2f}  {src}{sub_str}{ind_str}")

        if verbose and r.get("dimensions"):
            print(f"  {'─'*50}")
            dims = r["dimensions"]
            active = r.get("active_weights", {})
            dim_names = {
                "subscription_ratio": "认购倍数", "lottery_rate": "中签率",
                "participation": "申购参与度", "sponsor_quality": "保荐人质量",
                "deal_structure": "发行规模与结构",
            }
            for key, d in dims.items():
                name = dim_names.get(key, key)
                aw = active.get(key, 0)
                if d["score"] is not None:
                    rationale = _dim_rationale(key, d["score"])
                    print(f"  {name:<10s}  score={d['score']:+.2f}  "
                          f"w={d['weight']:.0%}->{aw:.0%}  {rationale}  "
                          f"v={_fmt_val(d.get('value'))}")
                else:
                    print(f"  {name:<10s}  (缺失)         w={d['weight']:.0%}->0%")
            print(f"  {'─'*50}")

        if show_llm and r.get("llm_analysis"):
            llm = r["llm_analysis"]
            if llm.get("error"):
                print(f"  LLM error: {llm['error']}")
            elif llm.get("raw_response"):
                text = llm["raw_response"][:200].replace("\n", " ")
                print(f"  LLM: {text}...")


def _print_accuracy(stats: dict[str, Any]) -> None:
    if "error" in stats:
        print(f"  ERROR: {stats['error']}")
        return

    print(f"\n{'='*60}")
    print(f"  情绪模型历史准确率统计")
    print(f"{'='*60}")
    print(f"  验证样本数：{stats['total_validated']}")
    print(f"  与首日收益相关系数：{stats['correlation_with_first_day']:.4f}")
    if stats["correlation_with_first_day"] > 0.3:
        print(f"    评价：正相关显著，模型预测能力良好")
    elif stats["correlation_with_first_day"] > 0.1:
        print(f"    评价：弱正相关，有一定参考价值")
    else:
        print(f"    评价：相关性弱，模型仅供参考")

    dist = stats.get("score_distribution", {})
    print(f"\n  评分分布：均值 {dist.get('mean', '-')}  |  "
          f"中位 {dist.get('median', '-')}  |  "
          f"范围 [{dist.get('min', '-')}, {dist.get('max', '-')}]")

    print(f"\n  {'─'*55}")
    print(f"  按信号分层准确率：")
    print(f"  {'信号':<18s} {'数量':>5s} {'首日均涨幅':>10s} {'上涨率':>8s} {'最佳':>8s} {'最差':>8s}")
    print(f"  {'─'*55}")
    tiers = stats.get("tier_accuracy", {})
    for tier in ["strongly_bullish", "bullish", "neutral", "bearish", "strongly_bearish"]:
        t = tiers.get(tier, {})
        tag = _SIGNAL_TAG.get(tier, "[?]")
        if t.get("count", 0) > 0:
            print(f"  {tag} {tier:<15s} {t['count']:>5d} {t['avg_first_day']:>+9.1f}% "
                  f"{t['up_ratio']:>7.1f}% {t['min_chg']:>+7.1f}% {t['max_chg']:>+7.1f}%")
        else:
            print(f"  {tag} {tier:<15s} {0:>5d}")

    signal_dist = stats.get("signal_distribution", {})
    print(f"\n  信号分布："
          f"HOT={signal_dist.get('strongly_bullish', 0)} | "
          f"LONG={signal_dist.get('bullish', 0)} | "
          f"NEUTRAL={signal_dist.get('neutral', 0)} | "
          f"SHORT={signal_dist.get('bearish', 0)} | "
          f"COLD={signal_dist.get('strongly_bearish', 0)}")
    print(f"{'='*60}")


def _fmt_val(v: Any) -> str:
    if v is None: return "N/A"
    if isinstance(v, float): return f"{v:.2f}"
    if isinstance(v, dict):
        parts = [f"{k}={_fmt_val(vv)}" for k, vv in v.items()]
        return ", ".join(parts)
    return str(v)


if __name__ == "__main__":
    main()
