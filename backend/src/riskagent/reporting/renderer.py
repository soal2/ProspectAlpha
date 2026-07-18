r"""Pure-function renderer for the V2 reporting DSL.

This module is the security boundary between untrusted template content
and the rendered Markdown.  It is deliberately tiny and contains
**no** ``eval``, ``exec``, ``__import__``, or string interpolation
beyond the three whitelisted DSL constructs:

1. ``{{var.path}}`` — dotted lookup against a *single* supplied root
   model.  Only ``var`` is permitted as the root; the dotted path is
   restricted to ``[A-Za-z0-9_]`` segments (no dunder access, no
   indexing, no slicing).
2. ``{{#section <id>}}`` / ``{{#slot <id>}}`` — section/slot markers
   consumed at load time by ``templates.py``; the renderer just walks
   the already-parsed ``ParsedSection`` / ``ParsedSlot`` lists.
3. Fenced ``\`\`\`component`` blocks — parsed at load time into
   ``ComponentSpec`` instances with whitelisted kinds and
   ``extra="forbid"`` YAML.  The renderer resolves each spec via the
   dataset registry and inserts a placeholder anchor.

Output safety:

* Every variable substitution value is HTML-escaped (the renderer emits
  Markdown; the UI renders that Markdown via a sanitised pipeline, so
  escaping here is the second line of defence).
* Component payloads are emitted inside a fenced
  ``\`\`\`chart-<kind>`` block plus an HTML comment carrying the
  deep-link anchor.  Block-level HTML comments survive Markdown
  processing, giving the UI a stable hook without embedding JSON in
  prose.
* Sections marked ``non_formal`` (hypothesis_matrix, weak_sources_appendix)
  get a banner prepended; the renderer never silently strips content.

The renderer raises ``ReportBuildError`` (R-series) for runtime issues
unknown to the loader (e.g. a var path that doesn't exist on the
supplied root).
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel

from .datasets import DatasetRegistry
from .errors import DegradationCode, ReportBuildError
from .models import (
    DegradationNote,
    ModuleReport,
    RenderedComponent,
    SectionRender,
)
from .templates import ComponentSpec, LoadedTemplate, ParsedSection, ParsedSlot


# ---------------------------------------------------------------------------
# DSL primitives
# ---------------------------------------------------------------------------


_VAR_RE = re.compile(r"\{\{\s*var\.([a-zA-Z0-9_.]+)\s*\}\}")
_NON_FORMAL_SECTION_IDS: frozenset[str] = frozenset(
    {"hypothesis_matrix", "weak_sources_appendix"}
)
_NON_FORMAL_BANNER_ZH: str = "本章节内容不构成正式结论。"
_HEADING_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*$")


# ---------------------------------------------------------------------------
# Var lookup
# ---------------------------------------------------------------------------


def _resolve_var_path(root: Any, path: str) -> Any:
    """Resolve ``a.b.c`` against ``root``.

    * No ``__dunder__`` access.
    * No indexing, slicing, or call syntax.
    * Each segment must match ``[A-Za-z0-9_]``.
    * Pydantic models and mappings are descended by attribute / key.

    Raises ``ReportBuildError`` (R3) for any path the renderer can't
    walk.  A missing var is a renderer-layer error (the bundle's
    context didn't supply it), not a template-load error.
    """

    segments = path.split(".")
    current: Any = root
    walked: list[str] = []
    for seg in segments:
        walked.append(seg)
        if not seg or not re.fullmatch(r"[a-zA-Z0-9_]+", seg):
            raise ReportBuildError(
                f"R3_INVALID_VAR_PATH: illegal segment in {path!r} at {seg!r}"
            )
        if isinstance(current, BaseModel):
            fields = current.__class__.model_fields
            if seg not in fields:
                raise ReportBuildError(
                    f"R3_INVALID_VAR_PATH: {seg!r} not on {'.'.join(walked[:-1]) or 'root'}"
                )
            current = getattr(current, seg)
        elif isinstance(current, Mapping):
            if seg not in current:
                raise ReportBuildError(
                    f"R3_INVALID_VAR_PATH: key {seg!r} missing in {'.'.join(walked[:-1]) or 'root'}"
                )
            current = current[seg]
        else:
            raise ReportBuildError(
                f"R3_INVALID_VAR_PATH: cannot descend into {type(current).__name__} at {'.'.join(walked[:-1]) or 'root'}"
            )
    return current


def _coerce_var_value(value: Any) -> str:
    """Render a Python value as a safe Markdown string."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return html.escape(value, quote=False)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return ", ".join(_coerce_var_value(v) for v in value)
    try:
        rendered = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        raise ReportBuildError(f"R3_INVALID_VAR_PATH: unserialisable value: {exc}") from exc
    return html.escape(rendered, quote=False)


def _substitute_vars(text: str, root: Any) -> str:
    """Replace every ``{{var.path}}`` with the HTML-escaped value."""

    def _replace(match: re.Match[str]) -> str:
        path = match.group(1)
        value = _resolve_var_path(root, path)
        return _coerce_var_value(value)

    return _VAR_RE.sub(_replace, text)


# ---------------------------------------------------------------------------
# Component rendering
# ---------------------------------------------------------------------------


def _lineage_from_payload(payload: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """Walk a dataset payload and collect (evidence_ids, hypothesis_ids, rule_ids).

    The dataset builders attach ``source_hypothesis_ids`` / ``source_evidence_ids``
    fields where relevant; rule_ids ride on hypothesis rows in matrix/card.
    """

    evidence_ids: list[str] = []
    hypothesis_ids: list[str] = []
    finding_ids: list[str] = []

    def _walk(obj: Any) -> None:
        if isinstance(obj, Mapping):
            for key, value in obj.items():
                if key in {"evidence_id"} and isinstance(value, str):
                    if value not in evidence_ids:
                        evidence_ids.append(value)
                elif key in {"hypothesis_id"} and isinstance(value, str):
                    if value not in hypothesis_ids:
                        hypothesis_ids.append(value)
                elif key in {"source_hypothesis_ids", "source_evidence_ids"} and isinstance(value, list):
                    target = hypothesis_ids if key == "source_hypothesis_ids" else evidence_ids
                    for item in value:
                        if isinstance(item, str) and item not in target:
                            target.append(item)
                elif key in {"rule_ids", "rule_finding_ids"} and isinstance(value, list):
                    for item in value:
                        if isinstance(item, str) and item not in finding_ids:
                            finding_ids.append(item)
                else:
                    _walk(value)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(payload)
    return evidence_ids, hypothesis_ids, finding_ids


def _render_component(
    spec: ComponentSpec,
    *,
    registry: DatasetRegistry,
    section_id: str,
) -> tuple[str, RenderedComponent, DegradationNote | None]:
    r"""Resolve a component spec to a Markdown stub + a structured render record.

    The Markdown stub is a fenced ``\`\`\`chart-<kind>`` block plus an
    HTML comment carrying the deep-link anchor.  The UI swaps the fenced
    block for an interactive chart using the payload's ``anchor``; the
    comment survives Markdown processing because block-level HTML is
    preserved.
    """

    dataset = registry.resolve(spec.kind, spec.params)
    payload = dataset.model_dump(mode="json")
    evidence_ids, hypothesis_ids, finding_ids = _lineage_from_payload(payload)
    lineage = [*evidence_ids, *hypothesis_ids]
    body = json.dumps(payload, ensure_ascii=False)
    comment = f"<!-- component kind={spec.kind} anchor={spec.anchor} -->"
    fence = f"```chart-{spec.kind}\n{body}\n```"
    markdown = f"{comment}\n{fence}\n"
    rendered = RenderedComponent(
        kind=spec.kind,
        dataset_key=spec.kind,
        lineage=lineage,
        rendered_html=None,  # the UI generates HTML; the renderer emits Markdown
        payload=payload,
    )
    degradation: DegradationNote | None = None
    if payload.get("available") is False:
        deg_code = payload.get("degradation")
        if deg_code == DegradationCode.D1_FINANCIAL_TREND_UNAVAILABLE.value:
            degradation = DegradationNote(
                code=DegradationCode.D1_FINANCIAL_TREND_UNAVAILABLE,
                message_zh="本轮未提供结构化财务数据，财务趋势组件已降级。",
                section_id=section_id,
            )
        elif deg_code == DegradationCode.D2_PEER_COMPARISON_UNAVAILABLE.value:
            degradation = DegradationNote(
                code=DegradationCode.D2_PEER_COMPARISON_UNAVAILABLE,
                message_zh="本轮未提供结构化同业对比数据，可比公司组件已降级。",
                section_id=section_id,
            )
    return markdown, rendered, degradation


# ---------------------------------------------------------------------------
# Section / slot rendering
# ---------------------------------------------------------------------------


def _extract_title_zh(body: str, fallback: str) -> str:
    for line in body.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            return html.unescape(match.group(1))
    return fallback


def _render_section(
    section: ParsedSection,
    *,
    root: Any,
    registry: DatasetRegistry,
    empty_section_label: str,
) -> tuple[str, SectionRender, list[RenderedComponent], list[DegradationNote]]:
    body = _substitute_vars(section.body, root)
    title_zh = _extract_title_zh(body, fallback=section.section_id)
    parts: list[str] = []
    if section.section_id in _NON_FORMAL_SECTION_IDS:
        parts.append(f"> {_NON_FORMAL_BANNER_ZH}\n\n")
    parts.append(body)
    rendered_components: list[RenderedComponent] = []
    component_anchors: list[str] = []
    referenced_evidence: list[str] = []
    referenced_hypotheses: list[str] = []
    referenced_findings: list[str] = []
    degradations: list[DegradationNote] = []
    for spec in section.components:
        markdown, rendered, degradation = _render_component(
            spec,
            registry=registry,
            section_id=section.section_id,
        )
        parts.append(markdown)
        component_anchors.append(spec.anchor)
        rendered_components.append(rendered)
        ev_ids, hyp_ids, rule_ids = _lineage_from_payload(rendered.payload)
        for eid in ev_ids:
            if eid not in referenced_evidence:
                referenced_evidence.append(eid)
        for hid in hyp_ids:
            if hid not in referenced_hypotheses:
                referenced_hypotheses.append(hid)
        for rid in rule_ids:
            if rid not in referenced_findings:
                referenced_findings.append(rid)
        if degradation is not None:
            degradations.append(degradation)
    rendered_body = "".join(parts)
    is_empty = not section.components and not section.body.strip()
    if is_empty:
        rendered_body = rendered_body + f"\n{empty_section_label}\n"
    section_render = SectionRender(
        section_id=section.section_id,
        title_zh=title_zh,
        markdown=rendered_body,
        component_anchors=component_anchors,
        referenced_evidence_ids=referenced_evidence,
        referenced_hypothesis_ids=referenced_hypotheses,
        referenced_finding_ids=referenced_findings,
        non_formal=section.section_id in _NON_FORMAL_SECTION_IDS,
    )
    return rendered_body, section_render, rendered_components, degradations


def _render_slot(
    slot: ParsedSlot,
    *,
    fragment_sections: Sequence[ParsedSection],
    root: Any,
    registry: DatasetRegistry,
    empty_section_label: str,
) -> tuple[str, list[SectionRender], list[RenderedComponent], list[DegradationNote]]:
    """Render a deep-core slot, filling from a fragment if available."""

    if fragment_sections:
        parts: list[str] = []
        renders: list[SectionRender] = []
        components: list[RenderedComponent] = []
        degrs: list[DegradationNote] = []
        for frag_section in fragment_sections:
            markdown, render, comps, degs = _render_section(
                frag_section,
                root=root,
                registry=registry,
                empty_section_label=empty_section_label,
            )
            parts.append(markdown)
            renders.append(render)
            components.extend(comps)
            degrs.extend(degs)
        return "".join(parts), renders, components, degrs
    fallback_body = _substitute_vars(slot.fallback_body, root)
    parts = [fallback_body]
    renders: list[SectionRender] = []
    components: list[RenderedComponent] = []
    degrs: list[DegradationNote] = []
    for spec in slot.fallback_components:
        markdown, rendered, degradation = _render_component(
            spec,
            registry=registry,
            section_id=slot.slot_id,
        )
        parts.append(markdown)
        components.append(rendered)
        if degradation is not None:
            degrs.append(degradation)
    return "".join(parts), renders, components, degrs


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------


def render_module(
    template: LoadedTemplate,
    *,
    root: Any,
    registry: DatasetRegistry,
    module_kind: str,
    fragment: LoadedTemplate | None = None,
    target_slot_id: str | None = None,
    empty_section_label: str = "本轮未形成有效结论。",
) -> ModuleReport:
    """Render a template into a :class:`ModuleReport`.

    ``root`` is the single object ``{{var.path}}`` traverses — typically a
    Pydantic model exposing ``company``/``run``/``artifact``/``bundle``
    subobjects.  ``fragment`` and ``target_slot_id`` only apply to the
    deep-core template: the chosen fragment's sections fill the slot
    with that id.
    """

    section_renders: list[SectionRender] = []
    degradations: list[DegradationNote] = []
    rendered_components: list[RenderedComponent] = []
    body_parts: list[str] = []
    hypothesis_ids_visible: list[str] = []
    finding_ids_visible: list[str] = []

    for section in template.sections:
        body, render, comps, degs = _render_section(
            section,
            root=root,
            registry=registry,
            empty_section_label=empty_section_label,
        )
        body_parts.append(body)
        section_renders.append(render)
        rendered_components.extend(comps)
        for hid in render.referenced_hypothesis_ids:
            if hid not in hypothesis_ids_visible:
                hypothesis_ids_visible.append(hid)
        for rid in render.referenced_finding_ids:
            if rid not in finding_ids_visible:
                finding_ids_visible.append(rid)
        degradations.extend(degs)

    for slot in template.slots:
        fragment_sections: list[ParsedSection] = []
        if fragment is not None and target_slot_id == slot.slot_id:
            fragment_sections = list(fragment.sections)
        slot_body, slot_renders, slot_components, slot_degs = _render_slot(
            slot,
            fragment_sections=fragment_sections,
            root=root,
            registry=registry,
            empty_section_label=empty_section_label,
        )
        body_parts.append(slot_body)
        for render in slot_renders:
            section_renders.append(render)
            for hid in render.referenced_hypothesis_ids:
                if hid not in hypothesis_ids_visible:
                    hypothesis_ids_visible.append(hid)
            for rid in render.referenced_finding_ids:
                if rid not in finding_ids_visible:
                    finding_ids_visible.append(rid)
        rendered_components.extend(slot_components)
        degradations.extend(slot_degs)

    markdown = "".join(body_parts).strip() + "\n"
    return ModuleReport(
        module_kind=module_kind,  # type: ignore[arg-type]
        template=template.ref(),
        markdown=markdown,
        sections=section_renders,
        components=rendered_components,
        degradations=degradations,
        hypothesis_ids_visible=hypothesis_ids_visible,
        finding_ids_visible=finding_ids_visible,
    )


def substitute_vars_public(text: str, root: Any) -> str:
    """Public wrapper around :func:`_substitute_vars` for tests and lint."""

    return _substitute_vars(text, root)
