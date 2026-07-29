"""
Cashflow Analyzer Skill — 现金流可持续性 + 10 维度风险扫描
==========================================================

独立运行::

    python analyzer.py --check
    python analyzer.py --check --industry saas
    python analyzer.py --check --healthy
    python analyzer.py --risk-scan --json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

# ======================================================================
# Risk indicator definitions (10 indicators, R0-R9)
# ======================================================================

_RISK_DEFINITIONS: list[dict[str, str]] = [
    # R0 — 经营造血能力
    {
        "id": "neg_op_cf",
        "dimension": "现金流",
        "indicator": "经营活动现金流量净额持续为负",
        "description": "经营活动无法产生正向现金流，公司处于「失血」状态，经营模式不可持续。同时评估趋势方向和OpCF/营收比率。",
        "threshold": "经营CF < 0 且连续2期以上；OpCF/营收 < 5%",
        "formula": "经营现金流净额（直接从现金流量表读取）；趋势方向 = 多期序列回归斜率；OpCF/营收比率 = 经营CF / 营业收入",
        "data_source": "现金流量表 / 利润表",
    },
    # R1 — 盈利质量
    {
        "id": "cf_ni_divergence",
        "dimension": "现金流",
        "indicator": "现金流与净利润严重背离",
        "description": "净利润为正但经营现金流为负，或营收高增长而经营CF反降，盈利质量存疑（纸面利润风险）。",
        "threshold": "(经营CF / 净利润) < 0.5 且连续2期以上；或营收增速>15%但OpCF增速<5%",
        "formula": "现金流净利比 = 经营活动现金流量净额 / 净利润；营收-CF背离 = (营收增速 > 15%) AND (OpCF增速 < 5%)",
        "data_source": "现金流量表 + 综合损益表",
    },
    # R2 — 自由现金流
    {
        "id": "neg_fcf",
        "dimension": "现金流",
        "indicator": "自由现金流持续为负",
        "description": "扣除资本支出后自由现金流为负，公司缺乏分红和再投资能力。评估FCF趋势方向和FCF/营收比率。",
        "threshold": "FCF < 0 且连续3期以上",
        "formula": "自由现金流(FCF) = 经营现金流净额 - |资本支出|；FCF/营收比率 = FCF / 营业收入",
        "data_source": "现金流量表",
    },
    # R3 — 资本支出合理性（合并原 R3+R4）
    {
        "id": "capex_ratio",
        "dimension": "现金流",
        "indicator": "资本支出与投资强度异常",
        "description": "资本支出远超经营现金流或投资强度过大，投资扩张过度依赖外部融资。区分维持性vs扩张性资本支出。",
        "threshold": "资本支出/经营CF > 2.0；|投资CF|/总资产 > 15%；扩张性Capex/OpCF > 1.5",
        "formula": "资本支出比 = |资本支出| / 经营CF；投资强度 = |投资CF| / 总资产 × 100%；扩张性Capex = |总Capex| - 折旧",
        "data_source": "现金流量表 / 资产负债表",
    },
    # R4 — 融资依赖度（原 R5）
    {
        "id": "financing_dependency",
        "dimension": "现金流",
        "indicator": "筹资活动现金流占比过高",
        "description": "严重依赖筹资活动维持现金流，经营造血能力不足。与行业基准比较偏离幅度。",
        "threshold": "筹资活动现金流净额 / 期末现金余额 > 1.5",
        "formula": "筹资依赖度 = 筹资活动现金流量净额 / 期末现金及等价物",
        "data_source": "现金流量表 / 资产负债表",
    },
    # R5 — 现金跑道（原 R6 + 敏感性）
    {
        "id": "burn_rate",
        "dimension": "现金流",
        "indicator": "现金消耗率过高（Burn Rate）",
        "description": "对于未盈利企业，月度现金消耗导致现金跑道不足。附带敏感性分析（乐观/基准/悲观三种情景）。",
        "threshold": "现金跑道 < 12个月（预警）、< 24个月（关注）",
        "formula": "月度现金消耗 = |经营CF + 投资CF| / 期间月数；现金跑道 = 现金及等价物 / 月度现金消耗",
        "data_source": "现金流量表 / 资产负债表",
    },
    # R6 — 现金流结构（NEW）
    {
        "id": "cf_structure",
        "dimension": "现金流",
        "indicator": "现金流结构失衡",
        "description": "三大现金流（经营/投资/筹资）来源占比失衡。经营CF占比过低则造血能力不足，筹资CF占比过高则依赖外部输血。",
        "threshold": "经营CF占比 < 30%（预警）；筹资CF占比 > 60%（预警）",
        "formula": "经营CF占比 = 经营CF（正值部分） / (|经营CF| + |投资CF| + |筹资CF|) × 100%",
        "data_source": "现金流量表",
    },
    # R7 — 营运资金效率（NEW）
    {
        "id": "working_capital",
        "dimension": "营运",
        "indicator": "营运资金效率恶化",
        "description": "现金转换周期(CCC)持续拉长，应收账款和存货占用过多营运资金，资金周转效率下降。",
        "threshold": "CCC较首期恶化 > 30 天（预警）、> 15 天（关注）",
        "formula": "CCC = DSO + DIO - DPO；DSO=应收账款/营收×365；DIO=存货/营业成本×365；DPO=应付账款/营业成本×365",
        "data_source": "资产负债表 + 利润表",
    },
    # R8 — 偿债能力（NEW）
    {
        "id": "debt_servicing",
        "dimension": "偿债",
        "indicator": "偿债能力不足",
        "description": "利息覆盖倍数不足或短期债务远超现金储备，存在流动性危机和偿债压力。综合评估利息覆盖、短期负债比、债务偿还年限。",
        "threshold": "利息覆盖 < 1.5x（预警）；短期负债/现金 > 3.0x（预警）；债务偿还年限 > 10年（预警）",
        "formula": "利息覆盖 = EBIT / |利息支出|；短期负债比 = 短期借款 / 现金储备；偿还年限 = 总有息负债 / 经营CF",
        "data_source": "资产负债表 + 利润表 + 现金流量表",
    },
    # R9 — 行业对标（NEW）
    {
        "id": "industry_benchmark",
        "dimension": "行业",
        "indicator": "行业对标显著偏离",
        "description": "关键财务指标显著偏离行业基准，反映商业模式差异或异常风险。该指标始终为提示性（关注），不单独触发预警。",
        "threshold": "OpCF利润率、DSO、FCF利润率、融资依赖等偏离行业基准 > 2x",
        "formula": "偏离度 = (实际值 - 行业基准) / |行业基准|；比较项=OpCF利润率、DSO、FCF利润率、融资依赖度；综合偏离 = max(各项偏离度)",
        "data_source": "行业基准数据（内置6行业）",
    },
]

# ---------------------------------------------------------------------------
# Industry-specific benchmarks
# ---------------------------------------------------------------------------

_INDUSTRY_BENCHMARKS: dict[str, dict[str, float]] = {
    "saas": {
        "opcf_margin": -0.05,       # OpCF / Revenue (SaaS often negative early)
        "fcf_margin": -0.10,         # FCF / Revenue
        "capex_to_revenue": 0.08,    # Capex / Revenue
        "dso_days": 60,              # Days Sales Outstanding
        "ccc_days": 45,              # Cash Conversion Cycle
        "interest_coverage": 5.0,    # EBIT / Interest
        "debt_to_cash": 0.5,         # Short-term debt / Cash
        "financing_dependency": 1.0, # FinCF / Cash
    },
    "医药": {
        "opcf_margin": -0.15,
        "fcf_margin": -0.25,
        "capex_to_revenue": 0.12,
        "dso_days": 90,
        "ccc_days": 120,
        "interest_coverage": 2.0,
        "debt_to_cash": 0.8,
        "financing_dependency": 1.5,
    },
    "金融": {
        "opcf_margin": 0.30,
        "fcf_margin": 0.20,
        "capex_to_revenue": 0.03,
        "dso_days": 15,
        "ccc_days": 10,
        "interest_coverage": 3.0,
        "debt_to_cash": 2.0,
        "financing_dependency": 0.5,
    },
    "地产": {
        "opcf_margin": 0.10,
        "fcf_margin": -0.05,
        "capex_to_revenue": 0.20,
        "dso_days": 60,
        "ccc_days": 200,
        "interest_coverage": 2.5,
        "debt_to_cash": 2.5,
        "financing_dependency": 1.0,
    },
    "消费": {
        "opcf_margin": 0.12,
        "fcf_margin": 0.06,
        "capex_to_revenue": 0.06,
        "dso_days": 25,
        "ccc_days": 35,
        "interest_coverage": 8.0,
        "debt_to_cash": 0.3,
        "financing_dependency": 0.3,
    },
    "default": {
        "opcf_margin": 0.10,
        "fcf_margin": 0.05,
        "capex_to_revenue": 0.08,
        "dso_days": 45,
        "ccc_days": 60,
        "interest_coverage": 4.0,
        "debt_to_cash": 1.0,
        "financing_dependency": 0.8,
    },
}


def _get_benchmark(industry: str | None) -> dict[str, float]:
    """Look up industry benchmark dict, falling back to 'default'."""
    if not industry:
        return _INDUSTRY_BENCHMARKS["default"]
    return _INDUSTRY_BENCHMARKS.get(
        industry.lower().strip(),
        _INDUSTRY_BENCHMARKS["default"],
    )


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------


def _status_order(status: str) -> int:
    return {"预警": 3, "关注": 2, "正常": 1, "数据不足": 0}.get(status, 0)


def _worst_status(*statuses: str) -> str:
    """Return the worst status from a set."""
    return max(statuses, key=_status_order)


# ======================================================================
# CashflowAnalyzer
# ======================================================================


class CashflowAnalyzer:
    """10-dimension cashflow risk analysis for HK IPO issuers."""

    # ------------------------------------------------------------------
    # Public API — runway (backward compatible)
    # ------------------------------------------------------------------

    def calculate_runway(
        self,
        financial_data: dict[str, Any],
        include_sensitivity: bool = False,
    ) -> dict[str, Any]:
        """Return cash runway in months.

        Accepts single-period dict or multi-period ``{"periods": [...]}``.
        In multi-period mode only the latest period is used.
        """
        periods = self._normalise_periods(financial_data)
        latest = periods[-1]

        cash = latest.get("cash_and_equivalents", 0)
        op_cf = latest.get("operating_cashflow", 0)
        inv_cf = latest.get("investing_cashflow", 0)
        fin_cf = latest.get("financing_cashflow", 0)
        months = latest.get("period_months", 12)

        net_cf = op_cf + inv_cf
        monthly_burn = -net_cf / months if months > 0 else 0.0

        result: dict[str, Any] = {
            "total_cashflow": round(op_cf + inv_cf + fin_cf, 2),
            "monthly_burn_rate": round(monthly_burn, 2),
            "runway_months": None,
            "status": "healthy",
        }

        if monthly_burn <= 0:
            result["status"] = "healthy"
            result["runway_months"] = None
        else:
            runway = round(cash / monthly_burn, 1) if monthly_burn > 0 else None
            result["runway_months"] = runway

            if runway is not None:
                if runway < 12:
                    result["status"] = "critical"
                elif runway < 24:
                    result["status"] = "warning"
                else:
                    result["status"] = "healthy"

        if include_sensitivity:
            result["sensitivity"] = self._sensitivity_runway(cash, net_cf, months)

        return result

    # ------------------------------------------------------------------
    # Public API — full risk scan (10 indicators)
    # ------------------------------------------------------------------

    def risk_scan(
        self, financial_data: dict[str, Any], ticker: str = ""
    ) -> dict[str, Any]:
        """Run all 10 cashflow risk indicators.

        Returns
        -------
        dict with ``overall_risk``, ``risk_counts``, ``indicators`` (10),
        ``periods_analysed``.
        """
        periods = self._normalise_periods(financial_data)
        latest = periods[-1]
        multi = len(periods) > 1

        indicators: list[dict[str, Any]] = [
            self._check_neg_op_cf(periods, multi),          # R0 enhanced
            self._check_cf_ni_divergence(periods, multi),    # R1 enhanced
            self._check_neg_fcf(periods, multi),             # R2 enhanced
            self._check_capex_ratio(periods, multi),          # R3 merged (was R3+R4)
            self._check_financing_dependency(latest),         # R4 (was R5)
            self._check_burn_rate(latest),                    # R5 enhanced (was R6)
            self._check_cf_structure(latest),                 # R6 NEW
            self._check_working_capital(periods, multi),      # R7 NEW
            self._check_debt_servicing(latest),               # R8 NEW
            self._check_industry_benchmark(latest),           # R9 NEW
        ]

        counts: dict[str, int] = {"预警": 0, "关注": 0, "正常": 0, "数据不足": 0}
        worst = "正常"
        for ind in indicators:
            counts[ind["status"]] = counts.get(ind["status"], 0) + 1
            if _status_order(ind["status"]) > _status_order(worst):
                worst = ind["status"]

        return {
            "ticker": ticker,
            "overall_risk": worst,
            "risk_counts": counts,
            "indicators": indicators,
            "periods_analysed": len(periods),
        }

    # ------------------------------------------------------------------
    # Public API — risk check (simplified conclusion)
    # ------------------------------------------------------------------

    def check_risk(
        self, financial_data: dict[str, Any], ticker: str = ""
    ) -> dict[str, Any]:
        """Run risk scan and return a simplified conclusion.

        Returns
        -------
        dict with ``has_risk``, ``conclusion`` (存在现金流风险 / 暂未识别现金流风险),
        ``overall_risk``, ``risks`` (only 预警+关注), ``all_indicators`` (all 10).
        """
        scan = self.risk_scan(financial_data, ticker=ticker)

        risky = [
            ind for ind in scan["indicators"]
            if ind["status"] in ("预警", "关注")
        ]

        return {
            "has_risk": len(risky) > 0,
            "conclusion": "存在现金流风险" if risky else "暂未识别现金流风险",
            "overall_risk": scan["overall_risk"],
            "risk_counts": scan["risk_counts"],
            "risks": risky,
            "all_indicators": scan["indicators"],
            "periods_analysed": scan["periods_analysed"],
            "ticker": ticker,
        }

    # ==================================================================
    # R0 — 经营造血能力 (enhanced)
    # ==================================================================

    def _check_neg_op_cf(
        self, periods: list[dict[str, Any]], multi: bool
    ) -> dict[str, Any]:
        vals = [p.get("operating_cashflow", 0) or 0 for p in periods]
        neg_streak = 0
        for v in reversed(vals):
            if v < 0:
                neg_streak += 1
            else:
                break

        trend = self._trend_detect(vals) if multi else "stable"
        latest_rev = periods[-1].get("revenue")
        margin_str = ""
        if latest_rev and latest_rev != 0:
            margin = vals[-1] / latest_rev
            margin_str = f"（OpCF/营收={margin:.1%}）"

        # Build detail with trend info
        trend_note = {"improving": "，趋势改善", "worsening": "，趋势恶化", "stable": ""}

        if neg_streak >= 3:
            extra = trend_note.get(trend, "")
            return self._build_indicator(
                "neg_op_cf", "预警",
                f"最近 {neg_streak} 期经营现金流均为负{extra}{margin_str}，持续失血",
                actual=vals[-1], threshold="连续2期以上 < 0",
            )
        if neg_streak >= 2:
            if trend == "worsening":
                return self._build_indicator(
                    "neg_op_cf", "预警",
                    f"最近 {neg_streak} 期经营现金流为负且趋势恶化{margin_str}",
                    actual=vals[-1], threshold="连续2期以上 < 0",
                )
            return self._build_indicator(
                "neg_op_cf", "关注",
                f"最近 {neg_streak} 期经营现金流为负{margin_str}，需关注后续能否转正",
                actual=vals[-1], threshold="连续2期以上 < 0",
            )
        if vals[-1] < 0:
            return self._build_indicator(
                "neg_op_cf", "关注",
                f"当期经营现金流为负{margin_str}",
                actual=vals[-1], threshold="连续2期以上 < 0",
            )
        return self._build_indicator(
            "neg_op_cf", "正常",
            f"经营现金流为正{margin_str}" if margin_str else "经营现金流为正",
            actual=vals[-1],
        )

    # ==================================================================
    # R1 — 盈利质量 (enhanced with revenue-OpCF mismatch)
    # ==================================================================

    def _check_cf_ni_divergence(
        self, periods: list[dict[str, Any]], multi: bool
    ) -> dict[str, Any]:
        # --- CF/NI divergence check (existing) ---
        pairs: list[float] = []
        for p in periods:
            op = p.get("operating_cashflow", 0) or 0
            ni = p.get("net_income") or p.get("净利润")
            if ni is not None and ni != 0:
                pairs.append(op / ni)

        if not pairs:
            return self._build_indicator(
                "cf_ni_divergence", "数据不足",
                "缺少净利润数据，无法计算现金流净利比",
            )

        divergence_streak = 0
        for ratio in reversed(pairs):
            if ratio < 0.5:
                divergence_streak += 1
            else:
                break

        latest_ratio = pairs[-1]

        if divergence_streak >= 3:
            base_status, base_detail = "预警", (
                f"连续 {divergence_streak} 期现金流/净利润 < 0.5"
                f"（最新={latest_ratio:.2f}），盈利质量严重存疑"
            )
        elif divergence_streak >= 2:
            base_status, base_detail = "关注", (
                f"连续 {divergence_streak} 期现金流/净利润 < 0.5"
                f"（最新={latest_ratio:.2f}），盈利质量需要关注"
            )
        elif latest_ratio < 0.5:
            base_status, base_detail = "关注", (
                f"当期现金流/净利润 = {latest_ratio:.2f}，低于0.5"
            )
        else:
            base_status, base_detail = "正常", (
                f"现金流/净利润 = {latest_ratio:.2f}，盈利质量良好"
            )

        # --- Revenue-OpCF growth mismatch (NEW) ---
        if multi and len(periods) >= 2:
            first = periods[0]
            latest = periods[-1]
            first_rev = first.get("revenue")
            latest_rev = latest.get("revenue")
            first_opcf = first.get("operating_cashflow", 0) or 0
            latest_opcf = latest.get("operating_cashflow", 0) or 0

            if first_rev and latest_rev and first_rev != 0:
                rev_growth = (latest_rev - first_rev) / abs(first_rev)
                opcf_growth = (
                    (latest_opcf - first_opcf) / max(abs(first_opcf), 1.0)
                    if first_opcf != 0 else 0
                )

                if rev_growth > 0.15 and opcf_growth < 0.05:
                    mismatch_detail = (
                        f"营收增长 {rev_growth:.0%} 但经营CF仅增长 {opcf_growth:.0%}，"
                        f"存在「纸面利润」风险"
                    )
                    if base_status == "预警":
                        base_detail += "；" + mismatch_detail
                    elif base_status == "关注":
                        base_status = "预警"
                        base_detail = mismatch_detail
                    else:
                        base_status = "关注"
                        base_detail = mismatch_detail

        return self._build_indicator(
            "cf_ni_divergence", base_status, base_detail,
            actual=latest_ratio, threshold="< 0.5 且连续2期",
        )

    # ==================================================================
    # R2 — 自由现金流 (enhanced with trend)
    # ==================================================================

    def _check_neg_fcf(
        self, periods: list[dict[str, Any]], multi: bool
    ) -> dict[str, Any]:
        fcfs: list[float] = []
        for p in periods:
            op = p.get("operating_cashflow", 0) or 0
            capex = p.get("capex") or p.get("capital_expenditure", 0) or 0
            fcfs.append(op - abs(capex))

        neg_streak = 0
        for v in reversed(fcfs):
            if v < 0:
                neg_streak += 1
            else:
                break

        latest_fcf = fcfs[-1]
        trend = self._trend_detect(fcfs) if multi else "stable"
        trend_note = {"improving": "，趋势改善", "worsening": "，趋势恶化", "stable": ""}

        # FCF/Revenue ratio
        latest_rev = periods[-1].get("revenue")
        margin_str = ""
        if latest_rev and latest_rev != 0:
            margin_str = f"；FCF/营收={latest_fcf / latest_rev:.1%}"

        if neg_streak >= 3:
            extra = trend_note.get(trend, "")
            return self._build_indicator(
                "neg_fcf", "预警",
                f"连续 {neg_streak} 期自由现金流为负{extra}"
                f"（最新={latest_fcf:.1f} Mn）{margin_str}，缺乏内生增长能力",
                actual=latest_fcf, threshold="FCF < 0 且连续3期",
            )
        if neg_streak >= 2:
            if trend == "worsening":
                return self._build_indicator(
                    "neg_fcf", "预警",
                    f"连续 {neg_streak} 期FCF为负且趋势恶化"
                    f"（最新={latest_fcf:.1f} Mn）{margin_str}",
                    actual=latest_fcf, threshold="FCF < 0 且连续3期",
                )
            return self._build_indicator(
                "neg_fcf", "关注",
                f"连续 {neg_streak} 期自由现金流为负"
                f"（最新={latest_fcf:.1f} Mn）{margin_str}",
                actual=latest_fcf, threshold="FCF < 0 且连续3期",
            )
        if latest_fcf < 0:
            return self._build_indicator(
                "neg_fcf", "关注",
                f"当期自由现金流为负（{latest_fcf:.1f} Mn）{margin_str}",
                actual=latest_fcf, threshold="FCF < 0",
            )
        return self._build_indicator(
            "neg_fcf", "正常",
            f"自由现金流为正（{latest_fcf:.1f} Mn）{margin_str}",
            actual=latest_fcf,
        )

    # ==================================================================
    # R3 — 资本支出合理性 (merged old R3 + R4, enhanced)
    # ==================================================================

    def _check_capex_ratio(
        self, periods: list[dict[str, Any]], multi: bool
    ) -> dict[str, Any]:
        latest = periods[-1]
        op = latest.get("operating_cashflow", 0) or 0
        capex_val = latest.get("capex") or latest.get("capital_expenditure", 0) or 0
        abs_capex = abs(capex_val)
        inv = latest.get("investing_cashflow", 0) or 0
        assets = latest.get("total_assets") or latest.get("总资产")
        depreciation = latest.get("depreciation")

        findings: list[tuple[str, str]] = []  # (status, detail)

        # --- Sub-A: Capex/OpCF ratio (old R3) ---
        if op == 0:
            findings.append(("数据不足", "经营现金流为零，无法计算Capex比率"))
        elif op < 0:
            ratio_a = abs(capex_val / op) if op != 0 else 999
            if ratio_a > 5:
                findings.append(("预警",
                    f"经营CF为负（{op:.1f}），资本支出（{abs_capex:.1f}）进一步加剧现金消耗"))
            else:
                findings.append(("关注",
                    f"经营CF为负（{op:.1f}），资本支出（{abs_capex:.1f}）需结合融资能力评估"))
        else:
            ratio_a = abs_capex / op
            if ratio_a > 3.0:
                findings.append(("预警",
                    f"Capex/OpCF = {ratio_a:.1f}x，投资扩张严重过度"))
            elif ratio_a > 2.0:
                findings.append(("关注",
                    f"Capex/OpCF = {ratio_a:.1f}x，投资偏激进"))
            else:
                findings.append(("正常",
                    f"Capex/OpCF = {ratio_a:.1f}x，投资节奏合理"))

        # --- Sub-B: Investment intensity (old R4 merged) ---
        if assets is None or assets == 0:
            findings.append(("数据不足", "缺少总资产数据，无法计算投资强度"))
        else:
            intensity = abs(inv) / abs(assets) * 100
            if intensity > 30:
                findings.append(("预警",
                    f"投资强度={intensity:.1f}%，激进扩张，远超15%警戒线"))
            elif intensity > 15:
                findings.append(("关注",
                    f"投资强度={intensity:.1f}%，超过15%警戒线"))
            else:
                findings.append(("正常",
                    f"投资强度={intensity:.1f}%，在合理范围内"))

        # --- Sub-C: Maintenance vs expansion capex (NEW) ---
        if depreciation is not None and depreciation != 0 and op > 0:
            expansion = max(0, abs_capex - abs(depreciation))
            exp_ratio = expansion / op
            if exp_ratio > 3.0:
                findings.append(("预警",
                    f"扩张性Capex/OpCF={exp_ratio:.1f}x，极度依赖外部融资支撑扩张"))
            elif exp_ratio > 1.5:
                findings.append(("关注",
                    f"扩张性Capex/OpCF={exp_ratio:.1f}x，扩张偏激进"))
            else:
                findings.append(("正常",
                    f"扩张性Capex/OpCF={exp_ratio:.1f}x，扩张节奏可控"))

        # Aggregate
        worst = _worst_status(*(s for s, _ in findings))
        detail = " | ".join(d for _, d in findings)

        return self._build_indicator(
            "capex_ratio", worst, detail,
            actual=abs_capex, threshold="Capex/OpCF > 2.0 | 投资强度 > 15% | 扩张性Capex/OpCF > 1.5",
        )

    # ==================================================================
    # R4 — 融资依赖度 (old R5, minor enhance)
    # ==================================================================

    def _check_financing_dependency(
        self, latest: dict[str, Any]
    ) -> dict[str, Any]:
        fin = latest.get("financing_cashflow", 0) or 0
        cash = latest.get("cash_and_equivalents", 0) or 0

        if cash == 0:
            return self._build_indicator(
                "financing_dependency", "数据不足",
                "期末现金余额为零或缺失",
            )

        ratio = fin / cash
        industry = latest.get("industry")
        benchmark = _get_benchmark(industry).get("financing_dependency", 0.8)

        if ratio > 2.0:
            detail = f"筹资依赖度 = {ratio:.1f}x，严重依赖外部输血"
            if ratio > benchmark * 3:
                detail += f"（行业基准{benchmark:.1f}x，严重偏离）"
            return self._build_indicator(
                "financing_dependency", "预警", detail,
                actual=ratio, threshold="> 1.5",
            )
        if ratio > 1.5:
            detail = f"筹资依赖度 = {ratio:.1f}x，超过1.5倍警戒线"
            if ratio > benchmark * 2:
                detail += f"（行业基准{benchmark:.1f}x，显著偏离）"
            return self._build_indicator(
                "financing_dependency", "关注", detail,
                actual=ratio, threshold="> 1.5",
            )
        if fin < 0:
            return self._build_indicator(
                "financing_dependency", "正常",
                f"筹资活动净流出（{fin:.1f}），可能在偿还债务或分红",
                actual=ratio,
            )
        return self._build_indicator(
            "financing_dependency", "正常",
            f"筹资依赖度 = {ratio:.1f}x，融资结构合理",
            actual=ratio,
        )

    # ==================================================================
    # R5 — 现金跑道 (old R6 + sensitivity)
    # ==================================================================

    def _check_burn_rate(self, latest: dict[str, Any]) -> dict[str, Any]:
        runway_result = self.calculate_runway(
            latest, include_sensitivity=True
        )

        status = runway_result["status"]
        runway = runway_result.get("runway_months")
        sensitivity = runway_result.get("sensitivity", {})

        if status == "critical":
            detail = f"现金跑道仅 {runway} 个月，资金链极度紧张"
            result = self._build_indicator(
                "burn_rate", "预警", detail,
                actual=runway, threshold="< 12 个月",
            )
        elif status == "warning":
            detail = f"现金跑道 {runway} 个月，12-24个月内需关注"
            result = self._build_indicator(
                "burn_rate", "关注", detail,
                actual=runway, threshold="< 24 个月",
            )
        elif runway is None:
            result = self._build_indicator(
                "burn_rate", "正常",
                "经营+投资现金流净额为正，无 Burn Rate 压力",
            )
        else:
            result = self._build_indicator(
                "burn_rate", "正常",
                f"现金跑道 {runway} 个月，短期无虞",
                actual=runway,
            )

        if sensitivity:
            result["sensitivity"] = sensitivity

        return result

    # ==================================================================
    # R6 — 现金流结构 (NEW)
    # ==================================================================

    def _check_cf_structure(self, latest: dict[str, Any]) -> dict[str, Any]:
        op = latest.get("operating_cashflow", 0) or 0
        inv = latest.get("investing_cashflow", 0) or 0
        fin = latest.get("financing_cashflow", 0) or 0
        total = abs(op) + abs(inv) + abs(fin)

        if total == 0:
            return self._build_indicator(
                "cf_structure", "数据不足",
                "三大现金流均为零，无法评估结构",
            )

        op_share = (op if op > 0 else 0) / total * 100
        fin_share = abs(fin) / total * 100
        inv_share = abs(inv) / total * 100

        findings: list[tuple[str, str]] = []

        # Operating CF contribution
        if op_share >= 50:
            findings.append(("正常", f"经营CF占比{op_share:.0f}%，造血结构健康"))
        elif op_share >= 30:
            findings.append(("关注", f"经营CF占比仅{op_share:.0f}%，造血能力偏弱"))
        else:
            findings.append(("预警", f"经营CF占比仅{op_share:.0f}%，高度依赖外部资金"))

        # Financing dependency signal
        if fin_share > 60 and op <= 0:
            findings.append(("预警", f"筹资CF占比{fin_share:.0f}%，完全依赖融资输血"))
        elif fin_share > 60:
            findings.append(("关注", f"筹资CF占比{fin_share:.0f}%，融资依赖偏高"))
        elif fin_share > 40:
            findings.append(("正常", f"筹资CF占比{fin_share:.0f}%"))
        else:
            findings.append(("正常", f"筹资CF占比{fin_share:.0f}%"))

        # Investing intensity note
        findings.append(("正常", f"投资CF占比{inv_share:.0f}%"))

        worst = _worst_status(*(s for s, _ in findings))
        detail = " | ".join(d for _, d in findings)

        return self._build_indicator(
            "cf_structure", worst, detail,
            actual=op_share, threshold="经营CF占比 >= 50%",
        )

    # ==================================================================
    # R7 — 营运资金效率 (NEW)
    # ==================================================================

    def _check_working_capital(
        self, periods: list[dict[str, Any]], multi: bool
    ) -> dict[str, Any]:
        required = ["revenue", "accounts_receivable", "inventory",
                     "accounts_payable", "cogs"]
        if any(periods[-1].get(f) is None for f in required):
            missing = [f for f in required if periods[-1].get(f) is None]
            return self._build_indicator(
                "working_capital", "数据不足",
                f"缺少营运资金计算所需字段：{', '.join(missing)}",
            )

        ccc_values: list[float] = []
        for p in periods:
            rev = p.get("revenue", 0) or 0
            ar = p.get("accounts_receivable", 0) or 0
            inv = p.get("inventory", 0) or 0
            ap = p.get("accounts_payable", 0) or 0
            cogs = p.get("cogs", 0) or 0

            dso = ar / rev * 365 if rev else 0
            dio = inv / cogs * 365 if cogs else 0
            dpo = ap / cogs * 365 if cogs else 0
            ccc_values.append(dso + dio - dpo)

        latest_ccc = ccc_values[-1]
        ccc_delta = ccc_values[-1] - ccc_values[0] if len(ccc_values) >= 2 else 0
        trend = self._trend_detect(ccc_values, better_is_lower=True) if multi else "stable"

        dso_val = periods[-1].get("accounts_receivable", 0) / max(periods[-1].get("revenue", 1), 1) * 365

        if ccc_delta > 30 and trend == "worsening":
            return self._build_indicator(
                "working_capital", "预警",
                f"CCC={latest_ccc:.0f}天，较首期恶化{ccc_delta:.0f}天，"
                f"营运效率严重下滑（DSO≈{dso_val:.0f}天）",
                actual=latest_ccc, threshold="CCC恶化 > 30天",
            )
        if ccc_delta > 15:
            return self._build_indicator(
                "working_capital", "关注",
                f"CCC={latest_ccc:.0f}天，较首期恶化{ccc_delta:.0f}天"
                f"（DSO≈{dso_val:.0f}天）",
                actual=latest_ccc, threshold="CCC恶化 > 15天",
            )
        if trend == "worsening" and latest_ccc > 60:
            return self._build_indicator(
                "working_capital", "关注",
                f"CCC={latest_ccc:.0f}天，呈恶化趋势（DSO≈{dso_val:.0f}天）",
                actual=latest_ccc,
            )
        return self._build_indicator(
            "working_capital", "正常",
            f"CCC={latest_ccc:.0f}天，营运效率稳定（DSO≈{dso_val:.0f}天）",
            actual=latest_ccc,
        )

    # ==================================================================
    # R8 — 偿债能力 (NEW)
    # ==================================================================

    def _check_debt_servicing(self, latest: dict[str, Any]) -> dict[str, Any]:
        findings: list[tuple[str, str]] = []
        any_data = False

        # Sub-A: Interest coverage
        ebit = latest.get("ebit")
        int_exp = latest.get("interest_expense")
        if ebit is not None and int_exp is not None and int_exp != 0:
            any_data = True
            icr = ebit / abs(int_exp)
            if icr < 1.5:
                findings.append(("预警",
                    f"利息覆盖倍数={icr:.1f}x（< 1.5x预警）"))
            elif icr < 3.0:
                findings.append(("关注",
                    f"利息覆盖倍数={icr:.1f}x（< 3.0x关注）"))
            else:
                findings.append(("正常",
                    f"利息覆盖倍数={icr:.1f}x"))

        # Sub-B: Short-term debt / cash
        st_debt = latest.get("short_term_debt")
        cash = latest.get("cash_and_equivalents", 0) or 0
        if st_debt is not None and cash != 0:
            any_data = True
            st_ratio = st_debt / cash
            if st_ratio > 3.0:
                findings.append(("预警",
                    f"短期负债/现金={st_ratio:.1f}x（> 3.0x预警）"))
            elif st_ratio > 1.5:
                findings.append(("关注",
                    f"短期负债/现金={st_ratio:.1f}x（> 1.5x关注）"))
            else:
                findings.append(("正常",
                    f"短期负债/现金={st_ratio:.1f}x"))

        # Sub-C: Debt repayment years
        t_debt = latest.get("total_debt")
        op_cf = latest.get("operating_cashflow", 0) or 0
        if t_debt is not None and op_cf > 0:
            any_data = True
            repay_years = t_debt / op_cf
            if repay_years > 10:
                findings.append(("预警",
                    f"债务偿还年限={repay_years:.1f}年（> 10年预警）"))
            elif repay_years > 5:
                findings.append(("关注",
                    f"债务偿还年限={repay_years:.1f}年（> 5年关注）"))
            else:
                findings.append(("正常",
                    f"债务偿还年限={repay_years:.1f}年"))

        if not any_data:
            return self._build_indicator(
                "debt_servicing", "数据不足",
                "缺少偿债能力计算所需字段（ebit/利息支出/短期负债/总负债）",
            )

        worst = _worst_status(*(s for s, _ in findings))
        detail = " | ".join(d for _, d in findings)
        return self._build_indicator("debt_servicing", worst, detail)

    # ==================================================================
    # R9 — 行业对标 (NEW)
    # ==================================================================

    def _check_industry_benchmark(
        self, latest: dict[str, Any]
    ) -> dict[str, Any]:
        industry = latest.get("industry")
        if not industry:
            return self._build_indicator(
                "industry_benchmark", "数据不足",
                "未指定行业标签（设置 --industry saas|医药|金融|地产|消费）",
            )

        bm = _get_benchmark(industry)
        deviations: list[str] = []
        worst_sub = "正常"

        op_cf = latest.get("operating_cashflow", 0) or 0
        rev = latest.get("revenue")
        cash = latest.get("cash_and_equivalents", 0) or 0
        fin_cf = latest.get("financing_cashflow", 0) or 0
        capex_val = latest.get("capex") or latest.get("capital_expenditure", 0) or 0

        # OpCF margin vs benchmark
        if rev and rev != 0:
            margin = op_cf / rev
            bm_margin = bm.get("opcf_margin", 0.10)
            if abs(bm_margin) > 0.001:
                dev = (margin - bm_margin) / abs(bm_margin)
                if abs(dev) > 2.0:
                    direction = "低于" if dev < 0 else "高于"
                    deviations.append(
                        f"OpCF利润率={margin:.1%}，{direction}行业基准{bm_margin:.1%}"
                        f"（偏离{abs(dev):.0f}x）"
                    )
                    if abs(dev) > 3.0:
                        worst_sub = _worst_status(worst_sub, "关注")

        # DSO vs benchmark
        ar = latest.get("accounts_receivable")
        if rev and ar and rev != 0:
            dso = ar / rev * 365
            bm_dso = bm.get("dso_days", 45)
            dev_dso = dso / bm_dso if bm_dso > 0 else 1.0
            if dev_dso > 2.0:
                deviations.append(
                    f"DSO={dso:.0f}天，远高于行业基准{bm_dso}天（{dev_dso:.1f}x）"
                )
                worst_sub = _worst_status(worst_sub, "关注")
            elif dev_dso > 1.5:
                deviations.append(
                    f"DSO={dso:.0f}天，高于行业基准{bm_dso}天（{dev_dso:.1f}x）"
                )

        # Financing dependency vs benchmark
        if cash != 0:
            fin_dep = fin_cf / cash
            bm_fin = bm.get("financing_dependency", 0.8)
            if abs(bm_fin) > 0.001:
                dev_fin = fin_dep / bm_fin if bm_fin > 0 else 1.0
                if dev_fin > 3.0:
                    deviations.append(
                        f"融资依赖={fin_dep:.1f}x，远超行业{bm_fin:.1f}x（{dev_fin:.0f}x）"
                    )
                    worst_sub = _worst_status(worst_sub, "关注")

        # FCF/Revenue vs benchmark
        if rev and rev != 0:
            fcf_val = op_cf - abs(capex_val)
            fcf_margin = fcf_val / rev
            bm_fcf = bm.get("fcf_margin", 0.05)
            if abs(bm_fcf) > 0.001:
                dev_fcf = (fcf_margin - bm_fcf) / abs(bm_fcf)
                if abs(dev_fcf) > 2.0:
                    direction = "低于" if dev_fcf < 0 else "高于"
                    deviations.append(
                        f"FCF利润率={fcf_margin:.1%}，{direction}行业{bm_fcf:.1%}"
                    )

        if not deviations:
            return self._build_indicator(
                "industry_benchmark", "正常",
                f"核心指标与「{industry}」行业基准基本吻合",
            )

        # R9 is always at most 关注 level (advisory only)
        status = min(worst_sub, "关注", key=_status_order)
        detail = "；".join(deviations)
        return self._build_indicator(
            "industry_benchmark", status, detail,
            actual=None, threshold="偏离行业基准 > 2x",
        )

    # ==================================================================
    # Helper methods
    # ==================================================================

    @staticmethod
    def _normalise_periods(data: dict[str, Any]) -> list[dict[str, Any]]:
        """Accept either a single-period dict or {periods: [...]}."""
        if "periods" in data:
            return data["periods"]
        return [data]

    @staticmethod
    def _trend_detect(
        values: list[float],
        better_is_lower: bool = False,
        threshold_pct: float = 0.05,
    ) -> str:
        """Classify trend across periods as improving/worsening/stable."""
        clean = [v for v in values if v is not None]
        if len(clean) < 2:
            return "stable"
        first, last = clean[0], clean[-1]
        span = max(abs(first), abs(last), 1.0)
        if abs(last - first) / span < threshold_pct:
            return "stable"
        delta = last - first
        if better_is_lower:
            delta = -delta
        return "improving" if delta > 0 else "worsening"

    @staticmethod
    def _sensitivity_runway(
        cash: float, net_cf: float, months: int
    ) -> dict[str, Any]:
        """Compute runway under three burn-rate scenarios."""
        monthly_burn = -net_cf / months if months > 0 else 0.0
        if monthly_burn <= 0:
            return {"base": None, "pessimistic": None, "optimistic": None}
        return {
            "base": round(cash / monthly_burn, 1),
            "pessimistic": round(cash / (monthly_burn * 1.2), 1),
            "optimistic": round(cash / (monthly_burn * 0.8), 1),
        }

    @staticmethod
    def _build_indicator(
        indicator_id: str,
        status: str,
        detail: str,
        actual: float | None = None,
        threshold: str | None = None,
    ) -> dict[str, Any]:
        meta = {}
        for d in _RISK_DEFINITIONS:
            if d["id"] == indicator_id:
                meta = d
                break

        result: dict[str, Any] = {
            "id": indicator_id,
            "indicator": meta.get("indicator", indicator_id),
            "dimension": meta.get("dimension", "现金流"),
            "status": status,
            "detail": detail,
        }
        if actual is not None:
            result["actual_value"] = round(actual, 4)
        if threshold is not None:
            result["threshold"] = threshold
        if meta.get("formula"):
            result["formula"] = meta["formula"]
        if meta.get("data_source"):
            result["data_source"] = meta["data_source"]
        return result


# ======================================================================
# Demo datasets
# ======================================================================

_DEMO_SCENARIOS: list[dict[str, Any]] = [
    {
        "label": "典型烧钱型 SaaS（有融资续命）",
        "data": {
            "cash_and_equivalents": 2_400,
            "operating_cashflow": -800,
            "investing_cashflow": -200,
            "financing_cashflow": 1_500,
            "period_months": 12,
            "revenue": 1_200,
            "accounts_receivable": 350,
            "inventory": 0,
            "accounts_payable": 80,
            "cogs": 400,
            "short_term_debt": 300,
            "total_debt": 1_000,
            "interest_expense": -40,
            "ebit": -200,
            "depreciation": -50,
            "industry": "saas",
        },
    },
    {
        "label": "盈利稳定的传统企业",
        "data": {
            "cash_and_equivalents": 5_000,
            "operating_cashflow": 600,
            "investing_cashflow": -300,
            "financing_cashflow": 0,
            "period_months": 12,
            "revenue": 4_000,
            "accounts_receivable": 300,
            "inventory": 500,
            "accounts_payable": 200,
            "cogs": 2_500,
            "short_term_debt": 200,
            "total_debt": 800,
            "interest_expense": -20,
            "ebit": 900,
            "depreciation": -100,
            "industry": "消费",
        },
    },
    {
        "label": "资金链极度紧张（< 12 个月跑道）",
        "data": {
            "cash_and_equivalents": 500,
            "operating_cashflow": -600,
            "investing_cashflow": -100,
            "financing_cashflow": 0,
            "period_months": 12,
            "revenue": 800,
            "accounts_receivable": 400,
            "inventory": 300,
            "accounts_payable": 100,
            "cogs": 600,
            "short_term_debt": 800,
            "total_debt": 2_000,
            "interest_expense": -60,
            "ebit": -300,
            "depreciation": -30,
            "industry": "地产",
        },
    },
]

# Multi-period demo: SaaS company over 3 years
_RISK_DEMO_MULTI: dict[str, Any] = {
    "periods": [
        {
            "label": "FY2024",
            "operating_cashflow": -900,
            "investing_cashflow": -300,
            "financing_cashflow": 2_000,
            "cash_and_equivalents": 2_800,
            "net_income": 150,
            "capex": -400,
            "total_assets": 8_000,
            "period_months": 12,
            "revenue": 1_500,
            "accounts_receivable": 450,
            "inventory": 0,
            "accounts_payable": 100,
            "cogs": 500,
            "short_term_debt": 400,
            "total_debt": 1_200,
            "interest_expense": -50,
            "ebit": -150,
            "depreciation": -60,
            "industry": "saas",
        },
        {
            "label": "FY2023",
            "operating_cashflow": -700,
            "investing_cashflow": -250,
            "financing_cashflow": 1_500,
            "cash_and_equivalents": 2_000,
            "net_income": 50,
            "capex": -350,
            "total_assets": 6_000,
            "period_months": 12,
            "revenue": 1_000,
            "accounts_receivable": 300,
            "inventory": 0,
            "accounts_payable": 70,
            "cogs": 350,
            "short_term_debt": 250,
            "total_debt": 800,
            "interest_expense": -30,
            "ebit": -80,
            "depreciation": -40,
            "industry": "saas",
        },
        {
            "label": "FY2022",
            "operating_cashflow": -500,
            "investing_cashflow": -200,
            "financing_cashflow": 800,
            "cash_and_equivalents": 1_500,
            "net_income": -100,
            "capex": -300,
            "total_assets": 4_000,
            "period_months": 12,
            "revenue": 600,
            "accounts_receivable": 150,
            "inventory": 0,
            "accounts_payable": 50,
            "cogs": 200,
            "short_term_debt": 150,
            "total_debt": 500,
            "interest_expense": -15,
            "ebit": -120,
            "depreciation": -30,
            "industry": "saas",
        },
    ],
}

# Healthy multi-period: profitable traditional company
_RISK_DEMO_HEALTHY: dict[str, Any] = {
    "periods": [
        {
            "label": "FY2024",
            "operating_cashflow": 800,
            "investing_cashflow": -200,
            "financing_cashflow": -100,
            "cash_and_equivalents": 6_000,
            "net_income": 750,
            "capex": -150,
            "total_assets": 12_000,
            "period_months": 12,
            "revenue": 5_000,
            "accounts_receivable": 350,
            "inventory": 600,
            "accounts_payable": 250,
            "cogs": 3_000,
            "short_term_debt": 100,
            "total_debt": 500,
            "interest_expense": -10,
            "ebit": 1_000,
            "depreciation": -80,
            "industry": "消费",
        },
        {
            "label": "FY2023",
            "operating_cashflow": 700,
            "investing_cashflow": -180,
            "financing_cashflow": -80,
            "cash_and_equivalents": 5_500,
            "net_income": 650,
            "capex": -130,
            "total_assets": 11_000,
            "period_months": 12,
            "revenue": 4_500,
            "accounts_receivable": 320,
            "inventory": 550,
            "accounts_payable": 230,
            "cogs": 2_700,
            "short_term_debt": 120,
            "total_debt": 550,
            "interest_expense": -12,
            "ebit": 900,
            "depreciation": -75,
            "industry": "消费",
        },
        {
            "label": "FY2022",
            "operating_cashflow": 600,
            "investing_cashflow": -150,
            "financing_cashflow": -50,
            "cash_and_equivalents": 5_000,
            "net_income": 550,
            "capex": -120,
            "total_assets": 10_000,
            "period_months": 12,
            "revenue": 4_000,
            "accounts_receivable": 300,
            "inventory": 500,
            "accounts_payable": 200,
            "cogs": 2_500,
            "short_term_debt": 150,
            "total_debt": 600,
            "interest_expense": -15,
            "ebit": 800,
            "depreciation": -70,
            "industry": "消费",
        },
    ],
}


# ======================================================================
# Standalone CLI
# ======================================================================


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CashflowAnalyzer — 10维度现金流风险扫描",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Run built-in runway demo scenarios",
    )
    parser.add_argument(
        "--risk-scan", action="store_true",
        help="Run full 10-indicator risk scan",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Run risk check and print conclusion (存在风险 / 暂未识别)",
    )
    parser.add_argument(
        "--healthy", action="store_true",
        help="Use healthy-company multi-period demo",
    )
    parser.add_argument(
        "--sensitivity", action="store_true",
        help="Show cash runway sensitivity analysis",
    )
    parser.add_argument(
        "--industry", default=None,
        help="Industry tag for benchmarking (saas/医药/金融/地产/消费)",
    )
    # Financial data inputs
    parser.add_argument("--cash", type=float, default=2_400,
                        help="Cash & equivalents in HKD mn")
    parser.add_argument("--op-cf", type=float, default=-800,
                        help="Operating cashflow in HKD mn")
    parser.add_argument("--inv-cf", type=float, default=-200,
                        help="Investing cashflow in HKD mn")
    parser.add_argument("--fin-cf", type=float, default=1_500,
                        help="Financing cashflow in HKD mn")
    parser.add_argument("--months", type=int, default=12,
                        help="Period length in months")
    # Extended fields
    parser.add_argument("--revenue", type=float, default=None,
                        help="Revenue in HKD mn")
    parser.add_argument("--cogs", type=float, default=None,
                        help="Cost of goods sold in HKD mn")
    parser.add_argument("--st-debt", type=float, default=None,
                        help="Short-term debt in HKD mn")
    parser.add_argument("--total-debt", type=float, default=None,
                        help="Total interest-bearing debt in HKD mn")
    parser.add_argument("--interest", type=float, default=None,
                        help="Interest expense in HKD mn")
    parser.add_argument("--ebit", type=float, default=None,
                        help="EBIT in HKD mn")
    parser.add_argument("--depreciation", type=float, default=None,
                        help="Depreciation in HKD mn")
    parser.add_argument("--ar", type=float, default=None,
                        help="Accounts receivable in HKD mn")
    parser.add_argument("--inventory", type=float, default=None,
                        help="Inventory in HKD mn")
    parser.add_argument("--ap", type=float, default=None,
                        help="Accounts payable in HKD mn")
    parser.add_argument(
        "--json", "-j", action="store_true",
        help="Output as JSON",
    )
    return parser


def _build_custom_data(args: argparse.Namespace) -> dict[str, Any]:
    """Build a single-period data dict from CLI args."""
    data: dict[str, Any] = {
        "cash_and_equivalents": args.cash,
        "operating_cashflow": args.op_cf,
        "investing_cashflow": args.inv_cf,
        "financing_cashflow": args.fin_cf,
        "period_months": args.months,
    }
    if args.industry:
        data["industry"] = args.industry
    for arg_name, key in [
        ("revenue", "revenue"), ("cogs", "cogs"),
        ("st_debt", "short_term_debt"), ("total_debt", "total_debt"),
        ("interest", "interest_expense"), ("ebit", "ebit"),
        ("depreciation", "depreciation"), ("ar", "accounts_receivable"),
        ("inventory", "inventory"), ("ap", "accounts_payable"),
    ]:
        val = getattr(args, arg_name, None)
        if val is not None:
            data[key] = val
    return data


def main() -> None:
    parser = _build_cli()
    args = parser.parse_args()

    analyzer = CashflowAnalyzer()

    # --check (simplified conclusion)
    if args.check:
        if args.healthy:
            data = _RISK_DEMO_HEALTHY
        elif any(getattr(args, a) is not None for a in [
            "revenue", "cogs", "st_debt", "ebit", "ar", "inventory"
        ]):
            data = _build_custom_data(args)
        else:
            data = _RISK_DEMO_MULTI
        result = analyzer.check_risk(data)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_check_result(result, args.sensitivity)
        return

    # --risk-scan
    if args.risk_scan:
        if args.healthy:
            data = _RISK_DEMO_HEALTHY
        elif any(getattr(args, a) is not None for a in [
            "revenue", "cogs", "st_debt", "ebit", "ar", "inventory"
        ]):
            data = _build_custom_data(args)
        else:
            data = _RISK_DEMO_MULTI
        result = analyzer.risk_scan(data)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_risk_scan(result, args.sensitivity)
        return

    # --demo
    if args.demo:
        for scenario in _DEMO_SCENARIOS:
            result = analyzer.calculate_runway(
                scenario["data"], include_sensitivity=args.sensitivity
            )
            if args.json:
                print(json.dumps(
                    {"scenario": scenario["label"], **result},
                    ensure_ascii=False,
                ))
            else:
                _print_runway(scenario["label"], scenario["data"], result)
        return

    # Custom single-period
    data = _build_custom_data(args)
    result = analyzer.calculate_runway(
        data, include_sensitivity=args.sensitivity
    )
    if args.json:
        print(json.dumps({"inputs": data, **result}, ensure_ascii=False, indent=2))
    else:
        _print_runway("自定义输入", data, result)


# ======================================================================
# Pretty-print
# ======================================================================

_STATUS_ICON = {"预警": "[!!]", "关注": "[?]", "正常": "[OK]", "数据不足": "[--]"}


def _print_runway(label: str, data: dict, result: dict) -> None:
    status_icon = {"healthy": "[OK]", "warning": "[WARN]", "critical": "[CRIT]"}
    icon = status_icon.get(result["status"], "[?]")

    print(f"\n  {'='*50}")
    print(f"  {label}")
    print(f"  {'='*50}")
    print(f"  现金: {data['cash_and_equivalents']:,.0f} Mn  |  "
          f"经营 CF: {data['operating_cashflow']:,.0f}  |  "
          f"投资 CF: {data['investing_cashflow']:,.0f}")
    print(f"  融资 CF: {data['financing_cashflow']:,.0f}  |  "
          f"期间: {data['period_months']} 个月")
    print(f"  {'-'*40}")
    print(f"  月均消耗: {result['monthly_burn_rate']:,.1f} Mn")
    print(f"  现金跑道: {result['runway_months']} 个月"
          if result["runway_months"] is not None else
          f"  现金跑道: N/A（现金净流入）")
    print(f"  状态: {icon} {result['status']}")
    if result.get("sensitivity"):
        s = result["sensitivity"]
        print(f"  敏感性: 乐观={s['optimistic']}个月 | "
              f"基准={s['base']}个月 | 悲观={s['pessimistic']}个月")


def _print_risk_scan(result: dict, show_sensitivity: bool = False) -> None:
    ticker_str = f" — {result['ticker']}" if result.get("ticker") else ""
    overall = result["overall_risk"]
    counts = result.get("risk_counts", {})

    print(f"\n{'='*60}")
    print(f"  现金流风险扫描报告（10维度）{ticker_str}")
    print(f"{'='*60}")
    print(f"  分析期数: {result['periods_analysed']}")
    print(f"  综合风险等级: {_STATUS_ICON.get(overall, '[?]')} {overall}")
    print(f"  风险分布: 预警 {counts.get('预警', 0)} | "
          f"关注 {counts.get('关注', 0)} | "
          f"正常 {counts.get('正常', 0)} | "
          f"数据不足 {counts.get('数据不足', 0)}")
    print(f"\n  {'─'*55}")

    for ind in result.get("indicators", []):
        icon = _STATUS_ICON.get(ind["status"], "[?]")
        dim_tag = f"[{ind.get('dimension', '')}]" if ind.get("dimension") else ""
        print(f"\n  {icon} {ind['status']} {dim_tag} {ind['indicator']}")
        print(f"     {ind['detail']}")
        if ind.get("actual_value") is not None:
            print(f"     实际值: {ind['actual_value']}")
        if ind.get("threshold"):
            print(f"     阈值: {ind['threshold']}")
        if ind.get("formula"):
            print(f"     公式: {ind['formula']}")
        if ind.get("data_source"):
            print(f"     数据来源: {ind['data_source']}")
        # Sensitivity for burn_rate
        if show_sensitivity and ind.get("sensitivity"):
            s = ind["sensitivity"]
            print(f"     敏感性: 乐观={s['optimistic']}月 | "
                  f"基准={s['base']}月 | 悲观={s['pessimistic']}月")

    print(f"\n{'='*60}")


def _print_check_result(result: dict, show_sensitivity: bool = False) -> None:
    ticker_str = f" — {result['ticker']}" if result.get("ticker") else ""

    print(f"\n{'='*60}")
    print(f"  现金流风险检查报告（10维度）{ticker_str}")
    print(f"{'='*60}")

    if result["has_risk"]:
        risks = result["risks"]
        counts = result.get("risk_counts", {})
        print(f"\n  [!!] 结论：存在现金流风险")
        print(f"  综合风险等级：{result['overall_risk']}")
        print(f"  风险指标：预警 {counts.get('预警', 0)} 项 | "
              f"关注 {counts.get('关注', 0)} 项 | "
              f"正常 {counts.get('正常', 0)} 项 | "
              f"数据不足 {counts.get('数据不足', 0)} 项")
        print(f"\n  {'─'*55}")
        print(f"  风险详情：")
        print(f"  {'─'*55}")

        for i, ind in enumerate(risks, 1):
            icon = _STATUS_ICON.get(ind["status"], "[?]")
            dim_tag = f"[{ind.get('dimension', '')}]" if ind.get("dimension") else ""
            print(f"\n  [{i}] {icon} {ind['status']} {dim_tag} {ind['indicator']}")
            print(f"      说明：{ind['detail']}")
            if ind.get("actual_value") is not None:
                print(f"      实际值：{ind['actual_value']}")
            if ind.get("threshold"):
                print(f"      阈值：{ind['threshold']}")
            if ind.get("formula"):
                print(f"      公式：{ind['formula']}")
            if ind.get("data_source"):
                print(f"      数据来源：{ind['data_source']}")
            if show_sensitivity and ind.get("sensitivity"):
                s = ind["sensitivity"]
                print(f"      敏感性：乐观={s['optimistic']}月 | "
                      f"基准={s['base']}月 | 悲观={s['pessimistic']}月")
    else:
        print(f"\n  [OK] 结论：暂未识别现金流风险")
        print(f"  全部 10 项指标均处于正常或数据不足状态。")

    print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
