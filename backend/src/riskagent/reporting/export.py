r"""Markdown export for V2 bundles.

The live web UI renders components via React widgets; the exported Markdown
must be readable without that layer.  Every whitelisted component kind has
a fallback renderer here that flattens its payload to plain Markdown
(tables, lists, blockquotes, or a static placeholder when no better shape
exists).

The export is **additive** — it never edits the bundle, never reaches back
into the dataset layer to re-fetch data, and never invents content.  If a
component payload is missing fields, the renderer emits a placeholder note
rather than guessing.
"""

from __future__ import annotations

from typing import Any

from .models import (
    DegradationNote,
    ModuleReport,
    ReportBundleV2,
    ReviewTriggerRecord,
    SectionRender,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def export_bundle_to_markdown(bundle: ReportBundleV2) -> str:
    """Render a bundle as a single self-contained Markdown document.

    The document is split into two H1 sections (brief / deep) followed by
    audit appendices (selection summary, triggers, degradations).
    """

    parts: list[str] = []
    parts.append(f"# 风险研究报告 — {bundle.run_id}\n")
    parts.append(_meta_block(bundle))
    parts.append("\n---\n")
    parts.append(_module_to_markdown(bundle.decision_brief, title="风险研究简报"))
    parts.append("\n---\n")
    parts.append(_module_to_markdown(bundle.deep_research, title="专项深度研究报告"))
    parts.append("\n---\n")
    parts.append(_audit_appendix(bundle))
    return "".join(parts).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Module → Markdown
# ---------------------------------------------------------------------------


def _module_to_markdown(module: ModuleReport, *, title: str) -> str:
    """Concatenate the module's pre-rendered markdown with per-component
    fallbacks spliced in below each section."""

    parts: list[str] = [f"## {title}\n"]
    for section in module.sections:
        parts.append(_section_to_markdown(section))
    return "".join(parts)


def _section_to_markdown(section: SectionRender) -> str:
    parts: list[str] = [f"### {section.title_zh}\n"]
    if section.non_formal:
        parts.append(
            "> 本章节内容不构成正式结论，仅供调查过程参考。\n\n"
        )
    if section.markdown.strip():
        parts.append(section.markdown.rstrip() + "\n\n")
    else:
        parts.append("_本轮未形成有效结论。_\n\n")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Meta block (run/artifact/template identity)
# ---------------------------------------------------------------------------


def _meta_block(bundle: ReportBundleV2) -> str:
    lines = [
        "| 字段 | 值 |",
        "| --- | --- |",
        f"| run_id | `{bundle.run_id}` |",
        f"| report_version | {bundle.report_version} |",
        f"| research_artifact_id | `{bundle.research_artifact_id}` |",
        f"| research_artifact_version | {bundle.research_artifact_version} |",
        f"| verdict_snapshot_hash | `{bundle.verdict_snapshot_hash[:16]}…` |",
        f"| brief_template | `{bundle.brief_template.template_id}` v{bundle.brief_template.version} |",
        f"| deep_template | `{bundle.deep_template.template_id}` v{bundle.deep_template.version} |",
        f"| industry | {bundle.industry_profile.detected} |",
        f"| review_state | {bundle.review_state} |",
        f"| created_at | {bundle.created_at.isoformat(timespec='seconds')} |",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Audit appendix
# ---------------------------------------------------------------------------


def _audit_appendix(bundle: ReportBundleV2) -> str:
    parts: list[str] = ["## 审计附录\n"]

    parts.append("### 事实层选择摘要\n")
    parts.append(_selection_summary_table(bundle))
    parts.append("\n")

    if bundle.review_triggers:
        parts.append("### 复核触发记录\n")
        parts.append(_review_triggers_table(bundle.review_triggers))
        parts.append("\n")
    else:
        parts.append("### 复核触发记录\n\n_无。_\n\n")

    if bundle.degradations:
        parts.append("### 降级记录\n")
        parts.append(_degradations_table(bundle.degradations))
        parts.append("\n")
    return "".join(parts)


def _selection_summary_table(bundle: ReportBundleV2) -> str:
    sel = bundle.selection
    rows = [
        ("已验证重大风险事项", _count_or_dash(sel.verified_hypothesis_ids), ", ".join(sel.verified_hypothesis_ids) or "—"),
        ("重大待核实事项", _count_or_dash(sel.material_unresolved_hypothesis_ids), ", ".join(sel.material_unresolved_hypothesis_ids) or "—"),
        ("争议假设", _count_or_dash(sel.contested_hypothesis_ids), ", ".join(sel.contested_hypothesis_ids) or "—"),
        ("部分支持假设", _count_or_dash(sel.partially_supported_hypothesis_ids), ", ".join(sel.partially_supported_hypothesis_ids) or "—"),
        ("已否定假设", _count_or_dash(sel.refuted_hypothesis_ids), ", ".join(sel.refuted_hypothesis_ids) or "—"),
        ("未决假设", _count_or_dash(sel.inconclusive_hypothesis_ids), ", ".join(sel.inconclusive_hypothesis_ids) or "—"),
        ("tier1 证据", _count_or_dash(sel.tier1_evidence_ids), "—"),
        ("tier2 证据", _count_or_dash(sel.tier2_evidence_ids), "—"),
        ("tier3 证据（隔离）", _count_or_dash(sel.tier3_evidence_ids), "—"),
        ("未分级证据", _count_or_dash(sel.unknown_tier_evidence_ids), "—"),
    ]
    lines = ["| 类别 | 数量 | 备注 |", "| --- | --- | --- |"]
    for label, count, note in rows:
        lines.append(f"| {label} | {count} | {note} |")
    return "\n".join(lines) + "\n"


def _review_triggers_table(triggers: list[ReviewTriggerRecord]) -> str:
    lines = ["| 触发规则 | 详情 | 涉及假设 |", "| --- | --- | --- |"]
    for t in triggers:
        hyps = ", ".join(t.hypothesis_ids) if t.hypothesis_ids else "—"
        detail = t.detail or "—"
        lines.append(f"| `{t.trigger_name}` | {detail} | {hyps} |")
    return "\n".join(lines) + "\n"


def _degradations_table(notes: list[DegradationNote]) -> str:
    lines = ["| 编号 | 章节 | 说明 |", "| --- | --- | --- |"]
    for n in notes:
        section = n.section_id or "—"
        detail = n.detail or n.message_zh
        lines.append(f"| `{n.code.value}` | {section} | {detail} |")
    return "\n".join(lines) + "\n"


def _count_or_dash(ids: list[str]) -> str:
    return str(len(ids)) if ids else "0"


# ---------------------------------------------------------------------------
# Component payload → Markdown fallback
# ---------------------------------------------------------------------------


def render_component_payload(kind: str, payload: dict[str, Any]) -> str:
    """Flatten a component payload into Markdown.

    Called by callers that want to surface a component outside of its
    rendered section context (e.g. evidence appendix).  For in-section
    rendering, the bundle's pre-rendered ``markdown`` already contains
    the original component fence — this helper is for exported dumps.
    """

    handler = _PAYLOAD_RENDERERS.get(kind)
    if handler is None:
        return f"_[组件 `{kind}` 暂无导出渲染]_"
    try:
        return handler(payload)
    except Exception:  # noqa: BLE001 — degrade to placeholder, never crash export
        return f"_[组件 `{kind}` 导出失败]_"


def _render_risk_profile_heatmap(payload: dict[str, Any]) -> str:
    cells = payload.get("cells") or []
    if not cells:
        return "_（无风险热力图数据）_\n"
    lines = [
        "| 维度 | 严重程度 | 置信度 | 关联假设 |",
        "| --- | --- | --- | --- |",
    ]
    for cell in cells:
        dimension = cell.get("dimension", "—")
        severity = cell.get("severity", "—")
        confidence = cell.get("confidence_level") or "—"
        hyps = ", ".join(cell.get("hypothesis_ids") or []) or "—"
        lines.append(f"| {dimension} | {severity} | {confidence} | {hyps} |")
    return "\n".join(lines) + "\n"


def _render_risk_matrix(payload: dict[str, Any]) -> str:
    rows = payload.get("rows") or []
    if not rows:
        return "_（无风险矩阵数据）_\n"
    lines = [
        "| 假设 ID | 维度 | 重要性 | 置信度 | 状态 | 支持证据 | 反证 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {hid} | {dim} | {imp} | {conf} | {status} | {sup} | {ctr} |".format(
                hid=row.get("hypothesis_id", "—"),
                dim=row.get("dimension", "—"),
                imp=row.get("importance", "—"),
                conf=row.get("confidence_level") or "—",
                status=row.get("status", "—"),
                sup=row.get("supporting_count", 0),
                ctr=row.get("counter_count", 0),
            )
        )
    return "\n".join(lines) + "\n"


def _render_risk_card(payload: dict[str, Any]) -> str:
    items = payload.get("items") or []
    if not items:
        return "_（风险卡片为空）_\n"
    parts: list[str] = []
    scope = payload.get("scope", "—")
    parts.append(f"_范围：{scope}_\n")
    for item in items:
        parts.append(f"#### {item.get('title', '未命名假设')}")
        parts.append(
            f"- 假设 ID: `{item.get('hypothesis_id', '—')}`"
        )
        parts.append(
            f"- 维度: {item.get('dimension', '—')} | 重要性: {item.get('importance', '—')} | "
            f"状态: {item.get('status', '—')} | 置信度: {item.get('confidence_level') or '—'}"
        )
        if item.get("rule_ids"):
            parts.append(f"- 关联规则: {', '.join(item['rule_ids'])}")
        if item.get("supporting"):
            parts.append("- 支持证据:")
            for ev in item["supporting"]:
                parts.append(_format_evidence_ref(ev))
        if item.get("counter"):
            parts.append("- 反证:")
            for ev in item["counter"]:
                parts.append(_format_evidence_ref(ev))
        if item.get("residual_uncertainty"):
            parts.append(f"- 残余不确定性: {item['residual_uncertainty']}")
        parts.append("")
    return "\n".join(parts) + "\n"


def _render_financial_trend(payload: dict[str, Any]) -> str:
    if not payload.get("available"):
        return "_（财务趋势数据不可用）_\n"
    points = payload.get("points") or []
    if not points:
        return "_（无财务趋势数据）_\n"
    lines = ["| 期间 | 指标 | 数值 | 来源证据 |", "| --- | --- | --- | --- |"]
    for p in points:
        value = p.get("value")
        value_str = "—" if value is None else str(value)
        ev = p.get("source_evidence_id") or "—"
        lines.append(
            f"| {p.get('period', '—')} | {p.get('metric', '—')} | {value_str} | `{ev}` |"
        )
    return "\n".join(lines) + "\n"


def _render_peer_comparison(payload: dict[str, Any]) -> str:
    if not payload.get("available"):
        return "_（同业对比数据不可用）_\n"
    rows = payload.get("rows") or []
    if not rows:
        return "_（无同业对比数据）_\n"
    lines = ["| 同业 | 指标 | 数值 | 来源证据 |", "| --- | --- | --- | --- |"]
    for r in rows:
        value = r.get("value")
        value_str = "—" if value is None else str(value)
        ev = r.get("source_evidence_id") or "—"
        lines.append(
            f"| {r.get('peer', '—')} | {r.get('metric', '—')} | {value_str} | `{ev}` |"
        )
    return "\n".join(lines) + "\n"


def _render_pipeline_chart(payload: dict[str, Any]) -> str:
    stages = payload.get("stages") or []
    if not stages:
        return "_（管线图为空）_\n"
    lines = ["| 阶段 | 详情 |", "| --- | --- |"]
    for stage in stages:
        name = stage.get("name") or stage.get("stage") or "—"
        detail = stage.get("detail") or stage.get("status") or "—"
        lines.append(f"| {name} | {detail} |")
    return "\n".join(lines) + "\n"


def _render_event_timeline(payload: dict[str, Any]) -> str:
    items = payload.get("items") or []
    if not items:
        return "_（事件时间线为空）_\n"
    lines = ["| 日期 | 事件 | 来源 |", "| --- | --- | --- |"]
    for item in items:
        date = item.get("event_date") or "未注明"
        ev = item.get("evidence_id") or "—"
        title = item.get("title", "—")
        if item.get("is_post_prospectus_update"):
            title = f"{title} _(招股书后更新)_"
        lines.append(f"| {date} | {title} | `{ev}` |")
    return "\n".join(lines) + "\n"


def _render_risk_causal_graph(payload: dict[str, Any]) -> str:
    edges = payload.get("edges") or []
    if not edges:
        return "_（风险因果图为空）_\n"
    lines = ["| 起点 | 终点 | 共享证据 |", "| --- | --- | --- |"]
    for edge in edges:
        shared = ", ".join(edge.get("shared_evidence_ids") or []) or "—"
        lines.append(
            f"| `{edge.get('from_hypothesis_id', '—')}` | "
            f"`{edge.get('to_hypothesis_id', '—')}` | {shared} |"
        )
    return "\n".join(lines) + "\n"


def _render_hypothesis_matrix(payload: dict[str, Any]) -> str:
    rows = payload.get("rows") or []
    if not rows:
        return "_（假设矩阵为空）_\n"
    lines = [
        "> 本章节内容不构成正式结论。\n",
        "| 假设 | 维度 | 重要性 | 状态 | 置信度 | 反证检索 | 关联规则 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        rules = ", ".join(row.get("rule_ids") or []) or "—"
        refutation = "是" if row.get("refutation_search_done") else "否"
        gate = row.get("gate_passed")
        gate_str = "通过" if gate else ("未通过" if gate is False else "—")
        lines.append(
            f"| {row.get('title', '—')} | {row.get('dimension', '—')} | "
            f"{row.get('importance', '—')} | {row.get('status', '—')} | "
            f"{row.get('confidence_level') or '—'} | {refutation} ({gate_str}) | {rules} |"
        )
    return "\n".join(lines) + "\n"


def _render_source_quality(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "| 来源层级 | 数量 |",
        "| --- | --- |",
        f"| tier1 | {summary.get('tier1_count', 0)} |",
        f"| tier2 | {summary.get('tier2_count', 0)} |",
        f"| tier3（隔离） | {summary.get('tier3_count', 0)} |",
        f"| 未分级 | {summary.get('unknown_count', 0)} |",
        f"| 不可访问 | {summary.get('inaccessible_count', 0)} |",
    ]
    return "\n".join(lines) + "\n"


def _render_research_coverage(payload: dict[str, Any]) -> str:
    dims = payload.get("coverage_dimensions") or []
    if not dims:
        return "_（覆盖维度为空）_\n"
    lines = ["| 维度 | 状态 | 假设数 | 已验证 |", "| --- | --- | --- | --- |"]
    for dim in dims:
        lines.append(
            f"| {dim.get('dimension', '—')} | {dim.get('status', '—')} | "
            f"{dim.get('hypothesis_count', 0)} | {dim.get('verified_count', 0)} |"
        )
    return "\n".join(lines) + "\n"


def _format_evidence_ref(ev: dict[str, Any]) -> str:
    """One evidence card as a nested blockquote line."""

    parts: list[str] = []
    title = ev.get("title") or "（无标题）"
    url = ev.get("url") or ""
    site = ev.get("site_name") or ""
    tier = ev.get("tier") or "unknown"
    accessible = ev.get("accessible", True)
    if url:
        parts.append(f"  - [{title}]({url})")
    else:
        parts.append(f"  - {title} _(无 URL)_")
    meta_bits = [f"tier={tier}"]
    if site:
        meta_bits.append(f"site={site}")
    if not accessible:
        meta_bits.append("不可访问")
    parts.append(f"    - 来源: {', '.join(meta_bits)}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_PAYLOAD_RENDERERS = {
    "risk_profile_heatmap": _render_risk_profile_heatmap,
    "risk_matrix": _render_risk_matrix,
    "risk_card": _render_risk_card,
    "financial_trend": _render_financial_trend,
    "pipeline_chart": _render_pipeline_chart,
    "peer_comparison": _render_peer_comparison,
    "event_timeline": _render_event_timeline,
    "risk_causal_graph": _render_risk_causal_graph,
    "hypothesis_matrix": _render_hypothesis_matrix,
    "source_quality": _render_source_quality,
    "research_coverage": _render_research_coverage,
}
