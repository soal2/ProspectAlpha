"""
Backtest Skill — IPO 上市后表现回测 + 因子分析 + 结论输出
==========================================================

独立运行::

    python backtester.py 6871.HK
    python backtester.py --check 6871.HK
    python backtester.py --factors
    python backtester.py --summary --json
    python backtester.py --timing
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SKILL_DIR = Path(__file__).resolve().parents[1]  # skill5_backtest/
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
# Sponsor tier classification (shared pattern with skill3)
# ======================================================================

_SPONSOR_TIER_1: set[str] = {
    "中国国际金融香港证券有限公司", "高盛(亚洲)有限责任公司",
    "摩根士丹利亚洲有限公司", "摩根大通",
    "J.P. Morgan Securities (Far East) Limited", "J.P. Morgan",
    "Merrill Lynch Far East Limited", "Merrill Lynch",
    "UBS Securities Hong Kong Limited", "UBS",
    "Citigroup Global Markets Asia Limited", "Citigroup", "花旗",
}
_SPONSOR_TIER_2: set[str] = {
    "中信证券(香港)有限公司", "华泰国际", "华泰金融控股(香港)有限公司",
    "海通国际证券有限公司", "海通国际", "国泰君安融资有限公司", "国泰君安",
    "招商证券(香港)有限公司", "招商国际", "Credit Suisse", "瑞士信贷(香港)有限公司",
    "德意志银行", "Deutsche Bank", "申万宏源", "广发证券", "中银国际",
}


def _classify_sponsor(sponsors_str: str | None) -> str:
    """Return 'Tier1', 'Tier2', 'Tier3' based on sponsor names."""
    if not sponsors_str or pd.isna(sponsors_str):
        return "Tier3"
    names = [n.strip() for n in str(sponsors_str).split(",") if n.strip()]
    for name in names:
        if name in _SPONSOR_TIER_1:
            return "Tier1"
    for name in names:
        if name in _SPONSOR_TIER_2:
            return "Tier2"
    return "Tier3"


# Subscription tier classification
def _classify_sub_tier(sub_ratio: float | None) -> str:
    """Classify subscription ratio into tiers."""
    if sub_ratio is None:
        return "未知"
    if sub_ratio >= 1000:
        return ">1000x(极热)"
    if sub_ratio >= 500:
        return "500-1000x"
    if sub_ratio >= 100:
        return "100-500x"
    if sub_ratio >= 50:
        return "50-100x"
    if sub_ratio >= 10:
        return "10-50x"
    if sub_ratio >= 1:
        return "1-10x"
    return "<1x(不足额)"


def _classify_size_tier(raised: float | None) -> str:
    """Classify IPO fundraising size."""
    if raised is None:
        return "未知"
    if raised >= 10000:
        return ">100亿"
    if raised >= 1000:
        return "10-100亿"
    if raised >= 100:
        return "1-10亿"
    return "<1亿"


def _classify_year_regime(deviation: float) -> str:
    """Classify a year as bull/bear/neutral based on deviation from global mean."""
    if deviation > 10:
        return "牛市"
    if deviation > 3:
        return "偏牛"
    if deviation >= -3:
        return "震荡"
    if deviation >= -10:
        return "偏熊"
    return "熊市"


# ======================================================================
# Backtester
# ======================================================================


class Backtester:
    """IPO backtester with first-day scoring, timing analysis, factor
    breakdowns, and conclusion output."""

    DIMENSION_WEIGHTS: dict[str, float] = {
        "first_day": 0.70,
        "timing": 0.30,
    }

    def __init__(self, data_path: str | Path | None = None) -> None:
        self._df: pd.DataFrame | None = None
        self._data_loaded: bool = False
        self._data_error: str | None = None

        self._global_mean_chg: float = 0.0
        self._monthly_benchmark: dict[str, float] = {}
        self._yearly_benchmark: dict[str, float] = {}
        self._monthly_count: dict[str, int] = {}

        if data_path is not None:
            resolved = Path(data_path)
        else:
            resolved = _find_data_file()

        if resolved is None:
            self._data_error = "Excel 数据文件未找到。已搜索: " + ", ".join(str(p) for p in _DATA_CANDIDATES)
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
            "公告日期": self._resolve_column(raw, ["公告日期"]),
            "认购倍数": self._resolve_column(raw, ["认购倍数"]),
            "中签率(%)": self._resolve_column(raw, ["中签率(%)", "中签率"]),
            "所属行业(HS)": self._resolve_column(raw, ["所属行业(HS)", "所属行业"]),
            "保荐人": self._resolve_column(raw, ["保荐人"]),
            "上市首日.涨跌幅(%)": self._resolve_column(raw, ["上市首日.3"]),
            "上市首日.换手率(%)": self._resolve_column(raw, ["上市首日.4"]),
            "上市首日.开盘涨跌幅(%)": self._resolve_column(raw, ["上市首日.5"]),
            "上市首日.收益率(后复权)": self._resolve_column(raw, ["上市首日.2"]),
            "上市首日.振幅(%)": self._resolve_column(raw, ["上市首日.6"]),
            "上市板": self._resolve_column(raw, ["Unnamed: 22"]),
            "实际募资总额(百万港元)": self._resolve_column(raw, ["实际募资总额(百万港元)"]),
        }

        self._build_timing_benchmarks(raw)
        self._data_loaded = True

    # ------------------------------------------------------------------
    # Timing benchmark construction
    # ------------------------------------------------------------------

    def _build_timing_benchmarks(self, raw: pd.DataFrame) -> None:
        date_col = self._col_map["公告日期"]
        if date_col is None:
            return
        dates = pd.to_datetime(raw[date_col], errors="coerce")
        chg_col = self._col_map["上市首日.涨跌幅(%)"]
        if chg_col is None:
            return
        chgs = pd.to_numeric(raw[chg_col], errors="coerce")
        valid = dates.notna() & chgs.notna() & ~chgs.isna()
        d = dates[valid]
        c = chgs[valid]
        self._global_mean_chg = float(c.mean()) if len(c) > 0 else 0.0
        months = d.dt.strftime("%Y-%m")
        for m, grp in c.groupby(months):
            self._monthly_benchmark[m] = round(float(grp.mean()), 2)
            self._monthly_count[m] = len(grp)
        years = d.dt.strftime("%Y")
        for y, grp in c.groupby(years):
            self._yearly_benchmark[y] = round(float(grp.mean()), 2)

    @property
    def is_ready(self) -> bool:
        return self._data_loaded

    @property
    def data_error(self) -> str | None:
        return self._data_error

    # ------------------------------------------------------------------
    # Public API — Check backtest (NEW)
    # ------------------------------------------------------------------

    def check_backtest(self, ticker: str) -> dict[str, Any]:
        """Return a conclusion-style backtest evaluation for a single ticker.

        Combines first-day score + timing score into a weighted total
        with a clear verdict.
        """
        fd = self.score_first_day(ticker)
        td = self._score_timing(ticker)

        if fd is None:
            return {"ticker": ticker.upper(), "has_data": False,
                    "conclusion": "无首日交易数据", "recommendation": "数据不足"}

        fd_score = fd.get("score", 0) or 0
        td_score = td.get("score", 0) if td else 0

        total = round(0.7 * fd_score + 0.3 * td_score, 2)
        signal = self._signal(total)

        # Build conclusion
        conclusions = {
            "strongly_bullish": ("历史首日表现极强，涨幅远超市场均值", "表现优秀"),
            "bullish": ("历史首日表现偏强，涨幅高于市场均值", "表现良好"),
            "neutral": ("历史首日表现中规中矩，接近市场平均", "表现一般"),
            "bearish": ("历史首日表现偏弱，涨幅低于市场均值", "表现较差"),
            "strongly_bearish": ("历史首日表现很弱，跌幅远超市场均值", "表现差"),
        }
        conclusion, recommendation = conclusions.get(signal, ("", ""))

        return {
            "ticker": ticker.upper(),
            "has_data": True,
            "conclusion": conclusion,
            "recommendation": recommendation,
            "score": total,
            "signal": signal,
            "first_day": fd,
            "timing": td,
        }

    # ------------------------------------------------------------------
    # Public API — Factor analysis (NEW)
    # ------------------------------------------------------------------

    def factor_analysis(self) -> dict[str, Any]:
        """Analyze which factors correlate with first-day returns.

        Returns breakdowns by: subscription tier, sponsor tier, board,
        fundraising size, and industry.
        """
        if not self._data_loaded or self._df is None:
            return {"error": "数据未加载"}

        # Collect all tickers with first-day data
        records: list[dict[str, Any]] = []
        code_col = self._col_map["代码"]
        for _, rec in self._df.iterrows():
            ticker = str(rec[code_col]).strip()
            if not ticker:
                continue
            chg = self._parse_float(rec, "上市首日.涨跌幅(%)")
            if chg is None:
                continue
            sub = self._parse_float(rec, "认购倍数")
            sponsors = self._parse_str(rec, "保荐人")
            board = self._parse_str(rec, "上市板")
            industry = self._parse_str(rec, "所属行业(HS)")
            raised = self._parse_float(rec, "实际募资总额(百万港元)")
            records.append({
                "ticker": ticker,
                "chg": chg,
                "sub_ratio": sub,
                "sub_tier": _classify_sub_tier(sub),
                "sponsor_tier": _classify_sponsor(sponsors),
                "board": "主板" if board and "主板" in str(board) else ("GEM" if board else "未知"),
                "industry": industry or "未知",
                "raised": raised,
                "size_tier": _classify_size_tier(raised),
            })

        if not records:
            return {"error": "无可用数据"}

        def _calc_group(group: list[dict[str, Any]]) -> dict[str, Any]:
            chgs = [r["chg"] for r in group]
            up = sum(1 for c in chgs if c > 0)
            return {
                "count": len(group),
                "avg_chg": round(sum(chgs) / len(chgs), 2),
                "median_chg": round(sorted(chgs)[len(chgs) // 2], 2),
                "up_ratio": round(up / len(chgs) * 100, 1),
                "min_chg": round(min(chgs), 2),
                "max_chg": round(max(chgs), 2),
            }

        # 1. Subscription tier
        sub_tiers: dict[str, Any] = {}
        for r in records:
            t = r["sub_tier"]
            if t not in sub_tiers:
                sub_tiers[t] = []
            sub_tiers[t].append(r)
        SUB_TIER_ORDER = ["<1x(不足额)", "1-10x", "10-50x", "50-100x", "100-500x", "500-1000x", ">1000x(极热)"]
        sub_breakdown = {t: _calc_group(sub_tiers[t]) for t in SUB_TIER_ORDER if t in sub_tiers}

        # 2. Sponsor tier
        sponsor_groups: dict[str, list] = defaultdict(list)
        for r in records:
            sponsor_groups[r["sponsor_tier"]].append(r)
        sponsor_breakdown = {t: _calc_group(sponsor_groups[t]) for t in ["Tier1", "Tier2", "Tier3"] if t in sponsor_groups}

        # 3. Board
        board_groups: dict[str, list] = defaultdict(list)
        for r in records:
            board_groups[r["board"]].append(r)
        board_breakdown = {b: _calc_group(board_groups[b]) for b in sorted(board_groups)}

        # 4. Industry
        industry_groups: dict[str, list] = defaultdict(list)
        for r in records:
            industry_groups[r["industry"]].append(r)
        industry_breakdown = {
            ind: _calc_group(grp)
            for ind, grp in sorted(industry_groups.items(), key=lambda kv: len(kv[1]), reverse=True)
        }

        # 5. Size tier
        size_groups: dict[str, list] = defaultdict(list)
        for r in records:
            if r["raised"] is not None:
                size_groups[r["size_tier"]].append(r)
        SIZE_ORDER = [">100亿", "10-100亿", "1-10亿", "<1亿"]
        size_breakdown = {t: _calc_group(size_groups[t]) for t in SIZE_ORDER if t in size_groups}

        # 6. Year regime
        year_groups: dict[str, list] = defaultdict(list)
        for r in records:
            # Find year from listing date via col_map
            pass  # Use timing benchmarks instead
        year_regimes: dict[str, dict[str, Any]] = {}
        for yk, avg in self._yearly_benchmark.items():
            dev = avg - self._global_mean_chg
            regime = _classify_year_regime(dev)
            if regime not in year_regimes:
                year_regimes[regime] = {"years": [], "count": 0}
            year_regimes[regime]["years"].append(yk)
        for regime in year_regimes:
            year_regimes[regime]["years_str"] = ", ".join(year_regimes[regime]["years"])

        return {
            "total_records": len(records),
            "global_mean_chg": round(self._global_mean_chg, 2),
            "subscription_tier": sub_breakdown,
            "sponsor_tier": sponsor_breakdown,
            "board": board_breakdown,
            "industry": industry_breakdown,
            "size_tier": size_breakdown,
            "year_regimes": year_regimes,
        }

    # ------------------------------------------------------------------
    # Public API — Industry comparison (NEW)
    # ------------------------------------------------------------------

    def industry_comparison(self) -> dict[str, Any]:
        """Return per-industry comparison of first-day performance."""
        fa = self.factor_analysis()
        if "error" in fa:
            return fa
        industries = fa.get("industry", {})
        # Sort by avg_chg descending
        sorted_inds = sorted(industries.items(), key=lambda kv: kv[1]["avg_chg"], reverse=True)
        return {
            "global_mean": round(self._global_mean_chg, 2),
            "industries": [
                {"industry": ind, **stats}
                for ind, stats in sorted_inds
            ],
            "best": sorted_inds[0][0] if sorted_inds else "",
            "worst": sorted_inds[-1][0] if sorted_inds else "",
        }

    # ------------------------------------------------------------------
    # Timing / regime
    # ------------------------------------------------------------------

    def _get_listing_date(self, rec: Any) -> pd.Timestamp | None:
        date_col = self._col_map["公告日期"]
        if date_col is None:
            return None
        val = rec.get(date_col)
        if pd.isna(val) or str(val).strip() == "":
            return None
        try:
            ts = pd.Timestamp(val)
            return None if pd.isna(ts) else ts
        except (ValueError, TypeError):
            return None

    def get_timing_data(self, ticker: str) -> dict[str, Any] | None:
        if not self._data_loaded or self._df is None:
            return None
        code_col = self._col_map["代码"]
        row = self._df[self._df[code_col] == ticker.upper().strip()]
        if row.empty:
            return None
        rec = row.iloc[0]
        ts = self._get_listing_date(rec)
        if ts is None:
            return {"ticker": ticker.upper(), "status": "no_date", "detail": "无上市日期数据"}
        month_key = ts.strftime("%Y-%m")
        year_key = ts.strftime("%Y")
        month_mean = self._monthly_benchmark.get(month_key)
        year_mean = self._yearly_benchmark.get(year_key)
        return {
            "ticker": ticker.upper(),
            "listing_date": ts.strftime("%Y-%m-%d"),
            "listing_month": month_key,
            "listing_year": year_key,
            "monthly_avg_chg": month_mean,
            "yearly_avg_chg": year_mean,
            "global_avg_chg": round(self._global_mean_chg, 2),
            "month_deviation": round(month_mean - self._global_mean_chg, 2) if month_mean is not None else None,
            "year_deviation": round(year_mean - self._global_mean_chg, 2) if year_mean is not None else None,
            "monthly_sample_size": self._monthly_count.get(month_key, 0),
            "year_regime": _classify_year_regime(year_mean - self._global_mean_chg) if year_mean is not None else "未知",
            "status": "ok",
        }

    def _score_timing(self, ticker: str) -> dict[str, Any] | None:
        td = self.get_timing_data(ticker)
        if td is None or td.get("status") != "ok":
            return None
        month_score = _score_deviation(td.get("month_deviation"))
        year_score = _score_deviation(td.get("year_deviation"))
        return {
            "score": round(0.6 * month_score + 0.4 * year_score, 2),
            "weight": self.DIMENSION_WEIGHTS["timing"],
            "sub_score_month": month_score,
            "sub_score_year": year_score,
            "month_deviation": td.get("month_deviation"),
            "year_deviation": td.get("year_deviation"),
            "listing_month": td.get("listing_month"),
            "year_regime": td.get("year_regime"),
            "status": "ok",
        }

    def timing_analysis(self) -> dict[str, Any]:
        sorted_months = sorted(self._monthly_benchmark.items(), key=lambda kv: kv[1], reverse=True)
        sorted_years = sorted(self._yearly_benchmark.items(), key=lambda kv: kv[1], reverse=True)
        hot_months = [(m, v) for m, v in sorted_months if v - self._global_mean_chg > 10]
        cold_months = [(m, v) for m, v in sorted_months if v - self._global_mean_chg < -10]
        hot_years = [(y, v) for y, v in sorted_years if v - self._global_mean_chg > 5]
        cold_years = [(y, v) for y, v in sorted_years if v - self._global_mean_chg < -5]
        return {
            "global_mean_chg": round(self._global_mean_chg, 2),
            "total_months": len(self._monthly_benchmark),
            "total_years": len(self._yearly_benchmark),
            "hottest_months": [{"month": m, "avg_chg": v, "sample_size": self._monthly_count.get(m, 0)} for m, v in hot_months],
            "coldest_months": [{"month": m, "avg_chg": v, "sample_size": self._monthly_count.get(m, 0)} for m, v in cold_months],
            "yearly_breakdown": [{"year": y, "avg_chg": v, "regime": _classify_year_regime(v - self._global_mean_chg)} for y, v in sorted_years],
            "full_monthly": [{"month": m, "avg_chg": v, "count": self._monthly_count.get(m, 0)} for m, v in sorted_months],
        }

    # ------------------------------------------------------------------
    # Single-ticker scoring
    # ------------------------------------------------------------------

    def score_first_day(self, ticker: str) -> dict[str, Any] | None:
        if not self._data_loaded or self._df is None:
            return None
        code_col = self._col_map["代码"]
        row = self._df[self._df[code_col] == ticker.upper().strip()]
        if row.empty:
            return None
        rec = row.iloc[0]
        chg = self._parse_float(rec, "上市首日.涨跌幅(%)")
        turnover = self._parse_float(rec, "上市首日.换手率(%)")
        open_chg = self._parse_float(rec, "上市首日.开盘涨跌幅(%)")
        amplitude = self._parse_float(rec, "上市首日.振幅(%)")
        if chg is None and turnover is None:
            return {"ticker": ticker.upper(), "score": None, "status": "no_data", "detail": "无首日交易数据"}
        sub_score = self._score_price_change(chg)
        turnover_score = self._score_turnover(turnover)
        final = round(0.7 * sub_score + 0.3 * turnover_score, 2) if chg is not None and turnover is not None else (turnover_score if chg is None else sub_score)
        return {
            "ticker": ticker.upper(),
            "name": self._parse_str(rec, "名称"),
            "score": final,
            "signal": self._signal(final),
            "first_day_chg": chg,
            "first_day_turnover": turnover,
            "first_day_open_chg": open_chg,
            "first_day_amplitude": amplitude,
            "sub_score_chg": sub_score,
            "sub_score_turnover": turnover_score,
            "status": "ok",
        }

    def get_first_day_data(self, ticker: str) -> dict[str, Any] | None:
        if not self._data_loaded or self._df is None:
            return None
        code_col = self._col_map["代码"]
        row = self._df[self._df[code_col] == ticker.upper().strip()]
        if row.empty:
            return None
        rec = row.iloc[0]
        return {
            "ticker": ticker.upper(),
            "name": self._parse_str(rec, "名称"),
            "listing_date": self._get_listing_date(rec).strftime("%Y-%m-%d") if self._get_listing_date(rec) else None,
            "subscription_ratio": self._parse_float(rec, "认购倍数"),
            "lottery_rate": self._parse_float(rec, "中签率(%)"),
            "industry": self._parse_str(rec, "所属行业(HS)"),
            "board": self._parse_str(rec, "上市板"),
            "first_day_chg": self._parse_float(rec, "上市首日.涨跌幅(%)"),
            "first_day_turnover": self._parse_float(rec, "上市首日.换手率(%)"),
            "first_day_open_chg": self._parse_float(rec, "上市首日.开盘涨跌幅(%)"),
            "first_day_return": self._parse_float(rec, "上市首日.收益率(后复权)"),
            "first_day_amplitude": self._parse_float(rec, "上市首日.振幅(%)"),
        }

    def get_all_with_first_day(self, industry: str | None = None) -> list[dict[str, Any]]:
        if not self._data_loaded or self._df is None:
            return []
        code_col = self._col_map["代码"]
        results: list[dict[str, Any]] = []
        for _, rec in self._df.iterrows():
            ticker = str(rec[code_col]).strip()
            if not ticker:
                continue
            if industry:
                ind = self._parse_str(rec, "所属行业(HS)")
                if not ind or industry not in ind:
                    continue
            chg = self._parse_float(rec, "上市首日.涨跌幅(%)")
            turnover = self._parse_float(rec, "上市首日.换手率(%)")
            if chg is None and turnover is None:
                continue
            sub_score = self._score_price_change(chg)
            t_score = self._score_turnover(turnover)
            final = round(0.7 * sub_score + 0.3 * t_score, 2) if chg is not None and turnover is not None else (t_score if chg is None else sub_score)
            ts = self._get_listing_date(rec)
            timing = {}
            if ts is not None:
                mk = ts.strftime("%Y-%m")
                timing = {"listing_date": ts.strftime("%Y-%m-%d"), "listing_month": mk,
                          "monthly_avg_chg": self._monthly_benchmark.get(mk),
                          "month_deviation": round((self._monthly_benchmark.get(mk) or 0) - self._global_mean_chg, 2) if mk in self._monthly_benchmark else None}
            results.append({"ticker": ticker, "name": self._parse_str(rec, "名称"),
                           "score": final, "signal": self._signal(final),
                           "first_day_chg": chg, "first_day_turnover": turnover,
                           "sub_ratio": self._parse_float(rec, "认购倍数"), **timing})
        results.sort(key=lambda r: r["score"] or -999, reverse=True)
        return results

    def run_backtest(self, industry: str | None = None) -> dict[str, Any]:
        data = self.get_all_with_first_day(industry=industry)
        if not data:
            return {"error": "无可用首日数据", "count": 0}

        def _ok(v): return v is not None and not (isinstance(v, float) and math.isnan(v))
        scores = [d["score"] for d in data if _ok(d["score"])]
        chgs = [d["first_day_chg"] for d in data if _ok(d["first_day_chg"])]
        turnovers = [d["first_day_turnover"] for d in data if _ok(d["first_day_turnover"])]
        if not scores:
            return {"error": "无有效评分", "count": len(data)}

        signal_counts: dict[str, int] = defaultdict(int)
        for d in data:
            signal_counts[d.get("signal", "unknown")] += 1
        up = sum(1 for c in chgs if c > 0)
        down = sum(1 for c in chgs if c < 0)
        sorted_by_chg = sorted([d for d in data if d["first_day_chg"] is not None], key=lambda d: d["first_day_chg"], reverse=True)
        return {
            "filter": industry or "全部行业",
            "total_with_data": len(data),
            "score_distribution": {"min": round(min(scores), 2), "max": round(max(scores), 2),
                                   "mean": round(sum(scores) / len(scores), 2),
                                   "median": round(sorted(scores)[len(scores) // 2], 2)},
            "first_day_chg_distribution": {
                "up_pct": round(up / max(len(chgs), 1) * 100, 1),
                "flat_pct": round((len(chgs) - up - down) / max(len(chgs), 1) * 100, 1),
                "down_pct": round(down / max(len(chgs), 1) * 100, 1),
                "mean": round(sum(chgs) / max(len(chgs), 1), 2),
                "median": round(sorted(chgs)[len(chgs) // 2], 2) if chgs else None,
                "min": round(min(chgs), 2) if chgs else None,
                "max": round(max(chgs), 2) if chgs else None,
            },
            "turnover_distribution": {"mean": round(sum(turnovers) / max(len(turnovers), 1), 2),
                                      "median": round(sorted(turnovers)[len(turnovers) // 2], 2) if turnovers else None} if turnovers else {},
            "signal_distribution": dict(signal_counts),
            "tier_breakdown": self._tier_breakdown(data),
            "timing_regime": self._timing_breakdown(data),
            "top_5": [{"ticker": d["ticker"], "name": d.get("name"), "chg": d["first_day_chg"], "score": d["score"]} for d in sorted_by_chg[:5]],
            "bottom_5": [{"ticker": d["ticker"], "name": d.get("name"), "chg": d["first_day_chg"], "score": d["score"]} for d in sorted_by_chg[-5:]],
        }

    # ------------------------------------------------------------------
    # Scoring rules
    # ------------------------------------------------------------------

    @staticmethod
    def _score_price_change(chg: float | None) -> float:
        if chg is None: return 0.0
        if chg > 100: return 1.00
        if chg >= 50: return 0.85
        if chg >= 20: return 0.60
        if chg > 0: return 0.30
        if chg == 0: return 0.00
        if chg >= -10: return -0.30
        if chg >= -30: return -0.50
        return -0.80

    @staticmethod
    def _score_turnover(turnover: float | None) -> float:
        if turnover is None: return 0.0
        if 10 <= turnover <= 50: return 0.40
        if 5 <= turnover < 10: return 0.10
        if 50 < turnover <= 80: return 0.00
        if 2 <= turnover < 5: return -0.20
        if turnover < 2: return -0.50
        return -0.30

    @staticmethod
    def _signal(score: float) -> str:
        if score > 0.50: return "strongly_bullish"
        if score > 0.25: return "bullish"
        if score >= -0.25: return "neutral"
        if score >= -0.50: return "bearish"
        return "strongly_bearish"

    # ------------------------------------------------------------------
    # Timing internals
    # ------------------------------------------------------------------

    def _timing_breakdown(self, data: list[dict[str, Any]]) -> dict[str, Any]:
        month_groups: dict[str, list[float]] = defaultdict(list)
        for d in data:
            mk = d.get("listing_month")
            if mk and d["first_day_chg"] is not None:
                month_groups[mk].append(d["first_day_chg"])
        month_stats = [{"month": mk, "count": len(vals), "avg_chg": round(sum(vals) / len(vals), 2),
                        "min_chg": round(min(vals), 2), "max_chg": round(max(vals), 2),
                        "deviation": round((sum(vals) / len(vals)) - self._global_mean_chg, 2)}
                       for mk, vals in sorted(month_groups.items())]
        year_groups: dict[str, list[float]] = defaultdict(list)
        for d in data:
            mk = d.get("listing_month")
            if mk and d["first_day_chg"] is not None:
                year_groups[mk[:4]].append(d["first_day_chg"])
        year_stats = [{"year": yk, "count": len(vals), "avg_chg": round(sum(vals) / len(vals), 2),
                       "deviation": round((sum(vals) / len(vals)) - self._global_mean_chg, 2),
                       "regime": _classify_year_regime((sum(vals) / len(vals)) - self._global_mean_chg)}
                      for yk, vals in sorted(year_groups.items())]
        return {"global_avg_chg": round(self._global_mean_chg, 2), "by_month": month_stats, "by_year": year_stats}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_float(self, rec: Any, key: str) -> float | None:
        col = self._col_map.get(key)
        if col is None: return None
        try:
            v = float(rec[col])
            return None if math.isnan(v) else v
        except (ValueError, TypeError):
            return None

    def _parse_str(self, rec: Any, key: str) -> str | None:
        col = self._col_map.get(key)
        if col is None: return None
        val = rec.get(col)
        if pd.isna(val) or str(val).strip() == "": return None
        return str(val).strip()

    @staticmethod
    def _resolve_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
        for c in candidates:
            if c in df.columns: return c
        return None

    @staticmethod
    def _tier_breakdown(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tiers = [("strongly_bullish", "> 0.50", 0.50, 999), ("bullish", "0.25 ~ 0.50", 0.25, 0.50),
                 ("neutral", "-0.25 ~ 0.25", -0.25, 0.25), ("bearish", "-0.50 ~ -0.25", -0.50, -0.25),
                 ("strongly_bearish", "< -0.50", -999, -0.50)]
        result = []
        for signal, label, lo, hi in tiers:
            subset = [d for d in data if d["score"] is not None and lo <= d["score"] < hi]
            if not subset:
                result.append({"signal": signal, "range": label, "count": 0})
                continue
            chgs_in_tier = [d["first_day_chg"] for d in subset if d["first_day_chg"] is not None and not (isinstance(d["first_day_chg"], float) and math.isnan(d["first_day_chg"]))]
            up_in_tier = sum(1 for c in chgs_in_tier if c > 0)
            result.append({"signal": signal, "range": label, "count": len(subset),
                           "avg_chg": round(sum(chgs_in_tier) / max(len(chgs_in_tier), 1), 2) if chgs_in_tier else 0,
                           "up_ratio": round(up_in_tier / max(len(chgs_in_tier), 1) * 100, 1) if chgs_in_tier else 0})
        return result


# ======================================================================
# Helpers
# ======================================================================

def _score_deviation(deviation: float | None) -> float:
    if deviation is None: return 0.0
    if deviation > 10: return 0.30
    if deviation > 3: return 0.10
    if deviation >= -3: return 0.00
    if deviation >= -10: return -0.10
    return -0.30


# ======================================================================
# CLI
# ======================================================================

def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtester — IPO 上市后表现回测 + 因子分析 + 结论输出")
    parser.add_argument("ticker", nargs="?", default=None, help="Stock ticker (e.g. 6871.HK)")
    parser.add_argument("--check", action="store_true", help="Conclusion-style output for a ticker")
    parser.add_argument("--batch", "-b", nargs="+", default=None, help="Score multiple tickers")
    parser.add_argument("--industry", "-i", default=None, help="Filter by industry")
    parser.add_argument("--all", "-a", action="store_true", help="List all tickers with first-day data")
    parser.add_argument("--summary", "-s", action="store_true", help="Full backtest summary")
    parser.add_argument("--timing", action="store_true", help="Timing heatmap")
    parser.add_argument("--timing-score", default=None, metavar="TICKER", help="Score timing for a ticker")
    parser.add_argument("--factors", action="store_true", help="Factor analysis (subscription, sponsor, board, industry, size)")
    parser.add_argument("--limit", "-n", type=int, default=30, help="Max rows for --all")
    parser.add_argument("--json", "-j", action="store_true", help="JSON output")
    parser.add_argument("--data", "-d", default=None, help="Excel data file path")
    return parser


_SIGNAL_TAG = {"strongly_bullish": "[HOT]", "bullish": "[LONG]", "neutral": "[----]", "bearish": "[SHORT]", "strongly_bearish": "[COLD]"}


def main() -> None:
    parser = _build_cli()
    args = parser.parse_args()
    bt = Backtester(data_path=args.data)
    if not bt.is_ready:
        print(f"ERROR: {bt.data_error}", file=sys.stderr)
        sys.exit(1)

    # --factors
    if args.factors:
        fa = bt.factor_analysis()
        if args.json:
            print(json.dumps(fa, ensure_ascii=False, indent=2))
        else:
            _print_factors(fa)
        return

    if args.timing:
        ta = bt.timing_analysis()
        if args.json:
            print(json.dumps(ta, ensure_ascii=False, indent=2))
        else:
            _print_timing(ta)
        return

    if args.timing_score:
        ts = bt._score_timing(args.timing_score)
        if ts is None:
            td = bt.get_timing_data(args.timing_score)
            print(f"  {args.timing_score}: {td.get('detail', '无法评分')}")
        elif args.json:
            print(json.dumps(ts, ensure_ascii=False, indent=2))
        else:
            print(f"\n  {args.timing_score}  |  上市月: {ts['listing_month']}  |  年环境: {ts.get('year_regime', '?')}")
            print(f"  月评分: {ts['sub_score_month']:+.2f}  |  年评分: {ts['sub_score_year']:+.2f}  |  综合: {ts['score']:+.2f}")
        return

    if args.summary:
        result = bt.run_backtest(industry=args.industry)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_summary(result)
        return

    if args.all:
        data = bt.get_all_with_first_day(industry=args.industry)
        if args.json:
            print(json.dumps(data[:args.limit], ensure_ascii=False, indent=2))
        else:
            _print_batch(data[:args.limit], show_count=len(data))
        return

    tickers = args.batch if args.batch else ([args.ticker] if args.ticker else None)
    if not tickers:
        parser.print_help()
        print("\nERROR: provide a ticker, --all, --summary, --factors, or --timing", file=sys.stderr)
        sys.exit(1)

    if args.check:
        results = [bt.check_backtest(t) for t in tickers]
    else:
        results = [r for t in tickers if (r := bt.score_first_day(t))]

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif args.check:
        _print_check_results(results)
    else:
        _print_batch(results, show_count=None)


# ======================================================================
# Pretty-print
# ======================================================================

def _print_check_results(results: list[dict[str, Any]]) -> None:
    for r in results:
        tag = _SIGNAL_TAG.get(r.get("signal", "?"), "[?]")
        fd = r.get("first_day", {})
        td = r.get("timing", {})
        print(f"\n{'='*50}")
        print(f"  回测结论 — {r['ticker']}")
        print(f"{'='*50}")
        print(f"  {tag} {r['conclusion']}")
        print(f"  综合评价：{r['recommendation']}")
        print(f"  综合评分：{r['score']:+.2f}  |  信号：{r['signal']}")
        if fd:
            print(f"\n  首日表现：涨跌 {fd.get('first_day_chg', 'N/A')}%  |  "
                  f"换手率 {fd.get('first_day_turnover', 'N/A')}%")
        if td:
            print(f"  择时环境：上市月={td.get('listing_month', '?')}  |  "
                  f"年环境={td.get('year_regime', '?')}")


def _print_factors(fa: dict[str, Any]) -> None:
    if "error" in fa:
        print(f"  ERROR: {fa['error']}")
        return
    print(f"\n{'='*60}")
    print(f"  IPO 因子分析  |  全局首日均值: {fa['global_mean_chg']:.1f}%  |  {fa['total_records']} 条记录")
    print(f"{'='*60}")

    # Subscription tier
    st = fa.get("subscription_tier", {})
    if st:
        print(f"\n  ── 认购倍数分层 ──")
        print(f"  {'倍数区间':<16s} {'数量':>5s} {'均涨幅':>8s} {'上涨率':>7s} {'范围':>14s}")
        for tier in ["<1x(不足额)", "1-10x", "10-50x", "50-100x", "100-500x", "500-1000x", ">1000x(极热)"]:
            if tier in st:
                s = st[tier]
                print(f"  {tier:<16s} {s['count']:>5d} {s['avg_chg']:>+7.1f}% {s['up_ratio']:>6.1f}%  [{s['min_chg']:+.0f}, {s['max_chg']:+.0f}]")

    # Sponsor tier
    sp = fa.get("sponsor_tier", {})
    if sp:
        print(f"\n  ── 保荐人等级 ──")
        for tier in ["Tier1", "Tier2", "Tier3"]:
            if tier in sp:
                s = sp[tier]
                label = {"Tier1": "顶级国际投行", "Tier2": "一线券商", "Tier3": "二线/其他"}[tier]
                print(f"  {label:<14s} {s['count']:>5d} 只  |  均涨幅 {s['avg_chg']:>+7.1f}%  |  上涨率 {s['up_ratio']:>5.1f}%")

    # Board
    bd = fa.get("board", {})
    if bd:
        print(f"\n  ── 上市板对比 ──")
        for board, s in bd.items():
            print(f"  {board:<6s} {s['count']:>5d} 只  |  均涨幅 {s['avg_chg']:>+7.1f}%  |  上涨率 {s['up_ratio']:>5.1f}%")

    # Top/bottom industries
    inds = fa.get("industry", {})
    if inds:
        sorted_i = sorted(inds.items(), key=lambda kv: kv[1]["avg_chg"], reverse=True)
        print(f"\n  ── 行业 Top 5 / Bottom 5 ──")
        for ind, s in sorted_i[:5]:
            print(f"  [HOT] {ind:<20s} {s['count']:>4d} 只  |  均涨幅 {s['avg_chg']:>+7.1f}%")
        if len(sorted_i) > 10:
            print(f"  ...")
        for ind, s in sorted_i[-5:]:
            print(f"  [COLD] {ind:<20s} {s['count']:>4d} 只  |  均涨幅 {s['avg_chg']:>+7.1f}%")


def _print_timing(ta: dict[str, Any]) -> None:
    gm = ta["global_mean_chg"]
    print(f"\n{'='*60}")
    print(f"  时序环境分析  |  全局首日平均涨幅: {gm:.1f}%")
    print(f"  覆盖 {ta['total_months']} 个月, {ta['total_years']} 个年份")
    print(f"{'='*60}")
    print(f"\n  ── 最热月份（偏差 > +10%）──")
    for m in ta.get("hottest_months", []):
        print(f"    {m['month']}  avg={m['avg_chg']:+.1f}%  偏差={m['avg_chg'] - gm:+.1f}%  n={m['sample_size']}")
    print(f"\n  ── 最冷月份（偏差 < -10%）──")
    for m in ta.get("coldest_months", []):
        print(f"    {m['month']}  avg={m['avg_chg']:+.1f}%  偏差={m['avg_chg'] - gm:+.1f}%  n={m['sample_size']}")
    print(f"\n  ── 年份总览 ──")
    for y in ta.get("yearly_breakdown", []):
        dev = y["avg_chg"] - gm
        regime = y.get("regime", "")
        print(f"  {y['year']}  avg={y['avg_chg']:>+7.1f}%  vs全局={dev:>+7.1f}%  [{regime}]")


def _print_summary(s: dict[str, Any]) -> None:
    if "error" in s:
        print(f"  ERROR: {s['error']}")
        return
    print(f"\n{'='*60}")
    print(f"  回测报告  |  行业筛选: {s.get('filter', '全部')}")
    print(f"{'='*60}")
    print(f"  有效样本: {s['total_with_data']}")
    dist = s.get("score_distribution", {})
    print(f"  首日评分: 均值 {dist.get('mean', '-')}  中位 {dist.get('median', '-')}  范围 [{dist.get('min', '-')}, {dist.get('max', '-')}]")
    chg_d = s.get("first_day_chg_distribution", {})
    print(f"  首日涨跌幅: 均值 {chg_d.get('mean', '-')}%  中位 {chg_d.get('median', '-')}%")
    print(f"  上涨 {chg_d.get('up_pct', '-')}%  |  平盘 {chg_d.get('flat_pct', '-')}%  |  下跌 {chg_d.get('down_pct', '-')}%")
    print(f"\n  {'─'*40}")
    for tier in s.get("tier_breakdown", []):
        print(f"  {tier['signal']:<18s} {tier['count']:>5d}  {tier['avg_chg']:>+7.1f}%  {tier['up_ratio']:>5.1f}%")
    top = s.get("top_5", [])
    print(f"\n  Top 5: " + ", ".join(f"{d['ticker']}({d['chg']:+.0f}%)" for d in top))
    bottom = s.get("bottom_5", [])
    print(f"  Bottom 5: " + ", ".join(f"{d['ticker']}({d['chg']:+.0f}%)" for d in bottom))


def _print_batch(results: list[dict[str, Any]], *, show_count: int | None) -> None:
    if show_count is not None:
        print(f"\n  共 {show_count} 条记录（显示前 {len(results)} 条）\n")
    for r in results:
        tag = _SIGNAL_TAG.get(r.get("signal", "?"), "[?]")
        chg = r.get("first_day_chg")
        chg_s = f"{chg:+.1f}%" if chg is not None else "N/A"
        sub_s = f"sub={r.get('sub_ratio',''):.0f}x" if r.get("sub_ratio") else ""
        print(f"  {tag} {r['ticker']:<12s}  score={r['score']:+.2f}  chg={chg_s:<8s}  {sub_s}  {r.get('name', '')}")


if __name__ == "__main__":
    main()
