"""Baseline due-diligence planner and multi-language query expansion.

For ``standard``/``deep`` runs the planner emits one :class:`CoverageTask`
per baseline dimension (财务/业务/治理/法务监管/行业竞争/估值/核心人员/供应链/
声誉) — never fewer.  ``quick`` mode emits no baseline tasks; research there
is anomaly-triggered only.  User focus may re-prioritise but cannot drop
dimensions (see :func:`apply_user_focus`).

Multi-language expansion uses a hand-curated character folding table (D9) —
not OpenCC, which would be a heavy dependency for the few hundred chars that
matter in financial and corporate names.  ``canonical_form`` collapses
traditional↔simplified pairs so mechanical 3x expansion is avoided: only
variants that survive canonical dedup are emitted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .models import BASELINE_DIMENSIONS, CoverageTask, PlannedQuery


# Tiny folding table for the chars that actually show up in HK/IPO due
# diligence: corporate-name suffixes, financial-statement terms, regulatory
# verbs.  Upgradeable in one place to OpenCC if coverage proves insufficient.
_TRAD_TO_SIMP: dict[str, str] = {
    # Corporate / name suffixes
    "國": "国", "證": "证", "監": "监", "銀": "银", "報": "报",
    "網": "网", "與": "与", "臺": "台", "灣": "湾", "東": "东",
    "長": "长", "廠": "厂", "員": "员", "務": "务", "聯": "联",
    "絡": "络", "集": "集", "團": "团", "公": "公", "司": "司",
    "騰": "腾", "訊": "讯", "華": "华", "寧": "宁", "榮": "荣",
    "樂": "乐", "達": "达", "際": "际", "機": "机", "會": "会",
    "企": "企", "業": "业", "控股": "控股",
    # Financial / accounting
    "資": "资", "產": "产", "負": "负", "債": "债", "營": "营",
    "運": "运", "額": "额", "計": "计", "劃": "划", "帳": "账",
    "項": "项", "績": "绩", "結": "结", "稅": "税", "據": "据",
    "貨": "货", "幣": "币", "匯": "汇", "購": "购", "併": "并",
    "權": "权", "創": "创", "經": "经", "濟": "济", "財": "财",
    "務": "务", "審": "审", "計": "计", "決": "决", "算": "算",
    # Legal / regulatory
    "條": "条", "違": "违", "約": "约", "關": "关", "係": "系",
    "牽": "牵", "訴": "诉", "訟": "讼", "裁": "裁", "處": "处",
    "罰": "罚", "規": "规", "則": "则", "法": "法", "律": "律",
    "罰": "罚", "責": "责", "任": "任",
    # Industry / operations
    "構": "构", "場": "场", "園": "园", "館": "馆", "島": "岛",
    "嶼": "屿", "農": "农", "礦": "矿", "鐵": "铁", "鋼": "钢",
    "車": "车", "電": "电", "氣": "气", "醫": "医", "藥": "药",
    "製": "制", "豬": "猪", "雞": "鸡", "魚": "鱼", "漁": "渔",
    "職": "职", "歷": "历",
}
_SIMP_TO_TRAD: dict[str, str] = {v: k for k, v in _TRAD_TO_SIMP.items() if v != k}


def _fold_trad_to_simp(text: str) -> str:
    return "".join(_TRAD_TO_SIMP.get(ch, ch) for ch in text)


def _fold_simp_to_trad(text: str) -> str:
    return "".join(_SIMP_TO_TRAD.get(ch, ch) for ch in text)


_PUNCT_RE = re.compile(r"[\s\W_]+", flags=re.UNICODE)


def canonical_form(text: str) -> str:
    """Normalize a query for dedup.

    Traditional↔simplified pairs collapse, so '騰訊' and '腾讯' share a
    canonical_form.  Whitespace and punctuation are stripped; case is folded.
    """
    if not text:
        return ""
    folded = _fold_trad_to_simp(text)
    return _PUNCT_RE.sub("", folded).lower()


@dataclass(frozen=True)
class AssignmentHint:
    """Lightweight view of a strategy assignment used only for planning."""

    module_id: str
    module: str
    goal: str


# Keyword → baseline-dimension lookup.  Applied by substring match against
# the assignment's module name; first hit wins per assignment.
_MODULE_TO_DIMENSION: dict[str, str] = {
    "财务": "财务", "會計": "财务", "会计": "财务",
    "业务": "业务", "市場": "业务", "市场": "业务",
    "治理": "治理",
    "法务": "法务监管", "法務": "法务监管",
    "监管": "法务监管", "監管": "法务监管", "合规": "法务监管", "訴訟": "法务监管",
    "行业": "行业竞争", "行業": "行业竞争", "竞争": "行业竞争",
    "估值": "估值",
    "人员": "核心人员", "人員": "核心人员", "管理层": "核心人员", "高管": "核心人员",
    "供应链": "供应链", "供應鏈": "供应链",
    "声誉": "声誉", "聲譽": "声誉", "舆情": "声誉",
}


def _detect_language(text: str) -> str:
    has_chinese = any("一" <= ch <= "鿿" for ch in text)
    if not has_chinese:
        return "en"
    if any(ch in _TRAD_TO_SIMP for ch in text):
        return "zh-Hant"
    return "zh-Hans"


def _split_segments(base: str) -> list[str]:
    return [seg.strip() for seg in re.split(r"\s*[/｜|]\s*", base) if seg.strip()]


def _add_variant(out: dict[str, PlannedQuery], text: str, language: str) -> None:
    key = canonical_form(text)
    if not key or key in out:
        return
    out[key] = PlannedQuery(text=text, language=language, canonical_form=key)


def expand_queries(
    *bases: str, languages: Iterable[str] = ("zh-Hans", "zh-Hant", "en")
) -> list[PlannedQuery]:
    """Expand base query strings into deduplicated language variants.

    Each base may be:
      * a single zh-Hans string (zh-Hant derived mechanically via folding),
      * a single zh-Hant string (zh-Hans derived mechanically),
      * an English string (kept as-is),
      * a composite ``"zh / en"`` (segments detected and tagged individually).

    Only variants that survive ``canonical_form`` deduplication are emitted —
    no mechanical 3x expansion when both Chinese forms collapse.
    """
    wanted = set(languages)
    out: dict[str, PlannedQuery] = {}
    for base in bases:
        base = (base or "").strip()
        if not base:
            continue
        for segment in _split_segments(base):
            language = _detect_language(segment)
            if language not in wanted:
                continue
            _add_variant(out, segment, language)
            if language == "zh-Hans" and "zh-Hant" in wanted:
                derived = _fold_simp_to_trad(segment)
                if derived != segment:
                    _add_variant(out, derived, "zh-Hant")
            elif language == "zh-Hant" and "zh-Hans" in wanted:
                derived = _fold_trad_to_simp(segment)
                if derived != segment:
                    _add_variant(out, derived, "zh-Hans")
    return list(out.values())


# Seed templates per dimension.  ``{company}`` is substituted at build time.
# Composite templates with ``/`` let callers supply a zh/en pair directly.
_BASELINE_TEMPLATES: dict[str, tuple[str, ...]] = {
    "财务": (
        "{company} 财务 异常 招股书",
        "{company} 审计意见 关键审计事项",
        "{company} revenue recognition policy",
    ),
    "业务": (
        "{company} 业务模式 风险",
        "{company} 主营业务 依赖 客户",
    ),
    "治理": (
        "{company} 公司治理 独立董事",
        "{company} 关联交易 控股股东",
    ),
    "法务监管": (
        "{company} 监管处罚 / 監管處罰",
        "{company} 诉讼 法律纠纷",
        "{company} regulatory action",
    ),
    "行业竞争": (
        "{company} 行业 竞争格局",
        "{company} 市场 份额 趋势",
    ),
    "估值": (
        "{company} 估值 合理性",
        "{company} 招股价 估值方法",
    ),
    "核心人员": (
        "{company} 高管 背景 履历",
        "{company} 核心人员 离职",
    ),
    "供应链": (
        "{company} 供应商 集中风险",
        "{company} 主要客户 依赖",
    ),
    "声誉": (
        "{company} 媒体 负面报道",
        "{company} 舆情 争议",
    ),
}

_PRIORITY_HIGH: frozenset[str] = frozenset({"财务", "治理", "法务监管"})


def build_baseline_plan(
    *,
    company_name: str,
    assignments: list[AssignmentHint] | None = None,
    analysis_depth: str = "standard",
) -> list[CoverageTask]:
    """Emit one :class:`CoverageTask` per baseline dimension.

    Returns an empty list for ``quick`` depth — research there is anomaly-
    triggered only.  ``standard`` uses the first two templates per dimension;
    ``deep`` uses three.  Dimensions are never removed regardless of user
    focus — focus is applied as a priority bump by :func:`apply_user_focus`.
    """
    if analysis_depth == "quick":
        return []
    company = (company_name or "").strip()
    if not company:
        return []
    templates_per_task = 2 if analysis_depth == "standard" else 3
    module_for = _map_modules_to_dimensions(assignments or [])
    tasks: list[CoverageTask] = []
    for dimension in BASELINE_DIMENSIONS:
        templates = _BASELINE_TEMPLATES[dimension][:templates_per_task]
        bases = [t.format(company=company) for t in templates]
        queries = expand_queries(*bases)
        tasks.append(
            CoverageTask(
                dimension=dimension,
                module_id=module_for.get(dimension),
                priority="high" if dimension in _PRIORITY_HIGH else "medium",
                queries=queries,
                status="pending",
            )
        )
    return tasks


def apply_user_focus(tasks: list[CoverageTask], focus: str) -> list[CoverageTask]:
    """Bump priority of tasks whose dimension matches ``focus``.

    Never removes tasks.  Match is fuzzy: ``focus`` is canonicalized and
    compared against each dimension's canonical form.
    """
    if not focus:
        return tasks
    focus_norm = canonical_form(focus)
    if not focus_norm:
        return tasks
    for task in tasks:
        if focus_norm in canonical_form(task.dimension) or focus in task.dimension:
            task.priority = "high"
    return tasks


def _map_modules_to_dimensions(
    assignments: list[AssignmentHint],
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for assignment in assignments:
        module_norm = assignment.module.strip()
        for keyword, dimension in _MODULE_TO_DIMENSION.items():
            if keyword in module_norm and dimension not in mapping:
                mapping[dimension] = assignment.module_id
                break
    return mapping
