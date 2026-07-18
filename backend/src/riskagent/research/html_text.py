"""HTML text extraction with the standard library only.

Deliberately dependency-free (no bs4/lxml/readability): fetched pages only
need a plain-text body, the ``<title>``, and a best-effort published-date
hint for :class:`SourceCard`.  Anything fancier belongs in a later phase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional

_SKIPPED_ELEMENTS = {"script", "style", "noscript", "template", "svg", "head"}
_BLOCK_ELEMENTS = {
    "p", "div", "br", "li", "ul", "ol", "table", "tr", "td", "th",
    "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header",
    "footer", "blockquote", "pre", "figure", "figcaption", "hr",
}
# meta name/property values that commonly carry the publish timestamp,
# in priority order (first match wins).
_PUBLISHED_META_KEYS = (
    "article:published_time",
    "og:published_time",
    "publishdate",
    "publish_date",
    "publication_date",
    "pubdate",
    "date",
    "dc.date",
    "dc.date.issued",
)

_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


@dataclass
class ExtractedDocument:
    title: Optional[str] = None
    text: str = ""
    published_hint: Optional[str] = None
    published_hint_key: Optional[str] = None
    _chunks: list = field(default_factory=list, repr=False)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.doc = ExtractedDocument()
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        # rank of the meta key currently held, so higher-priority keys win
        self._published_rank: Optional[int] = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in _SKIPPED_ELEMENTS and tag != "head":
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            self._handle_meta(dict((k.lower(), v) for k, v in attrs if v is not None))
        if tag in _BLOCK_ELEMENTS:
            self.doc._chunks.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "meta":
            self._handle_meta(dict((k.lower(), v) for k, v in attrs if v is not None))
        if tag in _BLOCK_ELEMENTS:
            self.doc._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIPPED_ELEMENTS and tag != "head" and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_ELEMENTS:
            self.doc._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
            return
        if self._skip_depth > 0:
            return
        if data.strip():
            self.doc._chunks.append(data)

    def _handle_meta(self, attrs: dict) -> None:
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        content = attrs.get("content")
        if not key or not content:
            return
        for rank, candidate in enumerate(_PUBLISHED_META_KEYS):
            if key == candidate:
                if self._published_rank is None or rank < self._published_rank:
                    self._published_rank = rank
                    self.doc.published_hint = content.strip()
                    self.doc.published_hint_key = key
                return

    def finish(self) -> ExtractedDocument:
        title = "".join(self._title_parts).strip()
        self.doc.title = _WHITESPACE_RE.sub(" ", title) or None
        raw = "".join(self.doc._chunks)
        lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in raw.split("\n")]
        text = "\n".join(line for line in lines if line)
        self.doc.text = _BLANK_LINES_RE.sub("\n\n", text).strip()
        return self.doc


def extract_text(html: str) -> ExtractedDocument:
    """Parse ``html`` and return title, visible text, and a publish-date hint.

    Never raises on malformed markup — HTMLParser is lenient by design and we
    prefer a partial extract over a failed fetch.
    """
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # pragma: no cover - HTMLParser rarely raises, be safe
        pass
    return parser.finish()
