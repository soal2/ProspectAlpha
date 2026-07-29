"""
Valuation Checker Skill — 对标同业估值（多维加权 + 结论输出 + 行业分层）
====================================================================

独立运行::

    python checker.py 0700.HK --compare --mc 50000 --rev 8000 --ni 1200 --ebitda 2500
    python checker.py 0700.HK --check --mc 50000 --rev 8000 --ni 1200 --ebitda 2500
    python checker.py --list
    python checker.py --list --industry saas
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

# ======================================================================
# Peer multiples database — organized by industry
# ======================================================================

_PEER_DB: dict[str, dict[str, Any]] = {
    # SaaS / Tech
    "0700.HK": {"pe": 22.5, "ps": 5.2, "ev_ebitda": 18.3, "pb": 4.1,
                "industry": "saas", "name": "腾讯控股"},
    "2688.HK": {"pe": 35.1, "ps": 8.7, "ev_ebitda": 28.9, "pb": 6.5,
                "industry": "saas", "name": "新奥能源"},
    "2013.HK": {"pe": 45.0, "ps": 10.5, "ev_ebitda": 35.0, "pb": 8.2,
                "industry": "saas", "name": "微盟集团"},
    "9923.HK": {"pe": 0.0, "ps": 3.8, "ev_ebitda": 20.5, "pb": 2.1,
                "industry": "saas", "name": "移卡"},
    # Biotech / Pharma
    "1801.HK": {"pe": 0.0, "ps": 12.3, "ev_ebitda": 0.0, "pb": 3.2,
                "industry": "医药", "name": "信达生物"},
    "2269.HK": {"pe": 48.2, "ps": 6.1, "ev_ebitda": 32.4, "pb": 5.8,
                "industry": "医药", "name": "药明生物"},
    "6160.HK": {"pe": 0.0, "ps": 8.5, "ev_ebitda": 45.0, "pb": 2.8,
                "industry": "医药", "name": "百济神州"},
    # Finance
    "0005.HK": {"pe": 9.8, "ps": 2.1, "ev_ebitda": 7.5, "pb": 0.8,
                "industry": "金融", "name": "汇丰控股"},
    "0011.HK": {"pe": 12.0, "ps": 3.4, "ev_ebitda": 10.2, "pb": 1.1,
                "industry": "金融", "name": "恒生银行"},
    "0388.HK": {"pe": 35.0, "ps": 14.0, "ev_ebitda": 25.0, "pb": 7.5,
                "industry": "金融", "name": "香港交易所"},
    # Property
    "0016.HK": {"pe": 8.5, "ps": 2.8, "ev_ebitda": 12.0, "pb": 0.45,
                "industry": "地产", "name": "新鸿基地产"},
    "0012.HK": {"pe": 7.2, "ps": 2.0, "ev_ebitda": 10.5, "pb": 0.35,
                "industry": "地产", "name": "恒基兆业地产"},
    # Consumer
    "0968.HK": {"pe": 18.5, "ps": 2.5, "ev_ebitda": 12.0, "pb": 3.2,
                "industry": "消费", "name": "信义光能"},
    "2020.HK": {"pe": 25.0, "ps": 4.2, "ev_ebitda": 16.0, "pb": 5.5,
                "industry": "消费", "name": "安踏体育"},
}

# Per-industry medians (computed from the above + domain knowledge)
_INDUSTRY_MEDIANS: dict[str, dict[str, float]] = {
    "saas":   {"pe": 30.0, "ps": 6.5, "ev_ebitda": 25.0, "pb": 5.0},
    "医药":    {"pe": 35.0, "ps": 9.0, "ev_ebitda": 30.0, "pb": 4.0},
    "金融":    {"pe": 12.0, "ps": 3.0, "ev_ebitda": 10.0, "pb": 1.0},
    "地产":    {"pe": 8.0,  "ps": 2.5, "ev_ebitda": 11.0, "pb": 0.5},
    "消费":    {"pe": 20.0, "ps": 3.5, "ev_ebitda": 14.0, "pb": 4.0},
    "default": {"pe": 15.0, "ps": 3.5, "ev_ebitda": 12.0, "pb": 2.0},
}

# Scoring weight for each multiple
_MULTIPLE_WEIGHTS = {"pe": 0.50, "ps": 0.25, "ev_ebitda": 0.25}


def _get_industry_peers(industry: str | None) -> list[dict[str, Any]]:
    """Return all peers for a given industry, or all if None."""
    result = []
    for ticker, info in _PEER_DB.items():
        if industry is None or info.get("industry") == industry:
            result.append({"ticker": ticker, **info})
    return result


def _compute_sector_median(industry: str | None) -> dict[str, Any]:
    """Compute actual median multiples from peer database for a given industry."""
    peers = _get_industry_peers(industry)
    if not peers:
        return dict(_INDUSTRY_MEDIANS["default"])

    metrics = {"pe": [], "ps": [], "ev_ebitda": [], "pb": []}
    for p in peers:
        for key in metrics:
            val = p.get(key, 0)
            if val and val > 0:
                metrics[key].append(val)

    medians: dict[str, Any] = {}
    for key, vals in metrics.items():
        if vals:
            sorted_vals = sorted(vals)
            medians[key] = round(sorted_vals[len(sorted_vals) // 2], 2)
        else:
            medians[key] = _INDUSTRY_MEDIANS.get(industry, _INDUSTRY_MEDIANS["default"])[key]
    return medians


# ======================================================================
# ValuationChecker
# ======================================================================


class ValuationChecker:
    """Multi-metric valuation comparison for HK IPO issuers with
    industry-aware peer matching, conclusion output, and weighted scoring."""

    PEER_MULTIPLES = _PEER_DB  # backward-compatible attribute

    # ------------------------------------------------------------------
    # Public API — Peer valuation
    # ------------------------------------------------------------------

    def get_peer_valuation(
        self, ticker: str, industry: str | None = None
    ) -> dict[str, Any]:
        """Return valuation multiples for *ticker*.

        Falls back to industry-specific median if ticker not in database.
        """
        ticker = ticker.upper().strip()

        if ticker in _PEER_DB:
            info = _PEER_DB[ticker]
            return {
                "ticker": ticker,
                "name": info.get("name", ""),
                "industry": info.get("industry", ""),
                "pe": info["pe"],
                "ps": info["ps"],
                "ev_ebitda": info["ev_ebitda"],
                "pb": info.get("pb"),
                "source": "lookup_table",
            }

        # Industry fallback
        medians = _INDUSTRY_MEDIANS.get(
            industry or "default", _INDUSTRY_MEDIANS["default"]
        )
        return {
            "ticker": ticker,
            "name": "",
            "industry": industry or "未知",
            "pe": medians["pe"],
            "ps": medians["ps"],
            "ev_ebitda": medians["ev_ebitda"],
            "pb": medians.get("pb"),
            "source": f"industry_median ({industry or 'default'})",
        }

    # ------------------------------------------------------------------
    # Public API — Peer range (NEW)
    # ------------------------------------------------------------------

    def get_peer_range(self, industry: str | None = None) -> dict[str, Any]:
        """Return min/max/median for all multiples in a given industry.

        Provides distribution context — not just one number but the range.
        """
        peers = _get_industry_peers(industry)
        if not peers:
            medians = _INDUSTRY_MEDIANS["default"]
            return {key: {"min": v, "max": v, "median": v, "count": 0}
                    for key, v in medians.items()}

        metrics = ["pe", "ps", "ev_ebitda", "pb"]
        result: dict[str, Any] = {}
        for key in metrics:
            vals = [p.get(key, 0) for p in peers if p.get(key, 0) > 0]
            if vals:
                sv = sorted(vals)
                result[key] = {
                    "min": round(sv[0], 2),
                    "max": round(sv[-1], 2),
                    "median": round(sv[len(sv) // 2], 2),
                    "count": len(vals),
                }
            else:
                result[key] = {"min": None, "max": None, "median": None, "count": 0}
        return result

    # ------------------------------------------------------------------
    # Public API — Issuer valuation
    # ------------------------------------------------------------------

    def get_issuer_valuation(
        self, issuer_metrics: dict[str, float]
    ) -> dict[str, Any]:
        """Compute valuation multiples for the issuer.

        Parameters
        ----------
        issuer_metrics : dict
            Keys: ``market_cap``, ``revenue``, ``net_income``,
            ``ebitda``, ``book_value`` (optional), ``growth_rate`` (optional).
            All in HKD mn.
        """
        mc = issuer_metrics.get("market_cap", 0)
        rev = issuer_metrics.get("revenue", 0)
        ni = issuer_metrics.get("net_income", 0)
        ebitda = issuer_metrics.get("ebitda", 0)
        bv = issuer_metrics.get("book_value")
        growth = issuer_metrics.get("growth_rate")

        result: dict[str, Any] = {
            "pe": round(mc / ni, 2) if ni and ni != 0 else None,
            "ps": round(mc / rev, 2) if rev and rev != 0 else None,
            "ev_ebitda": round(mc / ebitda, 2) if ebitda and ebitda != 0 else None,
        }
        if bv is not None and bv != 0:
            result["pb"] = round(mc / bv, 2)
        if growth is not None and result.get("pe") and growth != 0:
            result["peg"] = round(result["pe"] / growth, 2)
        return result

    # ------------------------------------------------------------------
    # Public API — Check valuation (NEW: conclusion output)
    # ------------------------------------------------------------------

    def check_valuation(
        self,
        issuer_metrics: dict[str, float],
        peer_ticker: str | None = None,
        industry: str | None = None,
    ) -> dict[str, Any]:
        """Run full valuation comparison and return a clear conclusion.

        Parameters
        ----------
        issuer_metrics : dict
            Issuer financials (market_cap, revenue, net_income, ebitda).
        peer_ticker : str or None
            Specific peer ticker to compare against. If None, uses industry median.
        industry : str or None
            Industry tag for peer selection (saas/医药/金融/地产/消费).

        Returns
        -------
        dict with ``verdict`` (估值合理/中性/偏高/过高),
        ``recommendation``, ``score``, ``comparisons``, ``peer_range``.
        """
        issuer = self.get_issuer_valuation(issuer_metrics)

        if peer_ticker:
            peer = self.get_peer_valuation(peer_ticker, industry=industry)
        else:
            # Use industry median composite
            medians = _compute_sector_median(industry)
            peer = {
                "ticker": f"{industry or 'all'}_median",
                "name": f"{industry or '全行业'}中位数" if industry else "全行业中位数",
                "industry": industry or "通用",
                "pe": medians.get("pe", 15.0),
                "ps": medians.get("ps", 3.5),
                "ev_ebitda": medians.get("ev_ebitda", 12.0),
                "pb": medians.get("pb"),
                "source": "computed_median",
            }

        # Compare each available multiple
        comparisons: list[dict[str, Any]] = []
        weighted_scores: list[tuple[float, float]] = []  # (score, weight)

        for mult_key, mult_label, weight_key in [
            ("pe", "P/E", "pe"),
            ("ps", "P/S", "ps"),
            ("ev_ebitda", "EV/EBITDA", "ev_ebitda"),
        ]:
            issuer_val = issuer.get(mult_key)
            peer_val = peer.get(mult_key)
            if issuer_val and peer_val and peer_val > 0:
                ratio = issuer_val / peer_val
                # Score: <0.7 = undervalued (100), 0.7-1.3 = fair (50-80), >1.3 = overvalued (<50)
                if ratio < 0.7:
                    sub_score = min(100, round((1 - ratio) * 150 + 50))
                    level = "显著折价"
                elif ratio < 0.9:
                    sub_score = round((1 - ratio) * 200 + 70)
                    level = "小幅折价"
                elif ratio <= 1.1:
                    sub_score = 50 + round((1 - ratio) * 100)
                    level = "基本持平"
                elif ratio <= 1.5:
                    sub_score = max(0, round((1 - ratio) * 100 + 50))
                    level = "小幅溢价"
                elif ratio <= 2.0:
                    sub_score = max(0, round((1 - ratio) * 80 + 40))
                    level = "明显溢价"
                else:
                    sub_score = max(0, round((1 - ratio) * 40 + 20))
                    level = "严重溢价"

                weight = _MULTIPLE_WEIGHTS.get(weight_key, 0.33)
                weighted_scores.append((sub_score, weight))
                comparisons.append({
                    "multiple": mult_label,
                    "issuer_value": issuer_val,
                    "peer_value": peer_val,
                    "ratio": round(ratio, 2),
                    "level": level,
                    "score": sub_score,
                    "weight": weight,
                })
            else:
                comparisons.append({
                    "multiple": mult_label,
                    "issuer_value": issuer_val,
                    "peer_value": peer_val,
                    "ratio": None,
                    "level": "数据不足",
                    "score": None,
                    "weight": _MULTIPLE_WEIGHTS.get(weight_key, 0.33),
                })

        # Weighted total score
        if weighted_scores:
            total_weight = sum(w for _, w in weighted_scores)
            normalized = [(s, w / total_weight) for s, w in weighted_scores]
            total_score = round(sum(s * w for s, w in normalized))
        else:
            total_score = 50  # neutral fallback

        # Verdict
        verdict, recommendation = self._make_verdict(total_score, comparisons)

        # Peer range for context
        peer_range = self.get_peer_range(industry)

        return {
            "verdict": verdict,
            "recommendation": recommendation,
            "score": total_score,
            "issuer": issuer,
            "peer": peer,
            "comparisons": comparisons,
            "peer_range": peer_range,
        }

    # ------------------------------------------------------------------
    # Verdict builder
    # ------------------------------------------------------------------

    @staticmethod
    def _make_verdict(
        score: float, comparisons: list[dict[str, Any]]
    ) -> tuple[str, str]:
        """Return (verdict_text, recommendation_text)."""
        # Count available comparisons
        available = [c for c in comparisons if c["ratio"] is not None]
        if not available:
            return ("数据不足，无法判断", "请补充发行人财务数据")

        # Check if all available metrics point in same direction
        undervalued = sum(1 for c in available if c["level"] in ("显著折价", "小幅折价"))
        overvalued = sum(1 for c in available if c["level"] in ("明显溢价", "严重溢价"))

        if score >= 80:
            return ("估值具备吸引力，多指标显示折价", "建议申购")
        elif score >= 65:
            return ("估值略低于同行，定价偏合理", "建议申购")
        elif score >= 45:
            return ("估值与同行基本持平，定价中性", "可选择性参与")
        elif score >= 30:
            return ("估值高于同行，存在一定溢价", "谨慎参与")
        elif overvalued >= len(available) * 0.5:
            return ("估值显著高于同行，多个指标显示严重溢价", "建议回避")
        else:
            return ("估值偏高，建议关注定价合理性", "谨慎参与")


# ======================================================================
# Standalone CLI
# ======================================================================


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ValuationChecker — 同业估值对标（多维加权+结论输出+行业分层）",
    )
    parser.add_argument("ticker", nargs="?", default=None,
                        help="Peer ticker (e.g. 0700.HK). Omit with --list.")
    parser.add_argument("--list", "-l", action="store_true",
                        help="List all known peers (optionally filtered by --industry)")
    parser.add_argument("--industry", "-i", default=None,
                        help="Industry tag for peer selection (saas/医药/金融/地产/消费)")
    parser.add_argument("--compare", "-c", action="store_true",
                        help="Compute issuer multiples for comparison")
    parser.add_argument("--check", action="store_true",
                        help="Conclusion-style output with verdict and recommendation")
    parser.add_argument("--mc", "--market-cap", type=float, default=50_000,
                        help="Issuer market cap in HKD mn")
    parser.add_argument("--rev", "--revenue", type=float, default=8_000,
                        help="Issuer revenue in HKD mn")
    parser.add_argument("--ni", "--net-income", type=float, default=1_200,
                        help="Issuer net income in HKD mn")
    parser.add_argument("--ebitda", type=float, default=2_500,
                        help="Issuer EBITDA in HKD mn")
    parser.add_argument("--bv", "--book-value", type=float, default=None,
                        help="Issuer book value in HKD mn (optional)")
    parser.add_argument("--json", "-j", action="store_true",
                        help="Output as JSON")
    return parser


def main() -> None:
    parser = _build_cli()
    args = parser.parse_args()
    checker = ValuationChecker()

    # --list
    if args.list:
        peers = _get_industry_peers(args.industry)
        if args.json:
            result = [{"ticker": p["ticker"], "name": p.get("name", ""),
                       "industry": p.get("industry", ""),
                       "pe": p["pe"], "ps": p["ps"],
                       "ev_ebitda": p["ev_ebitda"], "pb": p.get("pb")}
                      for p in peers]
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            industry_str = f" — {args.industry}" if args.industry else ""
            print(f"\n{'='*55}")
            print(f"  Known Peers{industry_str} ({len(peers)} stocks)")
            print(f"{'='*55}")
            print(f"  {'Ticker':<10} {'Name':<12s} {'P/E':>7s} {'P/S':>7s}"
                  f" {'EV/EBITDA':>10s} {'P/B':>6s}")
            print(f"  {'-'*48}")
            for p in peers:
                pb_str = f"{p.get('pb', 0):.1f}" if p.get("pb") else "N/A"
                print(f"  {p['ticker']:<10} {p.get('name', '')[:12]:<12s} "
                      f"{p['pe']:>7.1f} {p['ps']:>7.1f} "
                      f"{p['ev_ebitda']:>10.1f} {pb_str:>6s}")
        return

    if not args.ticker and not args.check:
        parser.print_help()
        print("\nERROR: ticker is required (or use --list / --check)", file=sys.stderr)
        sys.exit(1)

    issuer_metrics = {
        "market_cap": args.mc, "revenue": args.rev,
        "net_income": args.ni, "ebitda": args.ebitda,
    }
    if args.bv:
        issuer_metrics["book_value"] = args.bv

    # --check (conclusion-style)
    if args.check:
        result = checker.check_valuation(
            issuer_metrics,
            peer_ticker=args.ticker if args.ticker else None,
            industry=args.industry,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_check_result(result)
        return

    # --compare (traditional single-peer)
    peer = checker.get_peer_valuation(args.ticker)
    output: dict[str, Any] = {"peer": peer}

    if args.compare:
        issuer = checker.get_issuer_valuation(issuer_metrics)
        score = 60
        if issuer.get("pe") and peer.get("pe") and peer["pe"] > 0:
            ratio = issuer["pe"] / peer["pe"]
            score = min(100, max(0, round((1 - ratio) * 100 + 50)))
        if args.json:
            output["issuer"] = issuer
            output["comparison_score"] = score
        else:
            _print_report(peer, issuer, args, score)
    else:
        if args.json:
            pass
        else:
            print(f"\n  Peer: {peer['ticker']} ({peer.get('name', '')})"
                  f"  [source: {peer['source']}]")
            print(f"  P/E: {peer['pe']}  |  P/S: {peer['ps']}"
                  f"  |  EV/EBITDA: {peer['ev_ebitda']}"
                  f"  |  P/B: {peer.get('pb', 'N/A')}")

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))


# ======================================================================
# Pretty-print
# ======================================================================


def _print_report(peer: dict, issuer: dict, args: Any, score: float) -> None:
    print(f"\n  Peer: {peer['ticker']} ({peer.get('name', '')})"
          f"  [source: {peer['source']}]")
    print(f"  Peer P/E: {peer['pe']}  |  P/S: {peer['ps']}"
          f"  |  EV/EBITDA: {peer['ev_ebitda']}"
          f"  |  P/B: {peer.get('pb', 'N/A')}")
    print(f"\n  --- Issuer ---")
    print(f"  Market Cap: {args.mc:,.0f} Mn  |  Revenue: {args.rev:,.0f} Mn")
    print(f"  Net Income: {args.ni:,.0f} Mn  |  EBITDA: {args.ebitda:,.0f} Mn")
    print(f"  Issuer P/E: {issuer.get('pe', 'N/A')}  |  "
          f"P/S: {issuer.get('ps', 'N/A')}  |  "
          f"EV/EBITDA: {issuer.get('ev_ebitda', 'N/A')}")
    if issuer.get("pb"):
        print(f"  Issuer P/B: {issuer['pb']}")
    if issuer.get("pe") and peer.get("pe"):
        ratio = issuer["pe"] / peer["pe"]
        label = "undervalued" if ratio < 1 else "overvalued"
        print(f"  Issuer/Peer P/E ratio: {ratio:.2f}  ({label} vs peer)")
    print(f"  Comparison score: {score}/100")


def _print_check_result(result: dict[str, Any]) -> None:
    verdict = result["verdict"]
    rec = result["recommendation"]
    score = result["score"]
    peer = result.get("peer", {})
    issuer = result.get("issuer", {})

    rec_icon = {"建议申购": "[OK]", "可选择性参与": "[--]", "谨慎参与": "[?]", "建议回避": "[!!]"}

    print(f"\n{'='*60}")
    print(f"  估值对标报告")
    print(f"{'='*60}")

    # Verdict
    print(f"\n  {rec_icon.get(rec, '[?]')} 结论：{verdict}")
    print(f"  操作建议：{rec}")
    print(f"  综合评分：{score}/100")

    # Peer info
    print(f"\n  对标同行：{peer.get('ticker', '?')}"
          f" ({peer.get('name', '')})"
          f"  [{peer.get('source', '')}]")
    print(f"  ─────────────────────────────")

    # Comparison table
    print(f"  {'倍数':<12s} {'发行人':>8s} {'同行':>8s} {'比值':>8s} {'判定':<10s}")
    print(f"  {'-'*50}")
    for c in result.get("comparisons", []):
        iv = f"{c['issuer_value']:.1f}" if c['issuer_value'] else "N/A"
        pv = f"{c['peer_value']:.1f}" if c['peer_value'] else "N/A"
        ratio_str = f"{c['ratio']:.2f}x" if c['ratio'] else "N/A"
        print(f"  {c['multiple']:<12s} {iv:>8s} {pv:>8s} {ratio_str:>8s}  {c['level']}")

    # Peer range context
    pr = result.get("peer_range", {})
    if pr and pr.get("pe", {}).get("count", 0) > 0:
        print(f"\n  行业同行估值区间（{pr['pe']['count']}只）：")
        for mult_key, label in [("pe", "P/E"), ("ps", "P/S"), ("ev_ebitda", "EV/EBITDA")]:
            r = pr.get(mult_key, {})
            if r.get("count", 0) > 0:
                print(f"  {label:<6s}  最低 {r['min']:.1f}  |  "
                      f"中位 {r['median']:.1f}  |  最高 {r['max']:.1f}")

    print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
