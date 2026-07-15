"""Anthropic-format skill documents under ``backend/skills/``.

Each skill lives in its own directory as ``<name>/SKILL.md`` with YAML
frontmatter (``name`` + ``description``) followed by the instruction body.
Files are re-read on every call so skills can be tuned without restarting
the backend — every agent picks up edits on its next run.

Named ``skill_docs`` to avoid clashing with the ``riskagent.skills``
python package (rule-evaluation code).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("riskagent.skill_docs")

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = BACKEND_ROOT / "skills"

# Role → skills whose FULL body is injected into that agent's prompt (beyond
# the digest every agent receives). Keyed by agent_name; unmapped experts get
# the expert-analysis + evidence skills as the sensible default.
AGENT_SKILL_MAP: dict[str, tuple[str, ...]] = {
    "shen_beichen_orchestrator_agent": (
        "risk-rule-screening",
        "expert-risk-analysis",
        "report-generation-and-archive",
    ),
    "xiao_zeming_quality_agent": (
        "evidence-verification",
        "report-generation-and-archive",
    ),
    "zhou_shenyan_redteam_agent": (
        "evidence-verification",
        "expert-risk-analysis",
    ),
    "cheng_zheng_evidence_agent": (
        "pdf-parse-and-trace",
        "rag-embedding-index",
        "evidence-verification",
    ),
}
DEFAULT_EXPERT_SKILLS: tuple[str, ...] = (
    "expert-risk-analysis",
    "evidence-verification",
)


@dataclass(frozen=True)
class SkillDoc:
    name: str
    description: str
    body: str
    path: str


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    """Parse the minimal Anthropic SKILL.md frontmatter (name + description)."""

    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip("'\"")
    if not meta.get("name") or not meta.get("description"):
        return None
    return meta, parts[2].strip()


def load_skill_docs(root: Path | None = None) -> list[SkillDoc]:
    """Read every ``*/SKILL.md`` fresh from disk; skip and log malformed files."""

    skills_root = root or SKILLS_ROOT
    docs: list[SkillDoc] = []
    if not skills_root.is_dir():
        return docs
    for path in sorted(skills_root.glob("*/SKILL.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Skipping unreadable skill file %s: %s", path, exc)
            continue
        parsed = _parse_frontmatter(text)
        if parsed is None:
            logger.warning("Skipping malformed skill file (bad frontmatter): %s", path)
            continue
        meta, body = parsed
        docs.append(
            SkillDoc(
                name=meta["name"],
                description=meta["description"],
                body=body,
                path=str(path.relative_to(skills_root.parent)),
            )
        )
    return docs


def skills_digest(*, max_chars: int = 2000, root: Path | None = None) -> str:
    """One-line-per-skill catalog every agent can read regardless of role."""

    lines = [f"- {doc.name}：{doc.description}" for doc in load_skill_docs(root)]
    digest = "\n".join(lines)
    return digest[:max_chars]


def skills_for_agent(agent_name: str, *, root: Path | None = None) -> list[SkillDoc]:
    """Full skill bodies mapped to this agent's role (fallback: expert defaults)."""

    wanted = AGENT_SKILL_MAP.get(agent_name, DEFAULT_EXPERT_SKILLS)
    by_name = {doc.name: doc for doc in load_skill_docs(root)}
    return [by_name[name] for name in wanted if name in by_name]


def render_skills_section(agent_name: str, *, root: Path | None = None) -> str:
    """Digest of all skills + full text of the agent's mapped skills."""

    digest = skills_digest(root=root)
    if not digest:
        return ""
    sections = [f"【技能目录】\n{digest}"]
    for doc in skills_for_agent(agent_name, root=root):
        sections.append(f"【技能：{doc.name}】\n{doc.body}")
    return "\n\n".join(sections)
