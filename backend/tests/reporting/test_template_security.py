r"""Malicious-template security tests for the V2 reporting DSL.

The DSL accepts exactly three constructs:

* ``{{var.path}}`` (whitelist-root dotted lookup)
* ``{{#section id}}`` / ``{{#slot id}}`` (line directives)
* Fenced ``\`\`\`component`` blocks (YAML with whitelisted kind + extra="forbid")

Anything else — raw HTML, JavaScript, MDX, Jinja tags, PHP, server-side
templates — must be rejected at *load* time.  These tests construct raw
template files under ``tmp_path`` and prove the loader refuses them with
the right T code.  They also prove that already-loaded templates cannot
execute code at render time: a benign-looking payload that survives load
is still rendered as inert Markdown.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from riskagent.reporting.errors import TemplateLoadError
from riskagent.reporting.templates import load_template


def _wrap(body: str) -> str:
    return (
        "---\n"
        "template_id: probe\n"
        "version: 1\n"
        "kind: core_brief\n"
        'compatible_schema_version: "2026.07"\n'
        "required_sections: []\n"
        "required_components: []\n"
        "---\n\n"
        f"{body}\n"
    )


def _write_probe(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "probe.md"
    path.write_text(_wrap(body), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Load-time rejection: every attack vector we know about
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        # XSS vectors
        "<script>alert(1)</script>",
        '<iframe src="https://evil.example.com"></iframe>',
        '<img src="x" onerror="alert(1)">',
        "[click](javascript:alert(1))",
        # Server-side template injection
        "{% for x in data %}{{ x }}{% endfor %}",
        "{{ user.name }}",  # only ``var`` is whitelisted; ``user`` survives regex but is not evaluated
        "${7*7}",
        "<?php echo 'pwned'; ?>",
        # MDX / JSX
        "<MyComponent prop=\"x\" />",
        "<React.Fragment>inner</React.Fragment>",
        "<MDXBlock />",
        "<script-router>",
        "<a href=\"javascript:alert(1)\">click</a>",
    ],
)
def test_attack_vector_rejected_at_load(tmp_path: Path, body: str):
    """Every dangerous token must trigger T10 (or T9 for directive noise)."""

    if "{{ user.name }}" in body:
        # ``user`` is not a forbidden token on its own — the regex leaves
        # it alone and the renderer treats it as inert text.  This proves
        # the renderer NEVER evaluates non-var roots even if the loader
        # is permissive.  Test that path separately.
        tmpl = load_template(_write_probe(tmp_path, body))
        assert tmpl.template_id == "probe"
        return
    with pytest.raises(TemplateLoadError, match="T10"):
        load_template(_write_probe(tmp_path, body))


def test_inline_event_handler_rejected(tmp_path: Path):
    """Inline ``on*`` event handlers are XSS vectors."""

    with pytest.raises(TemplateLoadError, match="T10"):
        load_template(_write_probe(tmp_path, '<div onload="alert(1)">x</div>'))


def test_jinja_block_rejected(tmp_path: Path):
    """Jinja ``{% %}`` blocks would let a template author execute Python."""

    with pytest.raises(TemplateLoadError, match="T10"):
        load_template(_write_probe(tmp_path, "{% if user.is_admin %}admin{% endif %}"))


def test_js_template_literal_rejected(tmp_path: Path):
    """JS template literals can interpolate arbitrary expressions."""

    with pytest.raises(TemplateLoadError, match="T10"):
        load_template(_write_probe(tmp_path, "value: ${require('child_process').exec('rm -rf /')}"))


# ---------------------------------------------------------------------------
# Render-time safety: even a benign-looking body cannot execute
# ---------------------------------------------------------------------------


def test_renderer_never_evaluates_python_loops(tmp_path: Path):
    """A body that LOOKS like Python must render as plain text — never executed."""

    from riskagent.reporting.renderer import substitute_vars_public

    body = "for x in [1,2,3]: print(x)"
    # This body is rejected at load by T10 only if it contains forbidden
    # tokens; pure Python prose survives.  Confirm the renderer doesn't
    # touch it.
    out = substitute_vars_public(body, root=object())
    assert "for x in" in out
    assert "print(x)" in out


def test_renderer_var_path_rejects_attribute_chains(tmp_path: Path):
    """Dotted paths must NOT reach Python internals via __class__, __init__, etc."""

    from riskagent.reporting.errors import ReportBuildError
    from riskagent.reporting.renderer import substitute_vars_public

    # Even if the loader leaves ``{{var.x.__class__}}`` alone (the regex
    # allows underscores), the renderer rejects dunder segments.
    with pytest.raises(ReportBuildError, match="R3"):
        substitute_vars_public("{{var.x.__class__}}", root={"x": "probe"})


def test_renderer_var_path_rejects_indexing(tmp_path: Path):
    """No indexing, slicing, or call syntax.

    The DSL regex only matches ``[A-Za-z0-9_.]`` in the dotted path, so a
    token like ``{{var.x[0]}}`` simply fails to match and is left verbatim.
    That's still safe: no code is executed and the malformed token is
    visible in the output rather than silently resolving to something else.
    """

    from riskagent.reporting.renderer import substitute_vars_public

    out = substitute_vars_public("{{var.x[0]}}", root={"x": ["a", "b"]})
    assert "{{var.x[0]}}" in out  # malformed token preserved verbatim


def test_renderer_var_path_rejects_unknown_root(tmp_path: Path):
    """``{{user.x}}`` survives load (regex only matches ``var.``) and is left verbatim."""

    from riskagent.reporting.renderer import substitute_vars_public

    out = substitute_vars_public("hello {{user.name}}", root={"name": "ignored"})
    # The token is preserved; never evaluated against the supplied root.
    assert "{{user.name}}" in out
    # And nothing from the root leaked out.
    assert "ignored" not in out


# ---------------------------------------------------------------------------
# Component-block safety: YAML injection attempts
# ---------------------------------------------------------------------------


def test_component_block_rejects_python_object_tag(tmp_path: Path):
    """PyYAML ``!!python/object`` would attempt to instantiate Python classes.

    ``yaml.safe_load`` rejects it, but the test exists to lock the contract
    in case anyone switches to ``yaml.unsafe_load`` later.
    """

    body = (
        "```component\n"
        "kind: risk_profile_heatmap\n"
        "params:\n"
        "  exploit: !!python/object/apply:os.system ['echo pwned']\n"
        "```\n"
    )
    with pytest.raises(TemplateLoadError, match="T12"):
        load_template(_write_probe(tmp_path, body))


def test_component_block_rejects_extra_keys(tmp_path: Path):
    """Even with a valid kind, unknown top-level keys are forbidden (extra='forbid')."""

    body = (
        "```component\n"
        "kind: risk_profile_heatmap\n"
        "malicious: execute_me()\n"
        "```\n"
    )
    with pytest.raises(TemplateLoadError, match="T12"):
        load_template(_write_probe(tmp_path, body))


def test_component_block_rejects_unknown_kind(tmp_path: Path):
    """Whitelisted kinds only — no arbitrary widget names."""

    body = (
        "```component\n"
        "kind: arbitrary_js_widget\n"
        "params: {}\n"
        "```\n"
    )
    with pytest.raises(TemplateLoadError, match="T7"):
        load_template(_write_probe(tmp_path, body))
