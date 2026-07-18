"""Failure taxonomy for the V2 reporting pipeline.

Three classes of failure, mapped to HTTP status codes by the API layer:

* ``TemplateLoadError`` (T1-T12) — template files malformed or unsafe.
  Surfaces as HTTP 500 because templates ship with the deployment; a bad
  template is an operator error, not a user error.
* ``ReportBuildError`` (R1-R7) — the build path could not satisfy the
  report contract against a particular artifact (subset violation, missing
  required dataset, cross-artifact reference, etc.).  Surfaces as HTTP 422
  because the artifact exists but is not acceptable input.
* :class:`DegradationNote` (D1-D8) — NOT an exception.  Persisted on the
  bundle and surfaced as visible Chinese text in the rendered report
  (never silently swallowed).

The codes are stable identifiers used in tests and audit logs; the messages
are operator-facing English for traceability.  User-visible Chinese text
lives in ``labels.yaml`` and is looked up by the renderer.
"""

from __future__ import annotations

from enum import Enum


class TemplateLoadError(ValueError):
    """Raised at template-load time.  Maps to HTTP 500."""


class ReportBuildError(ValueError):
    """Raised at bundle-build time.  Maps to HTTP 422."""


class DegradationCode(str, Enum):
    """Persisted degradations.  Each renders as a visible Chinese note."""

    D1_FINANCIAL_TREND_UNAVAILABLE = "D1"
    D2_PEER_COMPARISON_UNAVAILABLE = "D2"
    D3_PIPELINE_CHART_UNAVAILABLE = "D3"
    D4_INDUSTRY_DETECTION_FALLBACK = "D4"
    D5_TIER_UNKNOWN_EVIDENCE = "D5"
    D6_SECTION_EMPTY = "D6"
    D7_EXTERNAL_RESEARCH_INCOMPLETE = "D7"
    D8_RESEARCH_FAILED = "D8"


TEMPLATE_LOAD_CODES: frozenset[str] = frozenset(
    {
        "T1_FRONTMATTER_MISSING",
        "T2_FRONTMATTER_INVALID",
        "T3_TEMPLATE_ID_MISMATCH",
        "T4_VERSION_NOT_POSITIVE",
        "T5_REQUIRED_SECTIONS_DUPLICATE",
        "T6_SECTION_ID_COLLISION",
        "T7_REQUIRED_COMPONENTS_UNKNOWN",
        "T8_COMPATIBLE_SCHEMA_MISMATCH",
        "T9_UNKNOWN_DIRECTIVE",
        "T10_RAW_HTML_BLOCK",
        "T11_FORBIDDEN_EXPRESSION",
        "T12_COMPONENT_PARAMS_INVALID",
    }
)


BUILD_CODES: frozenset[str] = frozenset(
    {
        "R1_REQUIRED_SECTION_MISSING",
        "R2_REQUIRED_COMPONENT_DATASET_MISSING",
        "R3_UNKNOWN_DATASET_KEY",
        "R4_CROSS_ARTIFACT_REFERENCE",
        "R5_BRIEF_NOT_SUBSET_OF_DEEP",
        "R6_COUNTER_EVIDENCE_UNPAIRED",
        "R7_FROZEN_ARTIFACT_REQUIRED",
    }
)
