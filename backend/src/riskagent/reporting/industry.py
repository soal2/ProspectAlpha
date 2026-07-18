"""Industry detection: pick which fragment template fills the deep-core slot.

The detection is intentionally simple — keyword matching against the
company name, business description, and evidence card titles/snippets.
Phase 2 supports only ``general`` and ``biotech``; any failure to match
falls back to ``general`` with a recorded reason so the bundle surfaces
that the detection was inconclusive.

Phase 3+ can swap this module for a learned classifier without touching
the rest of the reporting pipeline.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Mapping

from pydantic import BaseModel

from riskagent.research.models import ResearchArtifact, SourceCard
from .errors import DegradationCode
from .models import IndustryProfile
from .templates import LoadedTemplate, TemplateRepository


_DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "biotech": [
        "生物制药",
        "生物医药",
        "创新药",
        "临床试验",
        "药品审评",
        "药品注册",
        "IND",
        "NDA",
        "BLA",
        "GMP认证",
        "单克隆抗体",
        "mRNA",
        "疫苗",
        "生物制品",
    ],
}


class _Detection(BaseModel):
    """Internal result so callers can introspect why a fragment was picked."""

    industry: str
    fragment_template_id: str | None
    matched_keywords: list[str]
    reason: str


def _evidence_text(artifact: ResearchArtifact) -> str:
    parts: list[str] = []
    for card in artifact.evidence:
        if card.title:
            parts.append(card.title)
        if card.snippet:
            parts.append(card.snippet)
    return " ".join(parts)


def _hypothesis_text(artifact: ResearchArtifact) -> str:
    return " ".join(f"{h.title} {h.statement}" for h in artifact.hypotheses)


def detect_industry(
    artifact: ResearchArtifact,
    *,
    company_name: str = "",
    business_description: str = "",
    industry_config: Mapping[str, object] | None = None,
) -> _Detection:
    """Match company/evidence text against industry keywords.

    ``industry_config`` is the parsed ``industry_detection.yaml`` mapping
    (top-level ``industries`` list).  When omitted, only the hard-coded
    biotech keywords are evaluated — sufficient for tests and the
    shipping default.
    """

    industries = _parse_industry_config(industry_config)
    haystack = " ".join(
        [
            company_name or "",
            business_description or "",
            _hypothesis_text(artifact),
            _evidence_text(artifact),
        ]
    )
    if not haystack.strip():
        return _Detection(
            industry="general",
            fragment_template_id=_fragment_id_for(industries, "general"),
            matched_keywords=[],
            reason="fallback: no industry signal matched (empty haystack)",
        )

    biotech_keywords = industries.get("biotech", {}).get(
        "keywords", _DEFAULT_KEYWORDS["biotech"]
    )
    matches = [kw for kw in biotech_keywords if kw and kw in haystack]
    if matches:
        # Use the most common matches first for a deterministic reason string.
        ordered = sorted(matches, key=lambda m: (-haystack.count(m), m))
        return _Detection(
            industry="biotech",
            fragment_template_id=_fragment_id_for(industries, "biotech"),
            matched_keywords=ordered[:5],
            reason=f"matched biotech keywords: {', '.join(ordered[:5])}",
        )
    return _Detection(
        industry="general",
        fragment_template_id=_fragment_id_for(industries, "general"),
        matched_keywords=[],
        reason="fallback: no industry signal matched",
    )


def _parse_industry_config(
    config: Mapping[str, object] | None,
) -> dict[str, dict[str, object]]:
    if not config:
        return {}
    raw = config.get("industries")
    if not isinstance(raw, list):
        return {}
    parsed: dict[str, dict[str, object]] = {}
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        industry_id = entry.get("id")
        if not isinstance(industry_id, str):
            continue
        parsed[industry_id] = dict(entry)
    return parsed


def _fragment_id_for(industries: dict[str, dict[str, object]], industry: str) -> str | None:
    entry = industries.get(industry)
    if entry is None:
        return None
    fragment_id = entry.get("fragment_template_id")
    return fragment_id if isinstance(fragment_id, str) else None


# ---------------------------------------------------------------------------
# Public adapter: pick a fragment template from the repository
# ---------------------------------------------------------------------------


def select_industry_fragment(
    artifact: ResearchArtifact,
    repo: TemplateRepository,
    *,
    company_name: str = "",
    business_description: str = "",
    industry_config: Mapping[str, object] | None = None,
) -> tuple[IndustryProfile, LoadedTemplate | None]:
    """Detect the industry and return a fragment template (or None for general).

    The general fragment is always available because the repository ships
    ``fragment_general_v1.md``.  Returning ``None`` for general is wrong;
    we always return the general fragment when no industry-specific one
    is detected so the deep-core slot has *something* to render.
    """

    detection = detect_industry(
        artifact,
        company_name=company_name,
        business_description=business_description,
        industry_config=industry_config,
    )
    if detection.fragment_template_id is None:
        # No config supplied — fall back to the canonical ids.
        fragment_id = "fragment_biotech" if detection.industry == "biotech" else "fragment_general"
    else:
        fragment_id = detection.fragment_template_id
    try:
        fragment = repo.get(fragment_id)
    except Exception:  # noqa: BLE001 — TemplateRepository raises TemplateLoadError
        fragment = None
    profile = IndustryProfile(
        detected=detection.industry,  # type: ignore[arg-type]
        fragment=fragment.ref() if fragment is not None else None,
        detection_reason=detection.reason,
    )
    return profile, fragment


def fallback_profile(reason: str = "fallback: industry detection unavailable") -> IndustryProfile:
    """Build a degraded profile used when detection fails entirely."""

    _ = DegradationCode.D4_INDUSTRY_DETECTION_FALLBACK  # document the code in use
    return IndustryProfile(
        detected="general",
        fragment=None,
        detection_reason=reason,
    )
