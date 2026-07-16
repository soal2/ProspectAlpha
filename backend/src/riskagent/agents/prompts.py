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
你收到的用户消息包含 user_requirement、assignment、query_history、candidates 和 evidence_snippets。
必须围绕用户具体要求与当前模块目标判断证据是否回答了问题；不要把语义相近但未回答目标事实的片段当作证据。
请对每条候选做严谨的二次判断，只输出一个 JSON 对象，格式：
{"items":[{"rule_id":str,"evaluation_result":"triggered"|"not_triggered"|"insufficient_evidence","severity":"low"|"medium"|"high","confidence":0.0-1.0,"reasoning_summary":str,"needs_more_evidence":bool}]}
evaluation_result=triggered 仅表示原文直接满足规则触发条件；原文直接否定触发条件时必须返回 not_triggered，不能因为 confidence 较高而判为 triggered；无法验证时返回 insufficient_evidence 且 needs_more_evidence=true。
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


def render_expert_prompt(spec_payload: dict[str, Any], *, skills_digest: str = "") -> str:
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
        skills_section = _skills_section(agent_name)
        if skills_section:
            sections.append(skills_section)
    sections.append(EXPERT_OUTPUT_CONTRACT.strip())
    return "\n\n".join(sections)


def _skills_section(agent_name: str) -> str:
    """Digest of all backend/skills docs + this role's mapped skill bodies.

    Read fresh per render so skill edits reach every agent's next run; any
    loader failure degrades to no skills section rather than breaking prompts.
    """

    try:
        from riskagent.skill_docs import render_skills_section

        return render_skills_section(agent_name)
    except Exception:
        return ""


def orchestrator_skills_digest() -> str:
    """One-line skill catalog appended to strategy/report/arbiter prompts."""

    try:
        from riskagent.skill_docs import skills_digest

        digest = skills_digest()
    except Exception:
        return ""
    return f"\n\n【技能目录】\n{digest}" if digest else ""


def render_orchestrator_prompt(name: str, fallback: str) -> str:
    """Return a strategy/report/arbiter system prompt body, preferring the file."""

    return (load_agent_prompt(name) or fallback) + orchestrator_skills_digest()
