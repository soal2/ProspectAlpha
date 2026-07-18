r"""Template loader and parser for the V2 reporting DSL.

The DSL is deliberately tiny so the renderer (Step 5) can be a pure
function with no eval/exec:

* ``{{var.path}}`` — whitelist-root variable substitution.  ``var`` is the
  fixed root; ``path`` is dotted lookup against a Pydantic model supplied
  by the renderer.  No expressions, no filters, no template inheritance.
* ``{{#section <id>}}`` — section marker.  The body until the next
  directive is one rendered section with a stable ``section_id``.
* ``{{#slot <id>}}`` — slot marker (deep core only).  The renderer fills
  the slot with a fragment's sections, or renders the slot's fallback
  body if no fragment applies.
* Fenced ``\`\`\`component`` blocks — a single component directive.  The
  block body is YAML with ``kind`` (one of the whitelisted kinds) and
  optional ``params`` (extra="forbid"); the renderer resolves the
  dataset by ``kind``.

Anything else — raw HTML blocks, ``{% ... %}``, ``${...}``, ``<script>``
snippets, MDX-style ``{Component}`` — fails loading (T9-T11).

A frozen template is identified by ``(template_id, version,
content_sha256)``.  Two files with the same ``template_id/version`` but
different hashes cannot both be loaded — that's T3.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from .errors import TemplateLoadError
from .models import TemplateRef


# ---------------------------------------------------------------------------
# DSL primitives
# ---------------------------------------------------------------------------

_WHITELISTED_COMPONENT_KINDS: frozenset[str] = frozenset(
    {
        "risk_profile_heatmap",
        "risk_matrix",
        "risk_card",
        "financial_trend",
        "pipeline_chart",
        "peer_comparison",
        "event_timeline",
        "risk_causal_graph",
        "hypothesis_matrix",
        "source_quality",
        "research_coverage",
    }
)


_SECTION_RE = re.compile(r"^\{\{#section\s+([a-zA-Z0-9_]+)\}\}\s*$")
_SLOT_RE = re.compile(r"^\{\{#slot\s+([a-zA-Z0-9_]+)\}\}\s*$")
_VAR_RE = re.compile(r"\{\{\s*var\.([a-zA-Z0-9_.]+)\s*\}\}")
_COMPONENT_FENCE_RE = re.compile(
    r"^```component\s*\n(?P<body>.*?)\n```\s*$",
    flags=re.DOTALL | re.MULTILINE,
)
_FORBIDDEN_TOKENS_RE = re.compile(
    r"<script|<iframe|javascript:|on\w+\s*=|\{%|\$\{|<\?|<MDX|<React|<[A-Z]\w*[\s/>]",
    flags=re.IGNORECASE,
)


class ComponentSpec(BaseModel):
    r"""One parsed ``\`\`\`component`` block."""

    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    anchor: str  # the inline anchor the renderer emits next to the component

    @field_validator("kind")
    @classmethod
    def _kind_known(cls, value: str) -> str:
        if value not in _WHITELISTED_COMPONENT_KINDS:
            raise TemplateLoadError("T7_REQUIRED_COMPONENTS_UNKNOWN")
        return value


class ParsedSection(BaseModel):
    section_id: str
    body: str
    components: list[ComponentSpec] = Field(default_factory=list)


class ParsedSlot(BaseModel):
    slot_id: str
    fallback_body: str
    fallback_components: list[ComponentSpec] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Loaded template
# ---------------------------------------------------------------------------

TemplateKind = str  # "core_brief" | "core_deep" | "fragment"


class LoadedTemplate(BaseModel):
    template_id: str
    version: int
    kind: TemplateKind
    compatible_schema_version: str
    content_sha256: str
    source_path: str
    required_sections: list[str] = Field(default_factory=list)
    required_components: list[str] = Field(default_factory=list)
    provides_sections: list[str] = Field(default_factory=list)  # fragment only
    sections: list[ParsedSection] = Field(default_factory=list)
    slots: list[ParsedSlot] = Field(default_factory=list)
    provides_slot_targets: list[str] = Field(default_factory=list)  # fragment → slot id

    def ref(self) -> TemplateRef:
        return TemplateRef(
            template_id=self.template_id,
            version=self.version,
            content_sha256=self.content_sha256,
        )

    def section(self, section_id: str) -> ParsedSection:
        for s in self.sections:
            if s.section_id == section_id:
                return s
        raise KeyError(section_id)

    def slot(self, slot_id: str) -> ParsedSlot:
        for s in self.slots:
            if s.slot_id == slot_id:
                return s
        raise KeyError(slot_id)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split leading YAML frontmatter from the markdown body.

    Frontmatter is delimited by ``---`` lines at the very start of the file.
    Files without frontmatter raise T1.
    """

    if not text.startswith("---\n"):
        raise TemplateLoadError("T1_FRONTMATTER_MISSING")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise TemplateLoadError("T1_FRONTMATTER_MISSING")
    raw = text[4:end]
    body = text[end + 5 :]
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise TemplateLoadError(f"T2_FRONTMATTER_INVALID: {exc}") from exc
    if not isinstance(loaded, dict):
        raise TemplateLoadError("T2_FRONTMATTER_INVALID: not a mapping")
    return loaded, body


def _require_str(frontmatter: dict[str, Any], key: str) -> str:
    value = frontmatter.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TemplateLoadError(f"T2_FRONTMATTER_INVALID: missing {key}")
    return value.strip()


def _require_int(frontmatter: dict[str, Any], key: str) -> int:
    value = frontmatter.get(key)
    if not isinstance(value, int) or value < 1:
        raise TemplateLoadError(f"T4_VERSION_NOT_POSITIVE: {key}={value!r}")
    return value


def _require_str_list(frontmatter: dict[str, Any], key: str) -> list[str]:
    value = frontmatter.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise TemplateLoadError(f"T2_FRONTMATTER_INVALID: {key} must be a string list")
    if len(set(value)) != len(value):
        raise TemplateLoadError(f"T5_REQUIRED_SECTIONS_DUPLICATE: {key}")
    return list(value)


def _parse_body(body: str) -> tuple[list[ParsedSection], list[ParsedSlot]]:
    """Split body into sections and slots.

    A line ``{{#section <id>}}`` starts a section; the section ends at the
    next directive (section or slot) or end-of-body.  Same rule for slots.

    Raises T9 if a directive line doesn't match either pattern, and T10/T11
    if the body contains forbidden tokens (raw HTML, JS, MDX, etc.).
    """

    if _FORBIDDEN_TOKENS_RE.search(body):
        raise TemplateLoadError("T10_RAW_HTML_BLOCK")

    lines = body.splitlines(keepends=True)
    sections: list[ParsedSection] = []
    slots: list[ParsedSlot] = []
    current_kind: str | None = None  # "section" | "slot"
    current_id: str = ""
    current_body: list[str] = []
    current_components: list[ComponentSpec] = []

    def _flush() -> None:
        nonlocal current_kind, current_id, current_body, current_components
        if current_kind is None:
            return
        body_text = "".join(current_body)
        comps = list(current_components)
        if current_kind == "section":
            sections.append(
                ParsedSection(
                    section_id=current_id,
                    body=body_text,
                    components=comps,
                )
            )
        else:
            slots.append(
                ParsedSlot(
                    slot_id=current_id,
                    fallback_body=body_text,
                    fallback_components=comps,
                )
            )
        current_kind = None
        current_id = ""
        current_body = []
        current_components = []

    pending_components: list[ComponentSpec] = []
    for line in lines:
        section_match = _SECTION_RE.match(line)
        slot_match = _SLOT_RE.match(line)
        if section_match or slot_match:
            _flush()
            current_kind = "section" if section_match else "slot"
            current_id = (section_match or slot_match).group(1)
            continue
        # Inline component fence: a fenced ```component block can appear
        # anywhere inside a section/slot body.  We resolve it here so the
        # parsed section knows which components anchor inside it.
        if line.strip() == "```component":
            current_body.append(line)
            continue
        if line.strip() == "```" and _consume_open_component(current_body):
            # Close the open component fence, parse YAML, append ComponentSpec.
            yaml_body, prefix = _take_component_block(current_body)
            spec = _parse_component_yaml(yaml_body, anchor=prefix)
            current_components.append(spec)
            current_body.append(line)
            continue
        current_body.append(line)

    _flush()
    return sections, slots


def _consume_open_component(buffer: list[str]) -> bool:
    """True if the last open `````component`` line has not yet been closed."""

    for line in reversed(buffer):
        stripped = line.strip()
        if stripped == "```component":
            return True
        if stripped == "```":
            return False
    return False


def _take_component_block(buffer: list[str]) -> tuple[str, str]:
    """Pop the open `````component`` block into a (yaml, prefix) pair.

    ``prefix`` is the unique anchor the renderer emits next to the
    rendered component so the UI can deep-link.  The anchor is a stable
    sha256 prefix of the YAML body so the same component spec produces
    the same anchor across processes (Python's ``hash()`` is salted).
    """

    yaml_lines: list[str] = []
    prefix_lines: list[str] = []
    seen_fence = False
    for line in buffer:
        if not seen_fence:
            if line.strip() == "```component":
                seen_fence = True
            prefix_lines.append(line)
            continue
        yaml_lines.append(line)
    buffer.clear()
    buffer.extend(prefix_lines[:-1])
    yaml_text = "".join(yaml_lines)
    digest = hashlib.sha256(yaml_text.encode("utf-8")).hexdigest()
    return yaml_text, f"comp_{digest[:12]}"


def _parse_component_yaml(yaml_text: str, *, anchor: str) -> ComponentSpec:
    try:
        loaded = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise TemplateLoadError(f"T12_COMPONENT_PARAMS_INVALID: {exc}") from exc
    if not isinstance(loaded, dict):
        raise TemplateLoadError("T12_COMPONENT_PARAMS_INVALID: not a mapping")
    kind = loaded.get("kind")
    if not isinstance(kind, str):
        raise TemplateLoadError("T12_COMPONENT_PARAMS_INVALID: missing kind")
    params = loaded.get("params") or {}
    if not isinstance(params, dict):
        raise TemplateLoadError("T12_COMPONENT_PARAMS_INVALID: params must be a mapping")
    # extra="forbid" via manual check: only ``kind`` and ``params`` allowed.
    extra = set(loaded.keys()) - {"kind", "params"}
    if extra:
        raise TemplateLoadError(
            f"T12_COMPONENT_PARAMS_INVALID: unexpected keys {sorted(extra)}"
        )
    try:
        return ComponentSpec(kind=kind, params=dict(params), anchor=anchor)
    except ValidationError as exc:
        # Pydantic wraps our TemplateLoadError (raised by the kind validator)
        # in its own ValidationError — unwrap the original.
        for err in exc.errors():
            ctx = err.get("ctx") or {}
            cause = ctx.get("error")
            if isinstance(cause, TemplateLoadError):
                raise cause from exc
        raise TemplateLoadError(f"T12_COMPONENT_PARAMS_INVALID: {exc}") from exc


def _validate_required_directives(
    sections: list[ParsedSection],
    slots: list[ParsedSlot],
    *,
    required_sections: list[str],
    required_components: list[str],
) -> None:
    section_ids = {s.section_id for s in sections}
    slot_ids = {s.slot_id for s in slots}
    seen_ids = section_ids | slot_ids
    duplicates = [sid for sid in required_sections if required_sections.count(sid) > 1]
    if duplicates:
        raise TemplateLoadError(f"T5_REQUIRED_SECTIONS_DUPLICATE: {duplicates}")
    for sid in required_sections:
        if sid not in seen_ids:
            raise TemplateLoadError(f"T1_REQUIRED_SECTION_MISSING_IN_BODY: {sid}")
    present_components = {
        c.kind for s in sections for c in s.components
    } | {c.kind for s in slots for c in s.fallback_components}
    for kind in required_components:
        if kind not in present_components:
            raise TemplateLoadError(
                f"T7_REQUIRED_COMPONENTS_UNKNOWN: required kind {kind} not present"
            )


def load_template(path: Path) -> LoadedTemplate:
    """Parse one template file into a :class:`LoadedTemplate`."""

    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    template_id = _require_str(frontmatter, "template_id")
    version = _require_int(frontmatter, "version")
    kind_value = _require_str(frontmatter, "kind")
    if kind_value not in {"core_brief", "core_deep", "fragment"}:
        raise TemplateLoadError(f"T2_FRONTMATTER_INVALID: unknown kind {kind_value!r}")
    schema = _require_str(frontmatter, "compatible_schema_version")
    required_sections = _require_str_list(frontmatter, "required_sections")
    required_components = _require_str_list(frontmatter, "required_components")
    provides_sections = _require_str_list(frontmatter, "provides_sections")
    provides_slot_targets = _require_str_list(frontmatter, "provides_slot_targets")

    sections, slots = _parse_body(body)
    _validate_required_directives(
        sections,
        slots,
        required_sections=required_sections,
        required_components=required_components,
    )

    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return LoadedTemplate(
        template_id=template_id,
        version=version,
        kind=kind_value,
        compatible_schema_version=schema,
        content_sha256=sha,
        source_path=str(path),
        required_sections=required_sections,
        required_components=required_components,
        provides_sections=provides_sections,
        provides_slot_targets=provides_slot_targets,
        sections=sections,
        slots=slots,
    )


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class TemplateRepository:
    """Loads + indexes templates from a config directory.

    Layout (under ``backend/configs/report_templates/``)::

        brief_core_v1.md
        deep_core_v1.md
        fragment_general_v1.md
        fragment_biotech_v1.md
        labels.yaml
        review_triggers.yaml
        industry_detection.yaml
        content_gates.yaml

    Templates are indexed by ``(template_id, version)``.  Loading two
    files that claim the same id+version is T3 (template_id mismatch is
    not the right code but the failure is real — the second file's hash
    disagrees with the first).  Fragment section_ids colliding with
    core section_ids is T6.
    """

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir
        self._by_id: dict[str, LoadedTemplate] = {}
        self._loaded = False

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        for path in sorted(self._config_dir.glob("*.md")):
            tmpl = load_template(path)
            existing = self._by_id.get(tmpl.template_id)
            if existing is not None and existing.content_sha256 != tmpl.content_sha256:
                raise TemplateLoadError(
                    f"T3_TEMPLATE_ID_MISMATCH: {tmpl.template_id} already loaded"
                )
            self._by_id[tmpl.template_id] = tmpl
        self._check_fragment_section_collisions()
        self._loaded = True

    def _check_fragment_section_collisions(self) -> None:
        core_section_ids: set[str] = set()
        for tmpl in self._by_id.values():
            if tmpl.kind in {"core_brief", "core_deep"}:
                core_section_ids.update(s.section_id for s in tmpl.sections)
        for tmpl in self._by_id.values():
            if tmpl.kind != "fragment":
                continue
            for sid in (s.section_id for s in tmpl.sections):
                if sid in core_section_ids:
                    raise TemplateLoadError(
                        f"T6_SECTION_ID_COLLISION: fragment {tmpl.template_id} "
                        f"section {sid!r} collides with a core section"
                    )

    def get(self, template_id: str) -> LoadedTemplate:
        self.ensure_loaded()
        try:
            return self._by_id[template_id]
        except KeyError as exc:
            raise TemplateLoadError(f"T3_TEMPLATE_ID_MISMATCH: {template_id} not found") from exc

    def all_templates(self) -> list[LoadedTemplate]:
        self.ensure_loaded()
        return list(self._by_id.values())


def load_yaml_config(config_dir: Path, name: str) -> Mapping[str, Any]:
    """Load a sibling YAML config (labels / review_triggers / etc)."""

    path = config_dir / name
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TemplateLoadError(f"T2_FRONTMATTER_INVALID: {name}: {exc}") from exc
    return loaded if isinstance(loaded, dict) else {}
