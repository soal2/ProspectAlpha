"""Flexible hidden-first report envelope with strict reference integrity."""

from __future__ import annotations

from typing import Any


def build_report_document(run: Any, integrated: Any) -> dict[str, Any]:
    evidence_by_id = {item.evidence_id: item for item in run.evidence}
    source_by_id = {item.source_id: item for item in run.research_sources}
    admitted = [
        item
        for item in run.findings
        if item.verification_status in {"supported", "weakly_supported"}
    ]
    refuted_ids = {
        item.hypothesis_id
        for item in run.hypotheses
        if item.status == "refuted"
    }
    themes: list[dict[str, Any]] = []
    themed_findings: set[str] = set()
    for theme in getattr(integrated, "risk_themes", []) or []:
        finding_ids = [item for item in theme.finding_ids if any(f.finding_id == item for f in admitted)]
        if not finding_ids:
            continue
        evidence_ids = [
            item
            for item in theme.evidence_ids
            if item in evidence_by_id
            and evidence_by_id[item].finding_id in finding_ids
        ]
        for finding_id in finding_ids:
            finding = next(item for item in admitted if item.finding_id == finding_id)
            if not any(evidence_by_id[item].finding_id == finding_id for item in evidence_ids):
                evidence_ids.extend(
                    item for item in finding.evidence_ids if item in evidence_by_id
                )
        themes.append(
            {
                "title": theme.title,
                "conclusion": theme.conclusion,
                "analysis": theme.analysis,
                "finding_ids": list(dict.fromkeys(finding_ids)),
                "evidence_ids": list(dict.fromkeys(evidence_ids)),
            }
        )
        themed_findings.update(finding_ids)
    for finding in admitted:
        if finding.finding_id in themed_findings:
            continue
        themes.append(
            {
                "title": finding.title,
                "conclusion": finding.reasoning_summary,
                "analysis": finding.causal_chain or finding.reasoning_summary,
                "finding_ids": [finding.finding_id],
                "evidence_ids": [item for item in finding.evidence_ids if item in evidence_by_id],
            }
        )
    exploratory = [
        _finding_payload(item)
        for item in admitted
        if item.origin != "rule"
        or item.evidence_grade in {"inferential", "lead"}
        or item.confidence < 0.55
    ]
    counterevidence = []
    for hypothesis in run.hypotheses:
        if hypothesis.status not in {"disputed", "refuted"} and hypothesis.hypothesis_id not in refuted_ids:
            continue
        counterevidence.append(
            {
                "hypothesis_id": hypothesis.hypothesis_id,
                "title": hypothesis.title,
                "status": hypothesis.status,
                "source_ids": hypothesis.counter_evidence_ids,
                "sources": [
                    source_by_id[item].model_dump(mode="json")
                    for item in hypothesis.counter_evidence_ids
                    if item in source_by_id
                ],
            }
        )
    evidence_index = [
        {
            "evidence_id": item.evidence_id,
            "finding_id": item.finding_id,
            "document_block_id": item.document_block_id,
            "page_number": item.page_number,
            "section_path": item.section_path,
            "text": item.text,
        }
        for item in run.evidence
    ]
    evidence_index.extend(
        {
            "evidence_id": item.source_id,
            "finding_id": item.hypothesis_id,
            "url": item.url,
            "title": item.title,
            "published_at": item.published_at,
            "eligible_pre_cutoff": item.eligible_pre_cutoff,
        }
        for item in run.research_sources
    )
    limitations = list(
        dict.fromkeys(
            [
                *list(getattr(integrated, "limitations", []) or []),
                *list(run.research_limitations or []),
            ]
        )
    )
    return {
        "summary": integrated.executive_summary,
        "themes": themes,
        "exploratory_findings": exploratory,
        "counterevidence": counterevidence,
        "limitations": limitations,
        "evidence_index": evidence_index,
    }


def render_report_markdown(run: Any, document: dict[str, Any]) -> str:
    lines = [
        f"# {run.company_name} IPO 隐性风险尽调报告",
        "",
        "## 执行摘要",
        "",
        str(document["summary"]),
        "",
        "## 风险主题",
        "",
    ]
    if not document["themes"]:
        lines.append("未形成具有招股书证据锚点的风险主题；这不表示风险不存在。")
    for theme in document["themes"]:
        lines.extend(
            [
                f"### {theme['title']}",
                "",
                str(theme["conclusion"]),
                "",
                str(theme["analysis"]),
                "",
                f"- 风险引用：{', '.join(theme['finding_ids'])}",
                f"- 招股书证据：{', '.join(theme['evidence_ids'])}",
                "",
            ]
        )
    lines.extend(["## 探索性发现", ""])
    if not document["exploratory_findings"]:
        lines.append("无。")
    for finding in document["exploratory_findings"]:
        lines.extend(
            [
                f"### {finding['title']} · {finding['evidence_grade']}",
                "",
                finding["reasoning_summary"],
                "",
                f"- 风险类型：{finding['risk_type']}",
                f"- 因果链：{finding['causal_chain']}",
                f"- 公司特异性：{finding['company_specificity']}",
                f"- 置信度（仅展示）：{finding['confidence']:.2f}",
                f"- 状态：{finding['hypothesis_status'] or 'reported'}",
                f"- 招股书证据：{', '.join(finding['evidence_ids'])}",
                f"- 外部证据：{', '.join(finding['external_evidence_ids']) or '无'}",
                "",
            ]
        )
    lines.extend(["## 反证与争议", ""])
    if not document["counterevidence"]:
        lines.append("未取得可用反证；这属于研究限制，不代表假设成立。")
    for item in document["counterevidence"]:
        lines.append(f"- {item['title']}（{item['status']}）：{', '.join(item['source_ids']) or '无可用来源'}")
    lines.extend(["", "## 局限", ""])
    lines.extend(f"- {item}" for item in document["limitations"] or ["无额外限制。"])
    lines.extend(["", "## 证据索引", ""])
    for item in document["evidence_index"]:
        if "page_number" in item:
            lines.append(
                f"- {item['evidence_id']} · {item['finding_id']} · P.{item['page_number']} · {item['section_path']}"
            )
        else:
            lines.append(
                f"- {item['evidence_id']} · {item['title'] or item['url']} · {item['published_at'] or '日期不明'}"
            )
    return "\n".join(lines).strip() + "\n"


def _finding_payload(finding: Any) -> dict[str, Any]:
    return {
        "finding_id": finding.finding_id,
        "risk_type": finding.risk_type or finding.category,
        "title": finding.title,
        "reasoning_summary": finding.reasoning_summary,
        "causal_chain": finding.causal_chain or finding.reasoning_summary,
        "industry_relevance": finding.industry_relevance,
        "company_specificity": finding.company_specificity,
        "expected_market_effects": finding.expected_market_effects,
        "confidence": finding.confidence,
        "evidence_grade": finding.evidence_grade,
        "hypothesis_status": finding.hypothesis_status,
        "evidence_ids": finding.evidence_ids,
        "external_evidence_ids": finding.external_evidence_ids,
        "counter_evidence_ids": finding.counter_evidence_ids,
    }
