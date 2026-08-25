"""Validated Skill documents and immutable runtime catalog snapshots."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping

import yaml

from riskagent.skill_runtime import SCHEMA_REGISTRY, SKILL_HANDLERS, SkillHandler

logger = logging.getLogger("riskagent.skill_docs")

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = BACKEND_ROOT / "skills"

ExecutionMode = Literal["callable", "prompt_only", "offline"]
FailurePolicy = Literal["required", "partial", "optional"]

REQUIRED_SKILL_IDS = frozenset(
    {
        "pdf-parse-and-trace",
        "document-retrieval",
        "risk-rule-screening",
        "cashflow-analyzer",
        "valuation-checker",
        "sentiment-scorer",
        "legal-compliance-due-diligence",
        "expert-risk-analysis",
        "evidence-verification",
        "report-generation-and-archive",
        "orchestration",
    }
)

KNOWN_OWNER_FUNCTIONS = frozenset(
    {
        "orchestrator",
        "financial_agent",
        "legal_agent",
        "market_agent",
        "evidence",
        "quality",
        "redteam",
        "offline",
    }
)

AGENT_FUNCTIONS: dict[str, str] = {
    "shen_beichen_orchestrator_agent": "orchestrator",
    "qin_bingwen_financial_agent": "financial_agent",
    "financial_agent": "financial_agent",
    "wen_shouzheng_legal_agent": "legal_agent",
    "legal_agent": "legal_agent",
    "lu_guanlan_market_agent": "market_agent",
    "jiang_hengzhi_market_agent": "market_agent",
    "gu_lingsheng_market_agent": "market_agent",
    "bai_shice_market_agent": "market_agent",
    "market_agent": "market_agent",
    "cheng_zheng_evidence_agent": "evidence",
    "xiao_zeming_quality_agent": "quality",
    "zhou_shenyan_redteam_agent": "redteam",
}

AGENT_SKILL_MAP: dict[str, tuple[str, ...]] = {
    "shen_beichen_orchestrator_agent": (
        "orchestration",
        "risk-rule-screening",
        "report-generation-and-archive",
    ),
    "qin_bingwen_financial_agent": (
        "cashflow-analyzer",
        "valuation-checker",
        "expert-risk-analysis",
        "evidence-verification",
    ),
    "wen_shouzheng_legal_agent": (
        "legal-compliance-due-diligence",
        "expert-risk-analysis",
        "evidence-verification",
    ),
    "lu_guanlan_market_agent": (
        "sentiment-scorer",
        "expert-risk-analysis",
        "evidence-verification",
    ),
    "cheng_zheng_evidence_agent": (
        "pdf-parse-and-trace",
        "document-retrieval",
        "evidence-verification",
    ),
    "xiao_zeming_quality_agent": ("expert-risk-analysis", "evidence-verification"),
    "zhou_shenyan_redteam_agent": ("expert-risk-analysis", "evidence-verification"),
}
DEFAULT_EXPERT_SKILLS = ("expert-risk-analysis", "evidence-verification")
FUNCTION_SKILLS: dict[str, tuple[str, ...]] = {
    "orchestrator": AGENT_SKILL_MAP["shen_beichen_orchestrator_agent"],
    "financial_agent": AGENT_SKILL_MAP["qin_bingwen_financial_agent"],
    "legal_agent": AGENT_SKILL_MAP["wen_shouzheng_legal_agent"],
    "market_agent": AGENT_SKILL_MAP["lu_guanlan_market_agent"],
    "evidence": AGENT_SKILL_MAP["cheng_zheng_evidence_agent"],
    "quality": AGENT_SKILL_MAP["xiao_zeming_quality_agent"],
    "redteam": AGENT_SKILL_MAP["zhou_shenyan_redteam_agent"],
}


@dataclass(frozen=True)
class SkillDoc:
    name: str
    version: str
    description: str
    owners: tuple[str, ...]
    execution: ExecutionMode
    input_schema: str | None
    output_schema: str | None
    failure_policy: FailurePolicy
    body: str
    path: str
    document_hash: str


@dataclass(frozen=True)
class SkillDefinition:
    document: SkillDoc
    handler: SkillHandler | None
    available: bool

    @property
    def name(self) -> str:
        return self.document.name


@dataclass(frozen=True)
class SkillCatalogSnapshot:
    catalog_hash: str
    definitions: Mapping[str, SkillDefinition]
    source_mtime_ns: int

    def runtime_skills(self) -> tuple[SkillDefinition, ...]:
        return tuple(
            item for item in self.definitions.values() if item.document.execution != "offline"
        )

    def to_run_snapshot(self) -> dict[str, Any]:
        from riskagent.dataset_registry import dataset_snapshot

        return {
            "catalog_hash": self.catalog_hash,
            "skills": {
                name: {
                    "version": definition.document.version,
                    "document_hash": definition.document.document_hash,
                    "owners": list(definition.document.owners),
                    "execution": definition.document.execution,
                    "input_schema": definition.document.input_schema,
                    "output_schema": definition.document.output_schema,
                    "failure_policy": definition.document.failure_policy,
                    "description": definition.document.description,
                    "body": definition.document.body,
                    "available": definition.available,
                }
                for name, definition in self.definitions.items()
            },
            "agent_bindings": {
                agent_name: list(skill_ids_for_agent(agent_name))
                for agent_name in AGENT_FUNCTIONS
            },
            "dataset_versions": dataset_snapshot(),
        }


_CATALOG_LOCK = threading.RLock()
_CATALOG: SkillCatalogSnapshot | None = None
_WATCHER_STARTED = False
_HOT_RELOAD_DISABLED_LOGGED = False


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str] | None:
    if not text.startswith("---"):
        return None
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)(.*)$", text, re.DOTALL)
    if match is None:
        return None
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return None
    return (meta, match.group(2).strip()) if isinstance(meta, dict) else None


def _skill_doc(path: Path, skills_root: Path) -> SkillDoc:
    text = path.read_text(encoding="utf-8")
    parsed = _parse_frontmatter(text)
    if parsed is None:
        raise ValueError("invalid YAML frontmatter")
    meta, body = parsed
    required = {"name", "version", "description", "owners", "execution", "failure_policy"}
    missing = sorted(required - set(meta))
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")
    name = str(meta["name"]).strip()
    version = str(meta["version"]).strip()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise ValueError("name must be a lowercase kebab-case identifier")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise ValueError("version must use semantic versioning")
    owners_value = meta["owners"]
    if not isinstance(owners_value, list) or not owners_value:
        raise ValueError("owners must be a non-empty list")
    owners = tuple(str(item).strip() for item in owners_value)
    unknown_owners = sorted(set(owners) - KNOWN_OWNER_FUNCTIONS)
    if unknown_owners:
        raise ValueError(f"unknown owners: {', '.join(unknown_owners)}")
    execution = str(meta["execution"])
    if execution not in {"callable", "prompt_only", "offline"}:
        raise ValueError(f"invalid execution: {execution}")
    failure_policy = str(meta["failure_policy"])
    if failure_policy not in {"required", "partial", "optional"}:
        raise ValueError(f"invalid failure_policy: {failure_policy}")
    input_schema = str(meta.get("input_schema") or "") or None
    output_schema = str(meta.get("output_schema") or "") or None
    if execution == "callable":
        if not input_schema or input_schema not in SCHEMA_REGISTRY:
            raise ValueError(f"unknown input_schema: {input_schema}")
        if not output_schema or output_schema not in SCHEMA_REGISTRY:
            raise ValueError(f"unknown output_schema: {output_schema}")
    document = SkillDoc(
        name=name,
        version=version,
        description=str(meta["description"]).strip(),
        owners=owners,
        execution=execution,  # type: ignore[arg-type]
        input_schema=input_schema,
        output_schema=output_schema,
        failure_policy=failure_policy,  # type: ignore[arg-type]
        body=body,
        path=str(path.relative_to(skills_root.parent)),
        document_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    logger.info(
        "skill_catalog event=document_parsed skill_id=%s version=%s execution=%s "
        "owners=%s input_schema=%s output_schema=%s failure_policy=%s document_hash=%s",
        document.name,
        document.version,
        document.execution,
        ",".join(document.owners),
        document.input_schema or "-",
        document.output_schema or "-",
        document.failure_policy,
        document.document_hash[:12],
    )
    return document


def load_skill_docs(root: Path | None = None, *, strict: bool = False) -> list[SkillDoc]:
    """Load one directory level of Skill documents in deterministic order."""

    skills_root = root or SKILLS_ROOT
    logger.info("skill_catalog event=scan_started root=%s strict=%s", skills_root, strict)
    if not skills_root.is_dir():
        if strict:
            raise ValueError(f"Skill root does not exist: {skills_root}")
        return []
    docs: list[SkillDoc] = []
    errors: list[str] = []
    paths = sorted(skills_root.glob("*/SKILL.md"), key=lambda item: item.parent.name)
    logger.info("skill_catalog event=scan_discovered root=%s document_count=%d", skills_root, len(paths))
    for path in paths:
        try:
            docs.append(_skill_doc(path, skills_root))
        except (OSError, ValueError) as exc:
            message = f"{path}: {exc}"
            errors.append(message)
            logger.warning("skill_catalog event=document_rejected reason=%s", message)
    if strict and errors:
        logger.error(
            "skill_catalog event=scan_failed root=%s rejected_count=%d", skills_root, len(errors)
        )
        raise ValueError("Invalid Skill documents:\n" + "\n".join(errors))
    logger.info(
        "skill_catalog event=scan_completed root=%s accepted_count=%d rejected_count=%d",
        skills_root,
        len(docs),
        len(errors),
    )
    return docs


def _max_mtime(root: Path) -> int:
    values = [path.stat().st_mtime_ns for path in root.glob("*/SKILL.md")]
    return max(values, default=0)


def build_skill_catalog(root: Path | None = None) -> SkillCatalogSnapshot:
    skills_root = root or SKILLS_ROOT
    logger.info(
        "skill_catalog event=build_started root=%s handler_ids=%s schema_count=%d",
        skills_root,
        ",".join(sorted(SKILL_HANDLERS)),
        len(SCHEMA_REGISTRY),
    )
    docs = load_skill_docs(skills_root, strict=True)
    by_name: dict[str, SkillDefinition] = {}
    for doc in docs:
        if doc.name in by_name:
            logger.error("skill_catalog event=definition_rejected skill_id=%s reason=duplicate_id", doc.name)
            raise ValueError(f"Duplicate Skill id: {doc.name}")
        handler = SKILL_HANDLERS.get(doc.name)
        if doc.execution == "callable" and handler is None:
            logger.error(
                "skill_catalog event=definition_rejected skill_id=%s reason=missing_handler",
                doc.name,
            )
            raise ValueError(f"Callable Skill has no registered handler: {doc.name}")
        if doc.execution != "callable" and handler is not None:
            logger.error(
                "skill_catalog event=definition_rejected skill_id=%s reason=unexpected_handler",
                doc.name,
            )
            raise ValueError(f"Non-callable Skill must not register a handler: {doc.name}")
        by_name[doc.name] = SkillDefinition(doc, handler, handler is not None or doc.execution == "prompt_only")
        logger.info(
            "skill_catalog event=definition_registered skill_id=%s execution=%s handler_registered=%s available=%s",
            doc.name,
            doc.execution,
            handler is not None,
            by_name[doc.name].available,
        )
    missing = sorted(REQUIRED_SKILL_IDS - set(by_name))
    if missing:
        logger.error("skill_catalog event=required_skills_missing skill_ids=%s", ",".join(missing))
        raise ValueError(f"Missing required Skills: {', '.join(missing)}")
    for agent_name, skill_ids in AGENT_SKILL_MAP.items():
        unknown = sorted(set(skill_ids) - set(by_name))
        if unknown:
            logger.error(
                "skill_catalog event=agent_binding_rejected agent_name=%s skill_ids=%s",
                agent_name,
                ",".join(unknown),
            )
            raise ValueError(f"Agent {agent_name} references unknown Skills: {', '.join(unknown)}")
    logger.info(
        "skill_catalog event=validation_completed skill_count=%d required_skill_count=%d agent_binding_count=%d",
        len(by_name),
        len(REQUIRED_SKILL_IDS),
        len(AGENT_SKILL_MAP),
    )
    digest_payload = [
        [doc.name, doc.version, doc.document_hash] for doc in sorted(docs, key=lambda item: item.name)
    ]
    catalog_hash = hashlib.sha256(
        json.dumps(digest_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    snapshot = SkillCatalogSnapshot(catalog_hash, MappingProxyType(by_name), _max_mtime(skills_root))
    logger.info(
        "skill_catalog event=build_completed catalog_hash=%s skill_count=%d source_mtime_ns=%d",
        snapshot.catalog_hash,
        len(snapshot.definitions),
        snapshot.source_mtime_ns,
    )
    return snapshot


def initialize_skill_catalog(*, force: bool = False) -> SkillCatalogSnapshot:
    global _CATALOG
    with _CATALOG_LOCK:
        if _CATALOG is None or force:
            logger.info("skill_catalog event=initialization_started force=%s", force)
            _CATALOG = build_skill_catalog()
            logger.info(
                "skill_catalog event=initialization_published catalog_hash=%s",
                _CATALOG.catalog_hash,
            )
        else:
            logger.debug(
                "skill_catalog event=initialization_reused catalog_hash=%s", _CATALOG.catalog_hash
            )
        catalog = _CATALOG
    _ensure_development_watcher()
    return catalog


def get_skill_catalog() -> SkillCatalogSnapshot:
    return initialize_skill_catalog()


def _ensure_development_watcher() -> None:
    global _HOT_RELOAD_DISABLED_LOGGED, _WATCHER_STARTED
    if os.getenv("PROSPECTALPHA_ENV", "development").lower() != "development":
        with _CATALOG_LOCK:
            if not _HOT_RELOAD_DISABLED_LOGGED:
                logger.info("skill_catalog event=hot_reload_disabled mode=production")
                _HOT_RELOAD_DISABLED_LOGGED = True
        return
    with _CATALOG_LOCK:
        if _WATCHER_STARTED:
            return
        _WATCHER_STARTED = True
    logger.info("skill_catalog event=hot_reload_watcher_started root=%s interval_seconds=1", SKILLS_ROOT)
    threading.Thread(
        target=_watch_skill_documents,
        name="skill-document-watcher",
        daemon=True,
    ).start()


def _watch_skill_documents() -> None:
    global _CATALOG
    while True:
        threading.Event().wait(1.0)
        with _CATALOG_LOCK:
            catalog = _CATALOG
        if catalog is None or _max_mtime(SKILLS_ROOT) == catalog.source_mtime_ns:
            continue
        logger.info(
            "skill_catalog event=hot_reload_detected previous_catalog_hash=%s previous_mtime_ns=%d",
            catalog.catalog_hash,
            catalog.source_mtime_ns,
        )
        try:
            replacement = build_skill_catalog()
        except ValueError:
            logger.exception(
                "skill_catalog event=hot_reload_rejected retained_catalog_hash=%s", catalog.catalog_hash
            )
            continue
        with _CATALOG_LOCK:
            _CATALOG = replacement
        logger.info(
            "skill_catalog event=hot_reload_published previous_catalog_hash=%s catalog_hash=%s",
            catalog.catalog_hash,
            replacement.catalog_hash,
        )


def skills_digest(*, max_chars: int = 4000, root: Path | None = None) -> str:
    definitions = (
        build_skill_catalog(root).runtime_skills() if root is not None else get_skill_catalog().runtime_skills()
    )
    lines = [
        f"- {item.name}@{item.document.version}：{item.document.description}"
        f"（owners={','.join(item.document.owners)}；available={str(item.available).lower()}）"
        for item in definitions
    ]
    return "\n".join(lines)[:max_chars]


def _snapshot_docs(snapshot: dict[str, Any]) -> dict[str, SkillDoc]:
    result: dict[str, SkillDoc] = {}
    for name, raw in (snapshot.get("skills") or {}).items():
        result[name] = SkillDoc(
            name=name,
            version=str(raw["version"]),
            description=str(raw.get("description") or ""),
            owners=tuple(raw.get("owners") or []),
            execution=raw.get("execution", "prompt_only"),
            input_schema=None,
            output_schema=None,
            failure_policy="partial",
            body=str(raw.get("body") or ""),
            path="",
            document_hash=str(raw.get("document_hash") or ""),
        )
    return result


def skills_for_agent(
    agent_name: str,
    *,
    assigned_skill_ids: list[str] | tuple[str, ...] | None = None,
    snapshot: dict[str, Any] | None = None,
    root: Path | None = None,
) -> list[SkillDoc]:
    if snapshot is not None:
        by_name = _snapshot_docs(snapshot)
    elif root is not None:
        by_name = {doc.name: doc for doc in load_skill_docs(root)}
    else:
        by_name = {name: item.document for name, item in get_skill_catalog().definitions.items()}
    wanted = list(assigned_skill_ids or skill_ids_for_agent(agent_name))
    if AGENT_FUNCTIONS.get(agent_name) not in {"orchestrator", "evidence"}:
        wanted.extend(DEFAULT_EXPERT_SKILLS)
    return [by_name[name] for name in dict.fromkeys(wanted) if name in by_name and by_name[name].execution != "offline"]


def skill_ids_for_agent(agent_name: str) -> tuple[str, ...]:
    return AGENT_SKILL_MAP.get(
        agent_name,
        FUNCTION_SKILLS.get(AGENT_FUNCTIONS.get(agent_name, ""), DEFAULT_EXPERT_SKILLS),
    )


def render_skills_section(
    agent_name: str,
    *,
    assigned_skill_ids: list[str] | None = None,
    snapshot: dict[str, Any] | None = None,
    root: Path | None = None,
) -> str:
    docs = skills_for_agent(
        agent_name,
        assigned_skill_ids=assigned_skill_ids,
        snapshot=snapshot,
        root=root,
    )
    if not docs:
        return ""
    return "\n\n".join(
        f"【技能：{doc.name}@{doc.version}】\n{doc.body}" for doc in docs
    )
