"""Tests for reporting/templates.py: frontmatter validation, DSL parsing,
forbidden-token rejection, fragment collisions, and repository behavior.

These tests construct template files on the fly under ``tmp_path`` so the
loader is exercised against real ``.md`` files without depending on the
shipping ``configs/report_templates/`` directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from riskagent.reporting.errors import TemplateLoadError
from riskagent.reporting.templates import (
    ComponentSpec,
    LoadedTemplate,
    TemplateRepository,
    load_template,
    load_yaml_config,
)


# ---------------------------------------------------------------------------
# Valid template scaffolds
# ---------------------------------------------------------------------------

_BRIEF_CORE = """\
---
template_id: brief_core
version: 1
kind: core_brief
compatible_schema_version: "2026.07"
required_sections:
  - risks_verified
  - risks_unresolved
required_components:
  - risk_profile_heatmap
provides_sections: []
provides_slot_targets: []
---

{{#section risks_verified}}
# 已验证重大风险事项

```component
kind: risk_profile_heatmap
params:
  limit: 5
```
{{#section risks_unresolved}}
# 重大待核实事项
"""


_FRAGMENT = """\
---
template_id: fragment_general
version: 1
kind: fragment
compatible_schema_version: "2026.07"
required_sections: []
required_components: []
provides_sections:
  - industry_overview
provides_slot_targets: []
---

{{#section industry_overview}}
通用行业风险概述。
"""


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Frontmatter validation
# ---------------------------------------------------------------------------


def test_load_valid_brief_core(tmp_path: Path):
    template_path = _write(tmp_path / "brief_core_v1.md", _BRIEF_CORE)
    tmpl = load_template(template_path)
    assert tmpl.template_id == "brief_core"
    assert tmpl.version == 1
    assert tmpl.kind == "core_brief"
    assert tmpl.compatible_schema_version == "2026.07"
    assert tmpl.required_sections == ["risks_verified", "risks_unresolved"]
    assert tmpl.required_components == ["risk_profile_heatmap"]
    assert len(tmpl.sections) == 2
    assert tmpl.sections[0].section_id == "risks_verified"
    assert tmpl.sections[1].section_id == "risks_unresolved"
    assert len(tmpl.sections[0].components) == 1
    assert tmpl.sections[0].components[0].kind == "risk_profile_heatmap"
    assert tmpl.sections[0].components[0].params == {"limit": 5}
    assert tmpl.content_sha256  # 64-char hex
    assert len(tmpl.content_sha256) == 64


def test_t1_missing_frontmatter(tmp_path: Path):
    template_path = _write(tmp_path / "no_frontmatter.md", "# just a body\n")
    with pytest.raises(TemplateLoadError, match="T1_FRONTMATTER_MISSING"):
        load_template(template_path)


def test_t1_unclosed_frontmatter(tmp_path: Path):
    template_path = _write(
        tmp_path / "unclosed.md",
        "---\ntemplate_id: x\nversion: 1\nkind: core_brief\n",
    )
    with pytest.raises(TemplateLoadError, match="T1"):
        load_template(template_path)


def test_t2_invalid_yaml(tmp_path: Path):
    template_path = _write(
        tmp_path / "bad_yaml.md",
        "---\ntemplate_id: : :\n---\nbody\n",
    )
    with pytest.raises(TemplateLoadError, match="T2"):
        load_template(template_path)


def test_t2_missing_template_id(tmp_path: Path):
    text = _BRIEF_CORE.replace("template_id: brief_core\n", "")
    template_path = _write(tmp_path / "missing_id.md", text)
    with pytest.raises(TemplateLoadError, match="T2"):
        load_template(template_path)


def test_t2_unknown_kind(tmp_path: Path):
    text = _BRIEF_CORE.replace("kind: core_brief", "kind: random_thing")
    template_path = _write(tmp_path / "bad_kind.md", text)
    with pytest.raises(TemplateLoadError, match="T2"):
        load_template(template_path)


def test_t4_version_not_positive(tmp_path: Path):
    text = _BRIEF_CORE.replace("version: 1", "version: 0")
    template_path = _write(tmp_path / "v0.md", text)
    with pytest.raises(TemplateLoadError, match="T4"):
        load_template(template_path)


def test_t5_duplicate_required_sections(tmp_path: Path):
    text = _BRIEF_CORE.replace(
        "  - risks_verified\n  - risks_unresolved\n",
        "  - risks_verified\n  - risks_verified\n",
    )
    template_path = _write(tmp_path / "dup_sections.md", text)
    with pytest.raises(TemplateLoadError, match="T5"):
        load_template(template_path)


def test_t1_required_section_missing_in_body(tmp_path: Path):
    text = _BRIEF_CORE.replace(
        "{{#section risks_unresolved}}",
        "{{#section some_other_section}}",
    )
    template_path = _write(tmp_path / "missing_body.md", text)
    with pytest.raises(TemplateLoadError, match="T1_REQUIRED_SECTION_MISSING_IN_BODY"):
        load_template(template_path)


def test_t7_required_component_unknown(tmp_path: Path):
    text = _BRIEF_CORE.replace("risk_profile_heatmap", "totally_unknown_kind")
    template_path = _write(tmp_path / "unknown_component.md", text)
    with pytest.raises(TemplateLoadError, match="T7"):
        load_template(template_path)


def test_t7_unknown_component_kind_in_block_rejected(tmp_path: Path):
    text = _BRIEF_CORE.replace("kind: risk_profile_heatmap", "kind: evil_widget")
    template_path = _write(tmp_path / "evil_widget.md", text)
    with pytest.raises(TemplateLoadError, match="T7"):
        load_template(template_path)


def test_t12_component_yaml_invalid(tmp_path: Path):
    text = _BRIEF_CORE.replace(
        "kind: risk_profile_heatmap\nparams:\n  limit: 5\n",
        "kind: risk_profile_heatmap\nparams: \"not-a-mapping\"\n",
    )
    template_path = _write(tmp_path / "bad_params.md", text)
    with pytest.raises(TemplateLoadError, match="T12"):
        load_template(template_path)


def test_t12_component_missing_kind(tmp_path: Path):
    text = _BRIEF_CORE.replace("kind: risk_profile_heatmap\n", "params:\n  limit: 5\n")
    template_path = _write(tmp_path / "no_kind.md", text)
    with pytest.raises(TemplateLoadError, match="T12"):
        load_template(template_path)


def test_t12_component_extra_keys_rejected(tmp_path: Path):
    text = _BRIEF_CORE.replace(
        "kind: risk_profile_heatmap\n",
        "kind: risk_profile_heatmap\nmalicious_code: |\n  execute_me()\n",
    )
    template_path = _write(tmp_path / "extra_keys.md", text)
    with pytest.raises(TemplateLoadError, match="T12"):
        load_template(template_path)


# ---------------------------------------------------------------------------
# Forbidden-token / DSL safety
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "payload",
    [
        # raw HTML injection
        "<script>alert(1)</script>",
        # iframe
        'Text\n<iframe src="https://evil.example.com"></iframe>',
        # javascript URL
        "[click](javascript:alert(1))",
        # inline event handler
        '<img src="x" onerror="alert(1)">',
        # Jinja template tag
        "{% for x in data %}{{ x }}{% endfor %}",
        # JS template literal
        "Dynamic value: ${7*7}",
        # PHP tag
        '<?php echo "pwned"; ?>',
        # MDX component
        "<MyComponent prop=\"x\" />",
        # React component capitalised
        "<React.Fragment>inner</React.Fragment>",
    ],
)
def test_t10_forbidden_tokens_rejected(tmp_path: Path, payload: str):
    text = (
        "---\n"
        "template_id: evil\n"
        "version: 1\n"
        "kind: core_brief\n"
        'compatible_schema_version: "2026.07"\n'
        "required_sections: []\n"
        "required_components: []\n"
        "---\n\n"
        f"{payload}\n"
    )
    template_path = _write(tmp_path / "evil.md", text)
    with pytest.raises(TemplateLoadError, match="T10"):
        load_template(template_path)


def test_regular_markdown_allowed(tmp_path: Path):
    """Standard markdown constructs (headings, lists, links, bold) are not
    forbidden tokens — only HTML/JS/template-injection vectors are."""

    text = (
        "---\n"
        "template_id: ok\n"
        "version: 1\n"
        "kind: core_brief\n"
        'compatible_schema_version: "2026.07"\n'
        "required_sections: []\n"
        "required_components: []\n"
        "---\n\n"
        "# Title\n\n"
        "Some **bold** and *italic* text.\n\n"
        "- item a\n- item b\n\n"
        "[link](https://example.com)\n\n"
        "![image](https://example.com/i.png)\n\n"
        "Comparison: a < b and x > y.\n"
    )
    tmpl = load_template(_write(tmp_path / "ok.md", text))
    assert tmpl.template_id == "ok"


# ---------------------------------------------------------------------------
# Hash stability and slot parsing
# ---------------------------------------------------------------------------


def test_content_hash_stable_for_same_file(tmp_path: Path):
    a = load_template(_write(tmp_path / "a.md", _BRIEF_CORE))
    b = load_template(_write(tmp_path / "b.md", _BRIEF_CORE))
    assert a.content_sha256 == b.content_sha256


def test_content_hash_differs_when_body_changes(tmp_path: Path):
    a = load_template(_write(tmp_path / "a.md", _BRIEF_CORE))
    modified = _BRIEF_CORE.replace(
        "# 已验证重大风险事项",
        "# 已验证重大风险事项（更新）",
    )
    b = load_template(_write(tmp_path / "b.md", modified))
    assert a.content_sha256 != b.content_sha256


def test_slots_parsed_in_deep_template(tmp_path: Path):
    text = (
        "---\n"
        "template_id: deep_core\n"
        "version: 1\n"
        "kind: core_deep\n"
        'compatible_schema_version: "2026.07"\n'
        "required_sections:\n  - company_overview\n"
        "required_components: []\n"
        "provides_sections: []\n"
        "provides_slot_targets:\n  - industry_sections\n"
        "---\n\n"
        "{{#section company_overview}}\n公司概述。\n{{#slot industry_sections}}\n"
        "本插槽由行业片段填充。\n"
    )
    tmpl = load_template(_write(tmp_path / "deep.md", text))
    assert tmpl.kind == "core_deep"
    assert tmpl.provides_slot_targets == ["industry_sections"]
    assert len(tmpl.sections) == 1
    assert tmpl.sections[0].section_id == "company_overview"
    assert len(tmpl.slots) == 1
    assert tmpl.slots[0].slot_id == "industry_sections"
    assert "本插槽由行业片段填充" in tmpl.slots[0].fallback_body


# ---------------------------------------------------------------------------
# Repository: collisions, id/version keys, fragment lookup
# ---------------------------------------------------------------------------


def test_repository_loads_and_indexes_by_id(tmp_path: Path):
    _write(tmp_path / "brief_core_v1.md", _BRIEF_CORE)
    _write(tmp_path / "fragment_general_v1.md", _FRAGMENT)
    repo = TemplateRepository(tmp_path)
    repo.ensure_loaded()
    assert repo.get("brief_core").template_id == "brief_core"
    assert repo.get("fragment_general").kind == "fragment"
    assert len(repo.all_templates()) == 2


def test_t3_duplicate_id_version_with_different_hash(tmp_path: Path):
    _write(tmp_path / "a.md", _BRIEF_CORE)
    twin = _BRIEF_CORE.replace(
        "# 已验证重大风险事项",
        "# 已验证重大风险事项（副本）",
    )
    _write(tmp_path / "b.md", twin)
    repo = TemplateRepository(tmp_path)
    with pytest.raises(TemplateLoadError, match="T3"):
        repo.ensure_loaded()


def test_t3_same_id_version_same_hash_idempotent(tmp_path: Path):
    """Two files with identical content (same hash) claiming the same id+version
    do NOT collide — the loader treats them as the same template."""

    _write(tmp_path / "a.md", _BRIEF_CORE)
    _write(tmp_path / "b.md", _BRIEF_CORE)
    repo = TemplateRepository(tmp_path)
    repo.ensure_loaded()
    assert len(repo.all_templates()) == 1


def test_t6_fragment_section_id_collides_with_core(tmp_path: Path):
    _write(tmp_path / "brief_core_v1.md", _BRIEF_CORE)
    colliding = (
        "---\n"
        "template_id: fragment_collision\n"
        "version: 1\n"
        "kind: fragment\n"
        'compatible_schema_version: "2026.07"\n'
        "required_sections: []\n"
        "required_components: []\n"
        "provides_sections:\n  - risks_verified\n"  # collides with core section
        "provides_slot_targets: []\n"
        "---\n\n"
        "{{#section risks_verified}}\n碰撞\n"
    )
    _write(tmp_path / "fragment_collision.md", colliding)
    repo = TemplateRepository(tmp_path)
    with pytest.raises(TemplateLoadError, match="T6"):
        repo.ensure_loaded()


def test_get_unknown_template_raises_t3(tmp_path: Path):
    repo = TemplateRepository(tmp_path)
    repo.ensure_loaded()
    with pytest.raises(TemplateLoadError, match="T3"):
        repo.get("nonexistent")


def test_repository_caches_after_first_load(tmp_path: Path):
    _write(tmp_path / "brief_core_v1.md", _BRIEF_CORE)
    repo = TemplateRepository(tmp_path)
    repo.ensure_loaded()
    # Adding a new file after ensure_loaded() must NOT be picked up —
    # the repository is a frozen snapshot taken at first load.
    _write(
        tmp_path / "fragment_general_v1.md",
        _FRAGMENT,
    )
    repo.ensure_loaded()
    assert len(repo.all_templates()) == 1


# ---------------------------------------------------------------------------
# Sibling YAML configs
# ---------------------------------------------------------------------------


def test_load_yaml_config_returns_mapping(tmp_path: Path):
    _write(
        tmp_path / "labels.yaml",
        "risks_verified:\n  title: 已验证重大风险事项\n",
    )
    config = load_yaml_config(tmp_path, "labels.yaml")
    assert config == {"risks_verified": {"title": "已验证重大风险事项"}}


def test_load_yaml_config_missing_returns_empty(tmp_path: Path):
    assert load_yaml_config(tmp_path, "absent.yaml") == {}


def test_load_yaml_config_invalid_yaml_raises(tmp_path: Path):
    _write(tmp_path / "bad.yaml", "key: : :\n   bad: indentation\n")
    with pytest.raises(TemplateLoadError, match="T2"):
        load_yaml_config(tmp_path, "bad.yaml")
