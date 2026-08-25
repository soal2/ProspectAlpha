"""Import versioned HK IPO market source CSVs.

Usage:
  .venv/bin/python scripts/import_market_data.py --security ../数据字典/hksharedescription.csv --company ../数据字典/hkcompanyinfo.csv --prices ../数据字典/hkshareeodprices.csv
"""

from __future__ import annotations

import argparse
import json

from riskagent.backtesting.service import MarketImportRequest, service


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--security", required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--prices", required=True)
    args = parser.parse_args()
    print(json.dumps(service.import_market_data(MarketImportRequest(security_csv=args.security, company_csv=args.company, prices_csv=args.prices)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
