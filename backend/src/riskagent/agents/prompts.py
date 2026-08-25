"""File-backed per-agent system prompts with built-in fallbacks.

Prompt bodies live in ``backend/configs/prompts/<agent_name>.md`` so that
personas can be tuned without code changes. Every expert prompt gets the
shared JSON output contract appended so the response parser never breaks,
regardless of what the persona file says.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PROMPTS_ROOT = BACKEND_ROOT / "configs" / "prompts"

# Appended to every expert prompt unconditionally: the supervisor parses the
# LLM reply against this exact schema, so persona files must not override it.
EXPERT_OUTPUT_CONTRACT = """
【输出契约（不可更改）】
你收到的用户消息包含 user_requirement、assignment、query_history、candidates、evidence_snippets 和 evidence_by_rule。
必须围绕用户具体要求与当前模块目标判断证据是否回答了问题；不要把语义相近但未回答目标事实的片段当作证据。
优先逐条使用 evidence_by_rule[rule_id] 中为该规则重排后的多块证据；不得用其他规则的片段替代当前规则证据。
数值、占比或趋势条件应联合阅读同一规则下的多页表格和文字，不要因为单个片段未同时出现分子、分母就直接忽略其余证据。
占比阈值必须同时取得非零分母和对应分子；当原文明示尚无产品销售收入时，不得把“依赖某产品/技术”推算为产品收入占比100%，应返回 insufficient_evidence 或使用不依赖收入占比的其他规则。
请对每条候选做严谨的二次判断，只输出一个 JSON 对象，格式：
{"items":[{"rule_id":str,"evaluation_result":"triggered"|"not_triggered"|"insufficient_evidence","severity":"low"|"medium"|"high","confidence":0.0-1.0,"reasoning_summary":str,"needs_more_evidence":bool}]}
evaluation_result=triggered 仅表示原文直接满足规则触发条件；原文直接否定触发条件时必须返回 not_triggered，不能因为 confidence 较高而判为 triggered；无法验证时返回 insufficient_evidence 且 needs_more_evidence=true。
允许基于多段相关原文形成推测性研判，但推测必须保留在 reasoning_summary 中，并返回 insufficient_evidence + needs_more_evidence=true，明确已知依据、推断链条与缺失的确认材料；不得把间接线索升级为 triggered。
reasoning_summary 为 80-200 字，必须引用页码/章节或证据片段原文。
不得输出 JSON 之外的任何内容。
"""


@lru_cache(maxsize=64)
def load_agent_prompt(agent_name: str) -> str | None:
    """Return the prompt body for ``agent_name``, or None when no file exists."""

    if not agent_name or "/" in agent_name or "\\" in agent_name or ".." in agent_name:
        return None
    path = PROMPTS_ROOT / f"{agent_name}.md"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def render_expert_prompt(
    spec_payload: dict[str, Any],
    *,
    skills_digest: str = "",
    assigned_skill_ids: list[str] | None = None,
    skill_snapshot: dict[str, Any] | None = None,
) -> str:
    """Build the full system prompt for one expert from its capability payload.

    Falls back to a generic persona derived from the payload when the agent
    has no dedicated prompt file, so newly added experts keep working.
    """

    agent_name = str(spec_payload.get("agent_name") or "")
    body = load_agent_prompt(agent_name)
    if body is None:
        title = str(spec_payload.get("title") or agent_name or "风险分析专家")
        role = str(spec_payload.get("role") or spec_payload.get("role_zh") or title)
        capabilities = "、".join(
            str(item) for item in spec_payload.get("capabilities") or []
        )
        body = (
            f"你是 ProspectAlpha 多 Agent 团队中的「{title}」专家（agent_name={agent_name}）。\n"
            f"职责：{role}。\n"
            + (f"能力标签：{capabilities}。\n" if capabilities else "")
            + "请基于规则预筛候选与招股书证据片段完成严谨的风险二次判断，"
            "所有结论必须可溯源到页码、章节与原文片段；证据不足时明确说明。\n"
            "当证据不足时，可通过 A2A 对等消息向战略编排 Agent（strategic_agent）请求任务指引。"
        )
    sections = [body]
    if skills_digest.strip():
        sections.append(f"【可用技能】\n{skills_digest.strip()}")
    else:
        skills_section = _skills_section(
            agent_name,
            assigned_skill_ids=assigned_skill_ids,
            skill_snapshot=skill_snapshot,
        )
        if skills_section:
            sections.append(skills_section)
    sections.append(EXPERT_OUTPUT_CONTRACT.strip())
    return "\n\n".join(sections)


def _skills_section(
    agent_name: str,
    *,
    assigned_skill_ids: list[str] | None = None,
    skill_snapshot: dict[str, Any] | None = None,
) -> str:
    """Render only Skill bodies assigned to this expert for this run."""

    try:
        from riskagent.skill_docs import render_skills_section

        return render_skills_section(
            agent_name,
            assigned_skill_ids=assigned_skill_ids,
            snapshot=skill_snapshot,
        )
    except Exception:
        return ""


def orchestrator_skills_digest(skill_snapshot: dict[str, Any] | None = None) -> str:
    """Append the runtime catalog plus the controller's governance documents."""

    try:
        if skill_snapshot is None:
            from riskagent.skill_docs import get_skill_catalog, skills_digest

            digest = skills_digest()
            source = {
                name: definition.document
                for name, definition in get_skill_catalog().definitions.items()
            }
        else:
            digest = "\n".join(
                f"- {name}@{item.get('version')}：{item.get('description', '')}"
                f"（owners={','.join(item.get('owners') or [])}；available={str(item.get('available', False)).lower()}）"
                for name, item in (skill_snapshot.get("skills") or {}).items()
                if item.get("execution") != "offline"
            )
            source = skill_snapshot.get("skills") or {}
    except Exception:
        return ""
    sections = [f"【技能目录】\n{digest}"] if digest else []
    for skill_id in (
        "orchestration",
        "risk-rule-screening",
        "report-generation-and-archive",
    ):
        item = source.get(skill_id)
        if item is None:
            continue
        version = item.version if hasattr(item, "version") else item.get("version")
        body = item.body if hasattr(item, "body") else item.get("body")
        if body:
            sections.append(f"【总控技能：{skill_id}@{version}】\n{body}")
    return "\n\n" + "\n\n".join(sections) if sections else ""


def render_orchestrator_prompt(
    name: str,
    fallback: str,
    *,
    skill_snapshot: dict[str, Any] | None = None,
) -> str:
    """Return a strategy/report/arbiter system prompt body, preferring the file."""

    return (load_agent_prompt(name) or fallback) + orchestrator_skills_digest(skill_snapshot)
