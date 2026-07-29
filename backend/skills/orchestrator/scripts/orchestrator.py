"""
IPO Analysis Orchestrator — 编排器，串联所有原子 Skill 并输出加权评分报告
======================================================================

调用各 skill 的最新 check_xxx 接口，聚合风险信号，输出综合结论。
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Dynamic imports
# ---------------------------------------------------------------------------
_SKILLS_ROOT = Path(__file__).resolve().parents[2]  # hk_ipo_analyst/


def _load_skill(skill_name: str, module_name: str, class_name: str) -> type:
    spec = importlib.util.spec_from_file_location(
        module_name,
        _SKILLS_ROOT / skill_name / "scripts" / f"{module_name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, class_name)


DocRetriever = _load_skill("skill2_document_retrieval", "retriever", "DocRetriever")
ValuationChecker = _load_skill("skill4_valuation_checker", "checker", "ValuationChecker")
CashflowAnalyzer = _load_skill("skill1_cashflow_analyzer", "analyzer", "CashflowAnalyzer")
SentimentScorer = _load_skill("skill3_sentiment_scorer", "scorer", "SentimentScorer")


# ---------------------------------------------------------------------------
# Strategy configuration
# ---------------------------------------------------------------------------

@dataclass
class StrategyWeights:
    label: str
    weight_document: float
    weight_valuation: float
    weight_cashflow: float
    weight_sentiment: float


STRATEGY_MAP: dict[str, StrategyWeights] = {
    "saas": StrategyWeights("SaaS / 软件", 0.15, 0.40, 0.25, 0.20),
    "医药": StrategyWeights("医药 / 生物科技", 0.10, 0.25, 0.45, 0.20),
    "金融": StrategyWeights("金融 / 银行", 0.20, 0.30, 0.30, 0.20),
    "地产": StrategyWeights("地产 / 建筑", 0.15, 0.30, 0.40, 0.15),
    "消费": StrategyWeights("消费 / 零售", 0.15, 0.35, 0.25, 0.25),
}

DEFAULT_STRATEGY = StrategyWeights("通用 / 默认", 0.20, 0.30, 0.25, 0.25)

# Score mapping for cashflow risk levels
_CASHFLOW_RISK_SCORE = {"正常": 85, "关注": 55, "预警": 25, "数据不足": 50}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class IPOAnalysisOrchestrator:
    """Top-level pipeline that wires all skills and produces a
    comprehensive weighted IPO evaluation report."""

    def __init__(self, pdf_path: str) -> None:
        self.pdf_path = pdf_path
        self.full_text: str = ""
        self.doc_retriever = DocRetriever(pdf_path)
        self.valuation_checker = ValuationChecker()
        self.cashflow_analyzer = CashflowAnalyzer()
        self.sentiment_scorer = SentimentScorer()
        self.skill_results: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Strategy routing
    # ------------------------------------------------------------------

    def strategy_router(self, text: str) -> StrategyWeights:
        """Detect industry from text and return matching weights.

        Uses keyword-score voting: each keyword match adds weight to
        its industry. The industry with the most matches wins.
        """
        text_lower = text.lower()
        scores: dict[str, int] = {"saas": 0, "医药": 0, "金融": 0, "地产": 0, "消费": 0}

        keyword_map = {
            "saas": ["saas", "软件即服务", "云计算", "订阅", "api", "平台"],
            "医药": ["医药", "生物科技", "制药", "临床", "疫苗", "医疗器械"],
            "金融": ["金融", "银行", "保险", "券商", "基金", "信托"],
            "地产": ["地产", "物业", "建筑", "房地产", "开发商"],
            "消费": ["消费", "零售", "餐饮", "品牌", "电商", "快消"],
        }

        for industry, keywords in keyword_map.items():
            for kw in keywords:
                if kw.lower() in text_lower:
                    scores[industry] += 1

        best_industry = max(scores, key=scores.get)
        if scores[best_industry] > 0:
            return STRATEGY_MAP[best_industry]
        return DEFAULT_STRATEGY

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    def run(self, ticker: str | None = None,
            issuer_metrics: dict[str, float] | None = None,
            industry: str | None = None) -> str:
        """Execute the full analysis pipeline.

        Parameters
        ----------
        ticker : str or None
            Stock ticker for sentiment/valuation lookup.
        issuer_metrics : dict or None
            Issuer financials for valuation comparison.
        industry : str or None
            Manual industry override.
        """
        # 0. Load document
        self.doc_retriever.load()
        self.full_text = self.doc_retriever._full_text

        # 1. Strategy
        strategy = self.strategy_router(self.full_text)
        industry_hint = industry or strategy.label.split(" / ")[0].lower()

        # 2. Run all skills
        self.skill_results["document"] = self._run_doc_extraction()
        self.skill_results["valuation"] = self._run_valuation(issuer_metrics, industry_hint)
        self.skill_results["cashflow"] = self._run_cashflow()
        self.skill_results["sentiment"] = self._run_sentiment(ticker)

        # 3. Risk summary
        risk_summary = self._build_risk_summary()

        # 4. Weighted total
        total_score = self._compute_weighted_total(strategy)

        # 5. Confidence
        confidence = self._compute_confidence()

        # 6. Build report
        verdict = self._verdict(total_score)
        verdict_detail = self._verdict_detail(verdict)

        report = {
            "meta": {
                "pdf_path": self.pdf_path,
                "industry_strategy": strategy.label,
                "ticker": ticker,
            },
            "weights": {
                "document": strategy.weight_document,
                "valuation": strategy.weight_valuation,
                "cashflow": strategy.weight_cashflow,
                "sentiment": strategy.weight_sentiment,
            },
            "scores": {
                "document": self.skill_results["document"]["score"],
                "valuation": self.skill_results["valuation"]["score"],
                "cashflow": self.skill_results["cashflow"]["score"],
                "sentiment": self.skill_results["sentiment"]["score"],
            },
            "weighted_total": round(total_score, 2),
            "verdict": {
                "level": verdict_detail["level"],
                "summary": verdict_detail["summary"],
                "action": verdict_detail["action"],
                "priority": verdict_detail["priority"],
            },
            "confidence": confidence,
            "risk_summary": risk_summary,
            "details": {
                "document": self.skill_results["document"],
                "valuation": self.skill_results["valuation"],
                "cashflow": self.skill_results["cashflow"],
                "sentiment": self.skill_results["sentiment"],
            },
        }
        return json.dumps(report, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # Skill wrappers
    # ------------------------------------------------------------------

    def _run_doc_extraction(self) -> dict[str, Any]:
        """Use skill2's enhanced methods for richer document scoring."""
        try:
            summary = self.doc_retriever.get_summary()
            sections = self.doc_retriever.detect_sections()
            fin_sections = self.doc_retriever.find_financial_sections()
        except Exception:
            # Fallback for old retriever
            tables = self.doc_retriever.extract_tables()
            return {"tables_count": len(tables), "score": min(100, len(tables) * 5 + 50),
                    "sections_found": 0, "financial_statements_located": False, "status": "ok"}

        # Score: completeness of document
        score = 50  # baseline
        score += min(20, summary.get("page_count", 0) / 20)  # larger doc = more info
        score += min(15, summary.get("sections_found", 0) * 2)
        score += 10 if summary.get("is_text_searchable", False) else -20

        fin_located = sum(1 for k, v in fin_sections.items() if v.get("found"))
        score += fin_located * 5

        return {
            "summary": summary,
            "sections_found": summary.get("sections_found", 0),
            "financial_statements_located": fin_located,
            "financial_sections": {
                k: v.get("best_page") for k, v in fin_sections.items() if v.get("found")
            },
            "score": min(100, max(0, round(score))),
            "status": "ok",
        }

    def _run_valuation(self, issuer_metrics: dict[str, float] | None,
                       industry: str) -> dict[str, Any]:
        """Use skill4's check_valuation for richer comparison."""
        if issuer_metrics is None:
            issuer_metrics = {
                "market_cap": 50_000, "revenue": 8_000,
                "net_income": 1_200, "ebitda": 2_500,
            }

        try:
            result = self.valuation_checker.check_valuation(
                issuer_metrics, peer_ticker=None, industry=industry,
            )
            return {
                "score": result["score"],
                "verdict": result["verdict"],
                "recommendation": result["recommendation"],
                "comparisons": result.get("comparisons", []),
                "peer": result.get("peer", {}),
                "status": "ok",
            }
        except Exception:
            # Fallback
            peer = self.valuation_checker.get_peer_valuation("0700.HK")
            issuer = self.valuation_checker.get_issuer_valuation(issuer_metrics)
            score = 60
            if issuer.get("pe") and peer.get("pe"):
                ratio = issuer["pe"] / peer["pe"]
                score = min(100, max(0, round((1 - ratio) * 100 + 50)))
            return {"peer": peer, "issuer": issuer, "score": score, "status": "ok"}

    def _run_cashflow(self) -> dict[str, Any]:
        """Use skill1's check_risk for full 10-dimension risk analysis."""
        # Placeholder data — real impl extracts from prospectus
        cashflow_data = {
            "cash_and_equivalents": 2_400,
            "operating_cashflow": -800,
            "investing_cashflow": -200,
            "financing_cashflow": 1_500,
            "period_months": 12,
        }

        try:
            result = self.cashflow_analyzer.check_risk(cashflow_data)
            score = _CASHFLOW_RISK_SCORE.get(result.get("overall_risk", "数据不足"), 50)
            return {
                "score": score,
                "has_risk": result["has_risk"],
                "overall_risk": result["overall_risk"],
                "risk_counts": result.get("risk_counts", {}),
                "risks": result.get("risks", []),
                "conclusion": result["conclusion"],
                "status": "ok",
            }
        except Exception:
            runway = self.cashflow_analyzer.calculate_runway(cashflow_data)
            score_map = {"healthy": 90, "warning": 55, "critical": 20}
            score = score_map.get(runway["status"], 50)
            return {**runway, "score": score, "status": "ok"}

    def _run_sentiment(self, ticker: str | None) -> dict[str, Any]:
        """Use skill3's check_sentiment for conclusion-style output."""
        if ticker is None:
            ticker = "0700.HK"  # fallback

        try:
            result = self.sentiment_scorer.check_sentiment(ticker)
            score = round((result["score"] + 1) * 50)  # [-1,1] → [0,100]
            return {
                "score": score,
                "raw_score": result["score"],
                "conclusion": result["conclusion"],
                "recommendation": result["recommendation"],
                "signal": result["signal"],
                "confidence": result.get("confidence", {}),
                "industry_overlay": result.get("industry_overlay"),
                "status": "ok",
            }
        except Exception:
            sentiment = self.sentiment_scorer.get_sentiment_score(ticker)
            score = round((sentiment["score"] + 1) * 50)
            return {**sentiment, "score": score, "status": "ok"}

    # ------------------------------------------------------------------
    # Risk summary (NEW)
    # ------------------------------------------------------------------

    def _build_risk_summary(self) -> dict[str, Any]:
        """Aggregate risk signals across all dimensions."""
        risks: list[dict[str, Any]] = []

        # Cashflow risks
        cf = self.skill_results.get("cashflow", {})
        for r in cf.get("risks", []):
            risks.append({
                "source": "cashflow",
                "severity": r.get("status", ""),
                "indicator": r.get("indicator", ""),
                "detail": r.get("detail", ""),
            })

        # Sentiment risks
        sent = self.skill_results.get("sentiment", {})
        signal = sent.get("signal", "")
        if signal in ("bearish", "strongly_bearish"):
            risks.append({
                "source": "sentiment",
                "severity": "预警" if signal == "strongly_bearish" else "关注",
                "indicator": "申购情绪偏冷",
                "detail": sent.get("conclusion", ""),
            })

        # Valuation risks
        val = self.skill_results.get("valuation", {})
        v_score = val.get("score", 50)
        if v_score < 35:
            risks.append({
                "source": "valuation",
                "severity": "预警",
                "indicator": "估值显著偏高",
                "detail": val.get("verdict", ""),
            })
        elif v_score < 50:
            risks.append({
                "source": "valuation",
                "severity": "关注",
                "indicator": "估值偏高",
                "detail": val.get("verdict", ""),
            })

        # Document risks
        doc = self.skill_results.get("document", {})
        if doc.get("financial_statements_located", 0) < 3:
            risks.append({
                "source": "document",
                "severity": "关注",
                "indicator": "关键财务报表不完整",
                "detail": f"仅定位到 {doc.get('financial_statements_located', 0)}/5 类报表",
            })

        critical = sum(1 for r in risks if r["severity"] == "预警")
        warning = sum(1 for r in risks if r["severity"] == "关注")

        return {
            "total_risks": len(risks),
            "critical_count": critical,
            "warning_count": warning,
            "risks": risks,
            "has_critical": critical > 0,
        }

    # ------------------------------------------------------------------
    # Confidence (NEW)
    # ------------------------------------------------------------------

    def _compute_confidence(self) -> dict[str, Any]:
        """Assess reliability of the composite score."""
        factors: list[dict[str, Any]] = []

        # Check document quality
        doc = self.skill_results.get("document", {})
        if doc.get("sections_found", 0) < 3:
            factors.append({"factor": "文档质量", "level": "low",
                           "reason": f"仅识别 {doc.get('sections_found', 0)} 个章节，信息不完整"})

        # Check sentiment confidence
        sent = self.skill_results.get("sentiment", {})
        sent_conf = sent.get("confidence", {})
        if sent_conf.get("level") in ("medium", "low"):
            factors.append({"factor": "情绪评分", "level": sent_conf.get("level", "unknown"),
                           "reason": sent_conf.get("reason", sent_conf.get("label", ""))})

        # Check cashflow data
        cf = self.skill_results.get("cashflow", {})
        if cf.get("overall_risk") == "数据不足":
            factors.append({"factor": "现金流分析", "level": "low",
                           "reason": "现金流数据不足"})

        # Check valuation
        val = self.skill_results.get("valuation", {})
        available_comps = len(val.get("comparisons", []))
        if available_comps < 2:
            factors.append({"factor": "估值对标", "level": "medium",
                           "reason": f"仅 {available_comps} 个倍数可用于比较"})

        if not factors:
            return {"level": "high", "label": "高置信度", "factors": []}

        levels = [f["level"] for f in factors]
        if "low" in levels:
            overall = "low"
        elif "medium" in levels:
            overall = "medium"
        else:
            overall = "high"

        return {"level": overall, "label": {"high": "高置信度", "medium": "中等置信度", "low": "低置信度"}[overall],
                "factors": factors}

    # ------------------------------------------------------------------
    # Weighting & verdict
    # ------------------------------------------------------------------

    def _compute_weighted_total(self, w: StrategyWeights) -> float:
        scores = {
            "document": self.skill_results["document"]["score"],
            "valuation": self.skill_results["valuation"]["score"],
            "cashflow": self.skill_results["cashflow"]["score"],
            "sentiment": self.skill_results["sentiment"]["score"],
        }
        return (w.weight_document * scores["document"] +
                w.weight_valuation * scores["valuation"] +
                w.weight_cashflow * scores["cashflow"] +
                w.weight_sentiment * scores["sentiment"])

    @staticmethod
    def _verdict(total: float) -> str:
        if total >= 75:
            return "低危风险"
        elif total >= 50:
            return "中高危风险"
        else:
            return "极高危风险"

    @staticmethod
    def _verdict_detail(verdict: str) -> dict[str, str]:
        """Return recommendation and action for each risk level."""
        mapping = {
            "低危风险": {
                "level": "低危风险",
                "summary": "常规关注，无实质性利空",
                "action": "可正常参与打新",
                "priority": 3,
            },
            "中高危风险": {
                "level": "中高危风险",
                "summary": "重点跟踪，谨慎参与",
                "action": "建议小仓位或观望",
                "priority": 2,
            },
            "极高危风险": {
                "level": "极高危风险",
                "summary": "优先核查，一票否决打新",
                "action": "建议回避本次打新",
                "priority": 1,
            },
        }
        return mapping.get(verdict, mapping["中高危风险"])
