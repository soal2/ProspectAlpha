"""Source tiering and independence clustering.

Two orthogonal jobs, both structural (no LLM, no prompt):

* :func:`classify_source` maps a card's registrable domain to a
  :class:`SourceTier` using the seed suffix table in
  ``configs/research_source_tiers_v1.json`` plus per-run company IR domains.
  ``unknown`` domains are treated as ``tier3`` by the gate; the enum value is
  kept distinct only so the audit trail can tell "seen but unranked" apart
  from an explicit tier3 rule.

* :func:`cluster_independence` groups cards that are really the *same* report
  syndicated across sites, so the gate counts independent voices rather than
  URLs.  Edges are drawn on near-identical titles, high 5-gram Jaccard on the
  snippet/extract, or an explicit republication marker ("来源:", "转自",
  "据 X 报道", "originally reported by").  Connected components are clusters;
  ``independence_cluster`` is stamped onto each card in place.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from .models import SourceCard, SourceTier

BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TIER_CONFIG = BACKEND_ROOT / "configs" / "research_source_tiers_v1.json"

# Republication markers: a card carrying one of these is almost certainly a
# reprint of another outlet's reporting, not an independent source.
_REPRINT_PATTERNS = (
    re.compile(r"来源[:：]"),
    re.compile(r"转自"),
    re.compile(r"转载自?"),
    re.compile(r"据.{1,12}报道"),
    re.compile(r"综合.{1,12}报道"),
    re.compile(r"本文.{0,6}(来自|摘自)"),
    re.compile(r"originally reported by", re.IGNORECASE),
    re.compile(r"first reported by", re.IGNORECASE),
    re.compile(r"\(reuters\)|\(bloomberg\)", re.IGNORECASE),
    re.compile(r"via\s+\w+", re.IGNORECASE),
)

_TITLE_SIMILARITY_THRESHOLD = 0.85
_JACCARD_THRESHOLD = 0.7
_WORD_RE = re.compile(r"[0-9a-zA-Z一-鿿]+")


@lru_cache(maxsize=4)
def _load_tier_table(config_path: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (tier1_suffixes, tier2_suffixes) from the seed JSON."""
    data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    tiers = data.get("tiers", {})
    t1 = tuple(tiers.get("tier1", {}).get("domain_suffixes", []))
    t2 = tuple(tiers.get("tier2", {}).get("domain_suffixes", []))
    return t1, t2


def _host_of(url: Optional[str]) -> str:
    if not url:
        return ""
    from urllib.parse import urlsplit

    try:
        return (urlsplit(url).hostname or "").lower().strip(".")
    except ValueError:
        return ""


def _suffix_match(host: str, suffix: str) -> bool:
    suffix = suffix.lower().strip(".")
    return host == suffix or host.endswith("." + suffix)


def classify_source(
    card: SourceCard,
    *,
    ir_domains: Iterable[str] = (),
    config_path: Optional[str] = None,
) -> tuple[SourceTier, str]:
    """Classify a card into a tier plus a human-readable reason.

    ``ir_domains`` (per-run company investor-relations domains) are added to
    tier1: a company's own regulatory filings host counts as primary.
    """
    host = _host_of(card.final_url or card.url)
    if not host:
        return SourceTier.unknown, "no resolvable host"

    for ir in ir_domains:
        if ir and _suffix_match(host, ir):
            return SourceTier.tier1, f"configured company IR domain ({ir})"

    t1, t2 = _load_tier_table(config_path or str(DEFAULT_TIER_CONFIG))
    for suffix in t1:
        if _suffix_match(host, suffix):
            return SourceTier.tier1, f"tier1 primary/authoritative domain ({suffix})"
    for suffix in t2:
        if _suffix_match(host, suffix):
            return SourceTier.tier2, f"tier2 high-trust media/database ({suffix})"
    return SourceTier.unknown, f"unranked domain ({host}); treated as tier3"


def apply_tiers(
    cards: Iterable[SourceCard],
    *,
    ir_domains: Iterable[str] = (),
    config_path: Optional[str] = None,
) -> None:
    """Stamp ``tier``/``tier_reason`` onto each card in place."""
    ir_list = list(ir_domains)
    for card in cards:
        tier, reason = classify_source(
            card, ir_domains=ir_list, config_path=config_path
        )
        card.tier = tier
        card.tier_reason = reason


# ----------------------------------------------------------------------
# independence clustering


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold()


def _normalize_title(title: Optional[str]) -> str:
    if not title:
        return ""
    return "".join(_WORD_RE.findall(_fold(title)))


def _title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # character-level Dice on the shorter/longer normalized titles
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    denom = len(sa) + len(sb)
    if denom == 0:
        return 0.0
    return 2.0 * inter / denom


def _ngrams(text: str, n: int = 5) -> set[str]:
    tokens = _WORD_RE.findall(_fold(text))
    joined = "".join(tokens)
    if len(joined) < n:
        return {joined} if joined else set()
    return {joined[i : i + n] for i in range(len(joined) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _has_reprint_marker(card: SourceCard) -> bool:
    blob = " ".join(filter(None, (card.snippet, card.extract)))
    return any(pat.search(blob) for pat in _REPRINT_PATTERNS)


class _UnionFind:
    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, i: int) -> int:
        while self._parent[i] != i:
            self._parent[i] = self._parent[self._parent[i]]
            i = self._parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self._parent[max(ri, rj)] = min(ri, rj)


def cluster_independence(cards: list[SourceCard]) -> int:
    """Assign ``independence_cluster`` ids in place; return the cluster count.

    Two cards are linked (same underlying report) when titles are near
    identical, snippet/extract 5-gram Jaccard exceeds the threshold, or one
    of them carries a reprint marker while sharing meaningful text overlap
    with the other.
    """
    n = len(cards)
    if n == 0:
        return 0

    titles = [_normalize_title(c.title) for c in cards]
    grams = [_ngrams((c.extract or c.snippet or "")) for c in cards]
    reprint = [_has_reprint_marker(c) for c in cards]

    uf = _UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            if titles[i] and _title_similarity(titles[i], titles[j]) >= _TITLE_SIMILARITY_THRESHOLD:
                uf.union(i, j)
                continue
            overlap = _jaccard(grams[i], grams[j])
            if overlap >= _JACCARD_THRESHOLD:
                uf.union(i, j)
                continue
            # A reprint marker plus moderate textual overlap is enough to
            # treat the two as one voice.
            if (reprint[i] or reprint[j]) and overlap >= _JACCARD_THRESHOLD / 2:
                uf.union(i, j)

    roots: dict[int, str] = {}
    for idx, card in enumerate(cards):
        root = uf.find(idx)
        cluster_id = roots.get(root)
        if cluster_id is None:
            cluster_id = f"cl_{len(roots) + 1}"
            roots[root] = cluster_id
        card.independence_cluster = cluster_id
    return len(roots)
