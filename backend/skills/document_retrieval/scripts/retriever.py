"""
Document Retrieval Skill — 招股书 PDF 提取、章节识别、模式匹配与语义搜索
======================================================================

独立运行::

    python retriever.py <pdf_path>
    python retriever.py <pdf_path> --sections
    python retriever.py <pdf_path> --financial
    python retriever.py <pdf_path> -q "募集资金" --multi "风险因素"
    python retriever.py <pdf_path> --summary --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from typing import Any

import pdfplumber

# ======================================================================
# HK IPO Prospectus — standard section patterns
# ======================================================================

_SECTION_PATTERNS: list[tuple[str, str]] = [
    # (section_key, regex_pattern)
    ("definitions", r"释义|定義|词汇"),
    ("risk_factors", r"风险因素|風險因素"),
    ("waivers", r"豁免|豁免严格遵守"),
    ("company_info", r"公司资料|公司資料"),
    ("industry_overview", r"行业概览|行業概覽|市场概览"),
    ("history_structure", r"历史.*发展.*架构|歷史.*發展.*架構|公司架构"),
    ("business", r"业\s*务(?!模式)|業\s*務(?!模式)|业务概[览覽]|業務概[览覽]"),
    ("financial_info", r"财务资料|財務資料|财务信息|財務信息"),
    ("financial_statements", r"合并财务|合併財務|综合财务|綜合財務|财务报表|財務報表"),
    ("use_of_proceeds", r"募集资金|所得款项用途|未來計劃|未来计划"),
    ("directors", r"董事|高级管理层|高級管理層"),
    ("share_capital", r"股本|股份"),
    ("substantial_shareholders", r"主要股东|主要股東"),
    ("underwriting", r"包销|包銷|承销|承銷"),
    ("connected_transactions", r"关联交易|關聯交易"),
    ("dividend_policy", r"股息|股利|分红"),
    ("legal_proceedings", r"法律诉讼|法律訴訟|重大诉讼"),
    ("appendix", r"附录|附錄|附件"),
]

# ======================================================================
# Financial data pattern definitions
# ======================================================================

_FINANCIAL_PATTERNS: dict[str, str] = {
    "hkd_amount": r"港[币幣]\s*[\(（]?[HK\$]*\s*[\d,]+\.?\d*\s*[\(（]?[百千万亿萬億]?[元]?[\)）]?",
    "hkd_amount_simple": r"HK\$\s*[\d,]+\.?\d*\s*[百千万亿萬億]?",
    "rmb_amount": r"人民币\s*[\d,]+\.?\d*\s*[百千万亿萬億]?[元]?",
    "date_cn": r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日",
    "date_iso": r"\d{4}[-/]\d{1,2}[-/]\d{1,2}",
    "percentage": r"[\d]+\.[\d]{1,2}\s*%",
    "percentage_range": r"[\d.]+%\s*[～\-~至到]\s*[\d.]+%",
    "stock_code": r"[0-9]{4,5}\.HK",
    "fiscal_year": r"(?:截至|截至|财政年度|財政年度|FY|FYE)\s*\d{4}",
    "total_assets": r"总资产[：:\s]*[\d,]+|總資產[：:\s]*[\d,]+",
    "revenue": r"(?:收入|营收|營業收入|收益)[：:\s]*[\d,]+",
    "net_profit": r"(?:净利润|净利|純利|纯利)[：:\s]*[\d,]+",
    "total_equity": r"(?:权益总[额額]|權益總[额額]|净资产|淨資產)[：:\s]*[\d,]+",
}


# ======================================================================
# DocRetriever
# ======================================================================


class DocRetriever:
    """Extract tables, sections, financial data, and search text
    from HK IPO prospectus PDFs."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self._pages: list[Any] = []          # pdfplumber page objects
        self._full_text: str = ""            # cached concatenated text
        self._page_texts: list[str] = []     # text per page
        self._page_count: int = 0
        self._loaded: bool = False

    # ------------------------------------------------------------------
    # Public API — Load
    # ------------------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """Open PDF, cache page objects, full text, and per-page text.

        Returns a summary dict with page_count, total_chars, is_text_searchable.
        """
        self._pages = []
        self._page_texts = []
        texts: list[str] = []
        with pdfplumber.open(self.file_path) as pdf:
            self._page_count = len(pdf.pages)
            for page in pdf.pages:
                self._pages.append(page)
                t = page.extract_text()
                if t:
                    texts.append(t)
                    self._page_texts.append(t)
                else:
                    self._page_texts.append("")
        self._full_text = "\n".join(texts)
        self._loaded = True
        return {
            "file_path": self.file_path,
            "page_count": self._page_count,
            "total_chars": len(self._full_text),
            "is_text_searchable": len(self._full_text) > 100,
        }

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ------------------------------------------------------------------
    # Public API — Document Summary (NEW)
    # ------------------------------------------------------------------

    def get_summary(self) -> dict[str, Any]:
        """Return a high-level overview of the prospectus PDF.

        Includes page count, text statistics, section distribution,
        and table count (quick scan).
        """
        self._ensure_loaded()

        # Quick table count (first 5 pages for speed)
        table_count = 0
        try:
            with pdfplumber.open(self.file_path) as pdf:
                for page in pdf.pages:
                    tables = page.extract_tables()
                    table_count += len(tables)
        except Exception:
            table_count = -1

        # Section detection
        sections = self.detect_sections()

        # Text statistics
        total_chars = len(self._full_text)
        pages_with_text = sum(1 for t in self._page_texts if t.strip())
        avg_chars_per_page = total_chars / max(self._page_count, 1)

        return {
            "file_path": self.file_path,
            "page_count": self._page_count,
            "total_chars": total_chars,
            "pages_with_text": pages_with_text,
            "blank_pages": self._page_count - pages_with_text,
            "avg_chars_per_page": round(avg_chars_per_page, 1),
            "table_count": table_count,
            "sections_found": len(sections),
            "section_list": [
                {"section": key, "label": info["label"], "page": info["page"],
                 "line_count": info["line_count"]}
                for key, info in sections.items()
            ],
        }

    # ------------------------------------------------------------------
    # Public API — Section Detection (NEW)
    # ------------------------------------------------------------------

    def detect_sections(self) -> dict[str, dict[str, Any]]:
        """Identify standard HK IPO prospectus sections in the document.

        Returns a dict keyed by section_id, each containing label,
        first-occurrence page, match text, and approximate line count.
        """
        self._ensure_loaded()

        found: dict[str, dict[str, Any]] = {}

        for section_key, pattern in _SECTION_PATTERNS:
            matches = list(re.finditer(pattern, self._full_text, re.IGNORECASE))
            if not matches:
                continue

            # Find which page the first match is on
            first_match_pos = matches[0].start()
            char_count = 0
            page_num = 1
            for i, pt in enumerate(self._page_texts):
                char_count += len(pt) + 1  # +1 for newline
                if char_count > first_match_pos:
                    page_num = i + 1
                    break

            # Estimate line count (from first match to next section or 2000 chars)
            start = matches[0].start()
            end = min(start + 2000, len(self._full_text))
            section_text = self._full_text[start:end]
            line_count = len(section_text.split("\n"))

            label = _SECTION_LABELS.get(section_key, section_key)
            found[section_key] = {
                "label": label,
                "page": page_num,
                "match_count": len(matches),
                "first_line": self._full_text[
                    max(0, start): min(start + 120, len(self._full_text))
                ].strip().replace("\n", " "),
                "line_count": line_count,
            }

        return found

    # ------------------------------------------------------------------
    # Public API — Financial Pattern Search (NEW)
    # ------------------------------------------------------------------

    def search_financial_patterns(
        self, pattern_types: list[str] | None = None
    ) -> dict[str, Any]:
        """Extract structured financial data using regex patterns.

        Parameters
        ----------
        pattern_types : list[str] | None
            Specific pattern keys to search (see _FINANCIAL_PATTERNS).
            If None, searches all patterns.

        Returns
        -------
        dict with pattern_name → {matches, samples, total_occurrences}.
        """
        self._ensure_loaded()

        if pattern_types is None:
            pattern_types = list(_FINANCIAL_PATTERNS.keys())

        results: dict[str, Any] = {}
        for ptype in pattern_types:
            if ptype not in _FINANCIAL_PATTERNS:
                continue
            pattern = _FINANCIAL_PATTERNS[ptype]
            matches = list(re.finditer(pattern, self._full_text, re.IGNORECASE))
            samples = [m.group(0).strip() for m in matches[:10]]
            # Deduplicate samples while preserving order
            seen: set[str] = set()
            unique_samples: list[str] = []
            for s in samples:
                if s not in seen:
                    seen.add(s)
                    unique_samples.append(s)

            label = _PATTERN_LABELS.get(ptype, ptype)
            results[ptype] = {
                "label": label,
                "total_occurrences": len(matches),
                "samples": unique_samples,
            }

        return results

    # ------------------------------------------------------------------
    # Public API — Find Financial Sections (NEW)
    # ------------------------------------------------------------------

    def find_financial_sections(self) -> dict[str, Any]:
        """Locate pages containing key financial statements.

        Scans for patterns indicating:
        - 合并资产负债表 / Consolidated Balance Sheet
        - 合并利润表 / 综合损益表 / Income Statement
        - 合并现金流量表 / Cash Flow Statement
        - 合并权益变动表 / Statement of Changes in Equity
        - 财务报表附注 / Notes to Financial Statements
        """
        self._ensure_loaded()

        fin_markers = {
            "balance_sheet": {
                "label": "资产负债表 / Balance Sheet",
                "patterns": [r"资产负债[表負債]|Balance\s*Sheet|財務狀況[表表]"],
            },
            "income_statement": {
                "label": "利润表 / 损益表 / Income Statement",
                "patterns": [r"综合损益|綜合損益|损[益]*表|損[益]*表|Income\s*Statement|P[\.\s]*L"],
            },
            "cashflow_statement": {
                "label": "现金流量表 / Cash Flow Statement",
                "patterns": [r"现金流[量]*表|現金流[量]*表|Cash\s*Flow\s*Statement"],
            },
            "equity_statement": {
                "label": "权益变动表 / Statement of Changes in Equity",
                "patterns": [r"权益.*变动|權益.*變動|Changes?\s*in\s*Equity"],
            },
            "notes": {
                "label": "财务报表附注 / Notes",
                "patterns": [r"财务报表附注|財務報表附註|Notes?\s*to\s*(the\s*)?Financial\s*Statements"],
            },
        }

        results: dict[str, Any] = {}
        for key, info in fin_markers.items():
            found_pages: list[int] = []
            for page_num, page_text in enumerate(self._page_texts, start=1):
                for pat in info["patterns"]:
                    if re.search(pat, page_text, re.IGNORECASE):
                        if page_num not in found_pages:
                            found_pages.append(page_num)
                        break
            results[key] = {
                "label": info["label"],
                "pages": found_pages,
                "found": len(found_pages) > 0,
                "best_page": found_pages[0] if found_pages else None,
            }

        return results

    # ------------------------------------------------------------------
    # Public API — Table extraction
    # ------------------------------------------------------------------

    def extract_tables(
        self,
        page_range: tuple[int, int] | None = None,
        clean: bool = False,
    ) -> list[dict[str, Any]]:
        """Return every table in the PDF as a list of dicts.

        Parameters
        ----------
        page_range : (start, end) or None
            Optional 1-indexed inclusive page range. None = all pages.
        clean : bool
            If True, apply table cleaning (merged cell handling,
            number normalization).

        Each dict::

            {
                "page": int,
                "table_index": int,
                "rows": list[list[str | None]],
                "row_count": int,
                "col_count": int,
            }
        """
        self._ensure_loaded()

        results: list[dict[str, Any]] = []
        with pdfplumber.open(self.file_path) as pdf:
            pages_to_scan = pdf.pages
            if page_range:
                start, end = page_range
                pages_to_scan = pdf.pages[max(0, start - 1):end]

            for page_num_offset, page in enumerate(pages_to_scan):
                actual_page = (page_range[0] if page_range else 1) + page_num_offset
                tables = page.extract_tables()
                for idx, table in enumerate(tables):
                    rows = self._clean_table(table) if clean else table
                    # Filter fully empty rows
                    rows = [r for r in rows if any(c and str(c).strip() for c in r)]
                    results.append({
                        "page": actual_page,
                        "table_index": idx,
                        "rows": rows,
                        "row_count": len(rows),
                        "col_count": max((len(r) for r in rows), default=0),
                    })
        return results

    # ------------------------------------------------------------------
    # Public API — Keyword search
    # ------------------------------------------------------------------

    def search_context(self, query: str, window: int = 200) -> list[str]:
        """Return text snippets around each occurrence of *query*.

        Parameters
        ----------
        query : str
            Keyword or short phrase to locate.
        window : int
            Characters to include before & after the match.

        Returns
        -------
        list[str]
            One snippet per match, with the query highlighted via ``>>>``.
        """
        self._ensure_loaded()

        snippets: list[str] = []
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        for m in pattern.finditer(self._full_text):
            start = max(0, m.start() - window)
            end = min(len(self._full_text), m.end() + window)
            snippet = self._full_text[start:end]
            snippet = (
                snippet[: m.start() - start]
                + " >>>" + query + "<<< "
                + snippet[m.end() - start :]
            )
            snippets.append(snippet)
        return snippets

    # ------------------------------------------------------------------
    # Public API — Multi-keyword search (NEW)
    # ------------------------------------------------------------------

    def search_multi(
        self,
        keywords: list[str],
        mode: str = "and",
        near_window: int = 300,
    ) -> dict[str, Any]:
        """Search with multiple keywords using AND/OR/NEAR logic.

        Parameters
        ----------
        keywords : list[str]
            List of keywords or phrases to combine.
        mode : str
            'and' — all keywords must appear on the same page.
            'or' — any keyword match (union of results).
            'near' — keywords must appear within near_window characters.
        near_window : int
            Character window for 'near' mode (default 300).

        Returns
        -------
        dict with mode, keywords, total_matches, and results per page.
        """
        self._ensure_loaded()

        result: dict[str, Any] = {
            "mode": mode,
            "keywords": keywords,
            "total_matches": 0,
            "pages": [],
        }

        if mode == "or":
            # Union: collect all unique pages where any keyword appears
            all_pages: set[int] = set()
            for kw in keywords:
                for page_num, pt in enumerate(self._page_texts, start=1):
                    if re.search(re.escape(kw), pt, re.IGNORECASE):
                        all_pages.add(page_num)
            result["total_matches"] = len(all_pages)
            result["pages"] = sorted(all_pages)

        elif mode == "and":
            # Intersection: pages where ALL keywords appear
            page_sets: list[set[int]] = []
            for kw in keywords:
                pages_with_kw: set[int] = set()
                for page_num, pt in enumerate(self._page_texts, start=1):
                    if re.search(re.escape(kw), pt, re.IGNORECASE):
                        pages_with_kw.add(page_num)
                page_sets.append(pages_with_kw)
            common = page_sets[0] if page_sets else set()
            for ps in page_sets[1:]:
                common = common & ps
            result["total_matches"] = len(common)
            result["pages"] = sorted(common)

        elif mode == "near":
            # Near: keywords within near_window chars in the full text
            near_matches: list[dict[str, Any]] = []
            combined_pattern = "|".join(re.escape(kw) for kw in keywords)
            for m in re.finditer(combined_pattern, self._full_text, re.IGNORECASE):
                start = max(0, m.start() - near_window // 2)
                end = min(len(self._full_text), m.end() + near_window // 2)
                region = self._full_text[start:end]
                # Check if all keywords appear in this region
                if all(re.search(re.escape(kw), region, re.IGNORECASE) for kw in keywords):
                    # Find page number
                    char_count = 0
                    page_num = 1
                    for i, pt in enumerate(self._page_texts):
                        char_count += len(pt) + 1
                        if char_count > m.start():
                            page_num = i + 1
                            break
                    snippet = region[:300].replace("\n", " ")
                    near_matches.append({
                        "page": page_num,
                        "snippet": snippet,
                    })
            result["total_matches"] = len(near_matches)
            result["matches"] = near_matches[:20]

        return result

    # ==================================================================
    # Internal — Table cleaning (NEW)
    # ==================================================================

    @staticmethod
    def _clean_table(rows: list[list[str | None]]) -> list[list[str | None]]:
        """Clean a raw pdfplumber table.

        Operations:
        - Replace None with empty string
        - Strip whitespace
        - Normalize merged-cell duplicates (same value repeated in adjacent columns)
        - Remove fully empty rows
        """
        cleaned: list[list[str | None]] = []
        for row in rows:
            new_row: list[str | None] = []
            prev_val: str | None = None
            for cell in row:
                val = str(cell).strip() if cell else ""
                # If same as previous cell, might be a merge artifact → keep first, blank rest
                if val and prev_val and val == prev_val and len(new_row) > 0:
                    new_row.append("")  # merge artifact
                else:
                    new_row.append(val if val else None)
                    if val:
                        prev_val = val
            cleaned.append(new_row)
        return cleaned


# ======================================================================
# Section & pattern label maps
# ======================================================================

_SECTION_LABELS: dict[str, str] = {
    "definitions": "释义/定义",
    "risk_factors": "风险因素",
    "waivers": "豁免遵守上市规则",
    "company_info": "公司资料",
    "industry_overview": "行业概览",
    "history_structure": "历史及公司架构",
    "business": "业务",
    "financial_info": "财务资料",
    "financial_statements": "合并财务报表",
    "use_of_proceeds": "募集资金用途",
    "directors": "董事及管理层",
    "share_capital": "股本",
    "substantial_shareholders": "主要股东",
    "underwriting": "包销/承销",
    "connected_transactions": "关联交易",
    "dividend_policy": "股息政策",
    "legal_proceedings": "法律诉讼",
    "appendix": "附录",
}

_PATTERN_LABELS: dict[str, str] = {
    "hkd_amount": "港元金额",
    "hkd_amount_simple": "港元金额(HK$)",
    "rmb_amount": "人民币金额",
    "date_cn": "中文日期",
    "date_iso": "ISO日期",
    "percentage": "百分比",
    "percentage_range": "百分比区间",
    "stock_code": "股票代码",
    "fiscal_year": "财政年度",
    "total_assets": "总资产金额",
    "revenue": "营业收入金额",
    "net_profit": "净利润金额",
    "total_equity": "权益总额",
}


# ======================================================================
# Standalone CLI
# ======================================================================


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DocRetriever — 招股书 PDF 提取、章节识别、模式匹配",
    )
    parser.add_argument(
        "pdf_path",
        help="Path to the IPO prospectus PDF",
    )
    parser.add_argument(
        "--query", "-q", default=None,
        help="Keyword / phrase to search for",
    )
    parser.add_argument(
        "--multi", "-m", nargs="+", default=None,
        help="Multiple keywords (combine with --mode)",
    )
    parser.add_argument(
        "--mode", default="and", choices=["and", "or", "near"],
        help="Multi-keyword mode: and/or/near (default: and)",
    )
    parser.add_argument(
        "--near-window", type=int, default=300,
        help="Character window for --mode near (default: 300)",
    )
    parser.add_argument(
        "--tables-only", "-t", action="store_true",
        help="Only extract tables, skip text search",
    )
    parser.add_argument(
        "--clean", "-c", action="store_true",
        help="Apply table cleaning (merge cells, normalize numbers)",
    )
    parser.add_argument(
        "--pages", default=None,
        help="Page range for table extraction (e.g. '40-55')",
    )
    parser.add_argument(
        "--sections", "-s", action="store_true",
        help="Detect and list standard prospectus sections",
    )
    parser.add_argument(
        "--financial", "-f", action="store_true",
        help="Extract financial data patterns (amounts, dates, percentages)",
    )
    parser.add_argument(
        "--fin-sections", action="store_true",
        help="Locate pages of key financial statements",
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="Show document overview summary",
    )
    parser.add_argument(
        "--window", "-w", type=int, default=200,
        help="Context window size for search snippets (default: 200)",
    )
    parser.add_argument(
        "--json", "-j", action="store_true",
        help="Output results as JSON",
    )
    return parser


def _parse_page_range(range_str: str | None) -> tuple[int, int] | None:
    """Parse '40-55' → (40, 55)."""
    if not range_str:
        return None
    parts = range_str.split("-")
    if len(parts) == 2:
        return (int(parts[0].strip()), int(parts[1].strip()))
    return None


def _print_tables(tables: list[dict[str, Any]], pdf_path: str) -> None:
    print(f"\n{'='*60}")
    print(f"  PDF: {pdf_path}")
    print(f"  Tables found: {len(tables)}")
    print(f"{'='*60}")
    for t in tables:
        print(f"\n--- Page {t['page']}, Table {t['table_index']} "
              f"({t['row_count']} rows, {t['col_count']} cols) ---")
        for row in t["rows"][:10]:
            print(" | ".join(str(c) if c else "" for c in row))
        if t["row_count"] > 10:
            print(f"  ... ({t['row_count'] - 10} more rows)")


def _print_sections(sections: dict[str, dict[str, Any]]) -> None:
    print(f"\n{'='*60}")
    print(f"  招股书章节识别 — {len(sections)} 个章节")
    print(f"{'='*60}")
    for key, info in sections.items():
        print(f"\n  [{info['label']}]  — 第 {info['page']} 页 "
              f"({info['match_count']} 处匹配, ~{info['line_count']} 行)")
        print(f"    {info['first_line'][:100]}...")


def _print_financial(patterns: dict[str, Any]) -> None:
    print(f"\n{'='*60}")
    print(f"  财务数据模式匹配")
    print(f"{'='*60}")
    for ptype, info in patterns.items():
        tag = "[OK]" if info["total_occurrences"] > 0 else "[--]"
        print(f"\n  {tag} {info['label']}: {info['total_occurrences']} 处")
        for s in info["samples"][:5]:
            print(f"      {s}")
        if len(info["samples"]) > 5:
            print(f"      ... (+{len(info['samples']) - 5} more unique)")


def _print_fin_sections(fin: dict[str, Any]) -> None:
    print(f"\n{'='*60}")
    print(f"  关键财务报表定位")
    print(f"{'='*60}")
    for key, info in fin.items():
        tag = "[OK]" if info["found"] else "[--]"
        pages_str = ", ".join(str(p) for p in info["pages"]) if info["pages"] else "未找到"
        print(f"  {tag} {info['label']}: 第 {pages_str} 页")


def main() -> None:
    parser = _build_cli()
    args = parser.parse_args()

    retriever = DocRetriever(args.pdf_path)

    # Load first (required for all operations)
    try:
        retriever.load()
    except FileNotFoundError:
        print(f"ERROR: PDF not found — {args.pdf_path}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: Failed to load PDF — {exc}", file=sys.stderr)
        sys.exit(1)

    output: dict[str, Any] = {}

    # --summary
    if args.summary:
        summary = retriever.get_summary()
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            _print_summary(summary)
        return

    # --sections
    if args.sections:
        sections = retriever.detect_sections()
        if args.json:
            output["sections"] = {
                key: {"label": info["label"], "page": info["page"],
                      "matches": info["match_count"]}
                for key, info in sections.items()
            }
        else:
            _print_sections(sections)

    # --financial
    if args.financial:
        patterns = retriever.search_financial_patterns()
        if args.json:
            output["financial_patterns"] = patterns
        else:
            _print_financial(patterns)

    # --fin-sections
    if args.fin_sections:
        fin = retriever.find_financial_sections()
        if args.json:
            output["financial_sections"] = fin
        else:
            _print_fin_sections(fin)

    # --tables
    page_range = _parse_page_range(args.pages)
    tables = retriever.extract_tables(page_range=page_range, clean=args.clean)
    if args.json:
        output["tables"] = [
            {"page": t["page"], "index": t["table_index"],
             "row_count": t["row_count"], "col_count": t["col_count"],
             "rows": t["rows"]}
            for t in tables
        ]
    elif args.tables_only or not any([args.query, args.multi, args.json]):
        _print_tables(tables, args.pdf_path)

    # --multi keyword search
    if args.multi:
        multi_result = retriever.search_multi(
            args.multi, mode=args.mode, near_window=args.near_window,
        )
        if args.json:
            output["multi_search"] = multi_result
        else:
            _print_multi_search(multi_result)

    # --query (single keyword search)
    if args.query:
        snippets = retriever.search_context(args.query, window=args.window)
        if args.json:
            output["search"] = {
                "query": args.query,
                "matches": len(snippets),
                "snippets": snippets[:20],
            }
        else:
            print(f"\n{'='*60}")
            print(f"  Search: \"{args.query}\" — {len(snippets)} matches")
            print(f"{'='*60}")
            for i, s in enumerate(snippets[:20], 1):
                print(f"\n[{i}] {s.strip()}")
            if len(snippets) > 20:
                print(f"\n  ... ({len(snippets) - 20} more matches)")

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))


# ======================================================================
# Pretty-print helpers
# ======================================================================


def _print_summary(s: dict[str, Any]) -> None:
    print(f"\n{'='*60}")
    print(f"  招股书概览")
    print(f"{'='*60}")
    print(f"  文件: {s['file_path']}")
    print(f"  页数: {s['page_count']}  "
          f"（有文本 {s['pages_with_text']} 页, "
          f"空白 {s['blank_pages']} 页）")
    print(f"  总字符数: {s['total_chars']:,}  "
          f"（每页平均 {s['avg_chars_per_page']:.0f} 字符）")
    print(f"  表格数量: {s['table_count']}")
    print(f"  识别章节: {s['sections_found']} 个")

    if s.get("section_list"):
        print(f"\n  {'─'*50}")
        print(f"  章节分布:")
        for sec in s["section_list"]:
            print(f"    第 {sec['page']:>3d} 页  [{sec['label']}]")


def _print_multi_search(result: dict[str, Any]) -> None:
    print(f"\n{'='*60}")
    print(f"  多关键词搜索 (mode={result['mode']})")
    print(f"  关键词: {', '.join(result['keywords'])}")
    print(f"  匹配: {result['total_matches']} 处")
    print(f"{'='*60}")

    if result["mode"] in ("and", "or"):
        pages = result.get("pages", [])
        print(f"  匹配页码: {', '.join(str(p) for p in pages[:30])}")
        if len(pages) > 30:
            print(f"  ... (+{len(pages) - 30} more pages)")

    elif result["mode"] == "near":
        for i, m in enumerate(result.get("matches", [])[:10], 1):
            print(f"\n  [{i}] 第 {m['page']} 页")
            print(f"      {m['snippet'][:200]}...")


if __name__ == "__main__":
    main()
