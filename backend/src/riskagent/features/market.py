"""Pre-IPO market-window features from dated prices.

Rows without a parseable date, and rows after the cutoff, are left out.
Too few eligible observations stay missing; they are not a low-risk reading.
"""

from __future__ import annotations

from typing import Any

from riskagent.research.core import eligible_pre_cutoff, parse_published_date

_CURRENCY = {"人民币": "CNY", "人民幣": "CNY", "港元": "HKD", "港币": "HKD", "美元": "USD", "CNY": "CNY", "HKD": "HKD", "USD": "USD"}


def extract_pre_ipo_market_window(
    rows: list[dict[str, Any]] | None,
    *,
    data_cutoff_date: str | None,
) -> dict[str, Any]:
    eligible: list[tuple[str, float, str | None]] = []
    currencies: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        observed = parse_published_date(row.get("date") or row.get("as_of_date"))
        if not eligible_pre_cutoff(observed, data_cutoff_date or ""):
            continue
        close = row.get("close")
        if not isinstance(close, (int, float)) or isinstance(close, bool):
            continue
        currency = _CURRENCY.get(str(row.get("currency") or ""))
        if currency:
            currencies.add(currency)
        eligible.append((observed or "", float(close), currency))
    eligible.sort(key=lambda item: item[0])
    base = {
        "data_cutoff_date": data_cutoff_date or None,
        "observations": len(eligible),
        "missing_is_not_low_risk": True,
        "currency": next(iter(currencies)) if len(currencies) == 1 else None,
        "currency_consistent": len(currencies) <= 1,
    }
    if len(currencies) > 1 or len(eligible) < 2:
        reason = "价格币种不一致" if len(currencies) > 1 else "截止日前可用价格不足两期"
        return {
            **base,
            "status": "missing",
            "reason": reason,
            "window_start": eligible[0][0] if eligible else None,
            "window_end": eligible[-1][0] if eligible else None,
            "trailing_return": None,
        }
    start_close = eligible[0][1]
    end_close = eligible[-1][1]
    trailing = None if start_close == 0 else round(end_close / start_close - 1, 6)
    return {
        **base,
        "status": "available",
        "reason": None,
        "window_start": eligible[0][0],
        "window_end": eligible[-1][0],
        "trailing_return": trailing,
    }
