"""Self-contained, evidence-preserving HTML report renderers."""

from __future__ import annotations

import hashlib
from html import escape
from typing import Any, Iterable
from urllib.parse import urlparse


_STYLE = """
body{margin:0;background:#f8fafc;color:#172033;font:15px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
main{max-width:1040px;margin:32px auto;padding:0 24px 48px}.card{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:24px;margin:16px 0}
h1,h2,h3{line-height:1.25;color:#0f172a}h1{font-size:30px;margin:0 0 8px}h2{font-size:21px;margin:0 0 12px}h3{font-size:17px;margin:20px 0 8px}
.muted{color:#64748b;font-size:13px}.badge{display:inline-block;border-radius:999px;background:#e0e7ff;color:#3730a3;padding:2px 9px;font-size:12px;margin-right:6px}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:13px}th,td{border:1px solid #e2e8f0;padding:8px;vertical-align:top;text-align:left}th{background:#f1f5f9}
ul{padding-left:20px}pre{white-space:pre-wrap;word-break:break-word;background:#f8fafc;padding:12px;border-radius:8px;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
a{color:#2563eb;word-break:break-all}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}.metric{background:#f8fafc;border-radius:8px;padding:10px}.metric b{display:block;font-size:18px;color:#0f172a}
"""


def ensure_risk_report_html(
    *,
    company_name: str,
    run_id: str,
    report_id: str,
    created_at: str,
    risk_level: str,
    html: str = "",
    summary: str = "",
    markdown: str = "",
    themes: list[Any] | None = None,
    exploratory_findings: list[Any] | None = None,
    counterevidence: list[Any] | None = None,
    limitations: list[Any] | None = None,
    evidence_index: list[Any] | None = None,
) -> str:
    """Return stored HTML, or render it from the persisted report fields.

    Completed runs always keep markdown and the structured report envelope.
    Older rows can have an empty ``html`` field; this rebuilds a standalone
    page from those fields and does not call a model.
    """

    if html and html.strip():
        return html
    document = {
        "summary": (summary or "").strip() or _plain_markdown(markdown),
        "themes": themes or [],
        "exploratory_findings": exploratory_findings or [],
        "counterevidence": counterevidence or [],
        "limitations": limitations or ["无额外限制。"],
        "evidence_index": evidence_index or [],
    }
    return render_risk_report_html(
        company_name=company_name,
        run_id=run_id,
        report_id=report_id,
        created_at=created_at,
        risk_level=risk_level,
        document=document,
    )


def _plain_markdown(markdown: str) -> str:
    lines = [line for line in (markdown or "").splitlines() if not line.startswith("#")]
    text = "\n".join(lines).strip()
    return text or (markdown or "").strip() or "未提供。"


def render_risk_report_html(*, company_name: str, run_id: str, report_id: str, created_at: str, risk_level: str, document: dict[str, Any]) -> str:
    """Render a persisted risk-report document without invoking model code."""

    sections = [
        _card("执行摘要", _paragraph(document.get("summary"))),
        _risk_themes(document.get("themes") or []),
        _exploratory_findings(document.get("exploratory_findings") or []),
        _counterevidence(document.get("counterevidence") or []),
        _card("局限", _list(document.get("limitations") or ["无额外限制。"])),
        _evidence_index(document.get("evidence_index") or []),
    ]
    metadata = _metadata(
        [("分析运行", run_id), ("报告编号", report_id), ("生成时间", created_at), ("总体风险", risk_level)]
    )
    return _page(f"{company_name} IPO 隐性风险尽调报告", metadata + "".join(sections))


def render_backtest_audit_html(detail: dict[str, Any]) -> str:
    """Render a frozen backtest detail payload into a readable audit artifact."""

    summary = detail.get("summary") or {}
    metrics = detail.get("metrics") or {}
    versions = detail.get("versions") or {}
    protocol = detail.get("protocol") or {}
    sections = [
        _card("审计元数据", _metadata([
            ("回测编号", summary.get("id")), ("名称", summary.get("name")),
            ("状态", summary.get("status")), ("数据快照", versions.get("dataset")),
            ("评分卡", versions.get("scorecard")), ("生成时间", summary.get("createdAt")),
        ])),
        _card("回测协议", _json_block(protocol)),
        _metrics(metrics),
        _comparison("模型与基线对比", detail.get("weightComparison") or {}),
        _comparison("消融结果", detail.get("ablationResults") or {}),
        _cohorts(detail.get("industryCohorts") or []),
        _sample_replays(detail.get("samples") or []),
    ]
    return _page(f"{summary.get('name') or 'IPO 回测'} 审计报告", "".join(sections))


def _page(title: str, body: str) -> str:
    return f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(title)}</title><style>{_STYLE}</style></head><body><main><h1>{escape(title)}</h1><p class='muted'>ProspectAlpha · 可离线审阅的冻结结果，不会触发新的模型推理。</p>{body}</main></body></html>"


def _card(title: str, body: str) -> str:
    return f"<section class='card'><h2>{escape(title)}</h2>{body}</section>"


def _paragraph(value: Any) -> str:
    return "<p>" + escape(str(value or "未提供。")) + "</p>"


def _list(values: Iterable[Any]) -> str:
    return "<ul>" + "".join(f"<li>{escape(str(value))}</li>" for value in values) + "</ul>"


def _metadata(values: Iterable[tuple[str, Any]]) -> str:
    return "<div class='grid'>" + "".join(
        f"<div class='metric'><span class='muted'>{escape(label)}</span><b>{escape(str(value or '未提供'))}</b></div>"
        for label, value in values
    ) + "</div>"


def _risk_themes(themes: list[dict[str, Any]]) -> str:
    if not themes:
        return _card("风险主题", _paragraph("未形成具有招股书证据锚点的风险主题；这不表示风险不存在。"))
    body = []
    for item in themes:
        body.append(f"<h3>{escape(str(item.get('title') or '未命名主题'))}</h3>")
        body.append(_paragraph(item.get("conclusion")))
        body.append(_paragraph(item.get("analysis")))
        body.append("<p class='muted'>风险引用：%s<br>招股书证据：%s</p>" % (
            escape("、".join(map(str, item.get("finding_ids") or [])) or "未提供"),
            escape("、".join(map(str, item.get("evidence_ids") or [])) or "未提供"),
        ))
    return _card("风险主题", "".join(body))


def _exploratory_findings(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return _card("探索性发现", _paragraph("无。"))
    body = []
    for item in findings:
        body.append(f"<h3>{escape(str(item.get('title') or '未命名风险'))}</h3>")
        body.append("<p>" + "".join(f"<span class='badge'>{escape(str(value))}</span>" for value in [item.get("risk_type") or "自由风险", item.get("evidence_grade") or "lead", item.get("hypothesis_status") or "reported"]) + "</p>")
        body.extend([_paragraph(item.get("reasoning_summary")), _paragraph(f"因果链：{item.get('causal_chain') or '未展开'}")])
        body.append("<p class='muted'>公司特异性：%s<br>行业关联：%s<br>招股书证据：%s<br>外部来源：%s<br>反证：%s</p>" % tuple(
            escape("、".join(map(str, value)) if isinstance(value, list) else str(value or "未说明"))
            for value in (item.get("company_specificity"), item.get("industry_relevance"), item.get("evidence_ids") or ["无"], item.get("external_evidence_ids") or ["未取得"], item.get("counter_evidence_ids") or ["无"])
        ))
    return _card("探索性发现", "".join(body))


def _counterevidence(items: list[dict[str, Any]]) -> str:
    if not items:
        return _card("反证与争议", _paragraph("未取得可用反证；这属于研究限制，不代表假设成立。"))
    body = []
    for item in items:
        sources = item.get("sources") or []
        links = "、".join(_link(source.get("url"), source.get("title") or source.get("source_id")) for source in sources if isinstance(source, dict))
        body.append(f"<h3>{escape(str(item.get('title') or '风险假设'))} <span class='badge'>{escape(str(item.get('status') or 'disputed'))}</span></h3><p>来源：{links or escape('、'.join(map(str, item.get('source_ids') or [])) or '无')}</p>")
    return _card("反证与争议", "".join(body))


def _evidence_index(items: list[dict[str, Any]]) -> str:
    rows = []
    for item in items:
        if "page_number" in item:
            text = str(item.get("text") or "")
            rows.append([item.get("evidence_id"), item.get("finding_id"), f"P.{item.get('page_number') or '未提供'} · {item.get('section_path') or '未提供'}", hashlib.sha256(text.encode("utf-8")).hexdigest() if text else "未提供", text])
        else:
            rows.append([item.get("evidence_id"), item.get("finding_id"), item.get("published_at") or "日期不明", "外部来源", _link(item.get("url"), item.get("title") or item.get("url"))])
    return _card("证据索引", _table(["证据 ID", "关联对象", "页码/日期", "文本哈希", "原文或来源"], rows, raw_columns={4}))


def _metrics(metrics: dict[str, Any]) -> str:
    keys = [("样本量", metrics.get("samples")), ("AUC", metrics.get("auc")), ("KS", metrics.get("ks")), ("Lift@Top20%", metrics.get("lift_top20")), ("Recall", metrics.get("recall")), ("F1", metrics.get("f1"))]
    return _card("核心指标", _metadata(keys))


def _comparison(title: str, data: dict[str, Any]) -> str:
    if not data:
        return _card(title, _paragraph("未提供。"))
    rows = [[key, _json_block(value)] for key, value in data.items()]
    return _card(title, _table(["方案", "冻结结果"], rows, raw_columns={1}))


def _cohorts(cohorts: list[dict[str, Any]]) -> str:
    rows = [[item.get("code"), item.get("name"), item.get("samples"), item.get("status"), item.get("auc") or "样本不足", item.get("ks") or "样本不足"] for item in cohorts]
    return _card("行业队列", _table(["代码", "行业", "样本", "状态", "AUC", "KS"], rows) if rows else _paragraph("未提供。"))


def _sample_replays(samples: list[dict[str, Any]]) -> str:
    if not samples:
        return _card("公司级风险—行情回放", _paragraph("未提供。"))
    body = []
    for sample in samples:
        body.append(f"<h3>{escape(str(sample.get('name') or sample.get('code') or '未命名公司'))} · {escape(str(sample.get('code') or ''))}</h3>")
        body.append(_metadata([("风险评分", sample.get("score")), ("风险等级", sample.get("risk")), ("T+5 收益", sample.get("returnPct")), ("最大回撤", sample.get("maxDrawdownPct")), ("分析截止日", sample.get("analysisAsOfDate"))]))
        body.append("<h4>公司特有隐性风险</h4>" + _json_block(sample.get("specificRisks") or []))
        body.append("<h4>上市后表现标签</h4>" + _json_block(sample.get("outcomeProfile") or {}))
        body.append("<h4>事后归因</h4>" + _json_block(sample.get("retrospectiveAttribution") or {}))
    return _card("公司级风险—行情回放", "".join(body))


def _table(headers: list[str], rows: list[list[Any]], *, raw_columns: set[int] | None = None) -> str:
    raw_columns = raw_columns or set()
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    content = "".join("<tr>" + "".join(f"<td>{value if index in raw_columns else escape(str(value or '未提供'))}</td>" for index, value in enumerate(row)) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{content}</tbody></table>"


def _json_block(value: Any) -> str:
    import json
    return "<pre>" + escape(json.dumps(value, ensure_ascii=False, indent=2, default=str)) + "</pre>"


def _link(url: Any, label: Any) -> str:
    value = str(url or "")
    if urlparse(value).scheme not in {"http", "https"}:
        return escape(str(label or "未提供"))
    return f"<a href='{escape(value, quote=True)}' rel='noreferrer'>{escape(str(label or value))}</a>"
