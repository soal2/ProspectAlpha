#!/usr/bin/env python3
"""
HK IPO Analyst — 港股招股书分析系统（一键端到端分析）

Usage::

    python main.py <prospectus.pdf>
    python main.py <prospectus.pdf> --industry-hint SaaS --ticker 0700.HK
    python main.py <prospectus.pdf> --mc 50000 --rev 8000 --ni 1200 --ebitda 2500
"""

from __future__ import annotations

import argparse
import sys

from orchestrator import IPOAnalysisOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser(description="HK IPO Prospectus Analyser")
    parser.add_argument("pdf_path", help="Path to the IPO prospectus PDF")
    parser.add_argument("--industry-hint", default=None,
                        help="Industry override (SaaS/医药/金融/地产/消费)")
    parser.add_argument("--ticker", default=None,
                        help="Stock ticker for sentiment lookup (e.g. 0700.HK)")
    parser.add_argument("--mc", "--market-cap", type=float, default=None,
                        help="Issuer market cap in HKD mn")
    parser.add_argument("--rev", "--revenue", type=float, default=None,
                        help="Issuer revenue in HKD mn")
    parser.add_argument("--ni", "--net-income", type=float, default=None,
                        help="Issuer net income in HKD mn")
    parser.add_argument("--ebitda", type=float, default=None,
                        help="Issuer EBITDA in HKD mn")
    args = parser.parse_args()

    orchestrator = IPOAnalysisOrchestrator(args.pdf_path)

    # Industry hint
    if args.industry_hint:
        orchestrator.full_text = (
            args.industry_hint + "\n" + orchestrator.doc_retriever._full_text
        )
        if not orchestrator.full_text.strip():
            orchestrator.doc_retriever.load()

    # Issuer metrics
    issuer_metrics = None
    if args.mc or args.rev or args.ni or args.ebitda:
        issuer_metrics = {
            "market_cap": args.mc or 50_000,
            "revenue": args.rev or 8_000,
            "net_income": args.ni or 1_200,
            "ebitda": args.ebitda or 2_500,
        }

    try:
        report_json = orchestrator.run(
            ticker=args.ticker,
            issuer_metrics=issuer_metrics,
            industry=args.industry_hint,
        )
        print(report_json)
    except FileNotFoundError:
        print(f"ERROR: PDF not found — {args.pdf_path}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
