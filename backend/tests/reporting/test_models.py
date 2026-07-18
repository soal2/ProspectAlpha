"""Smoke tests for reporting/models.py contract invariants.

The behavioral guarantees (frozen immutability, subset assertions,
selection derivation) are exercised by the per-feature tests; this file
covers only construction, defaults and the immutability hook.
"""

from __future__ import annotations

import pytest

from riskagent.reporting.errors import (
    BUILD_CODES,
    DegradationCode,
    ReportBuildError,
    TEMPLATE_LOAD_CODES,
    TemplateLoadError,
)
from riskagent.reporting.models import (
    ActorRef,
    DegradationNote,
    ExclusionRecord,
    FrozenBundleError,
    IndustryProfile,
    ModuleReport,
    ReportAnnotation,
    ReportBundleV2,
    ResearchTask,
    SelectionSummary,
    TemplateRef,
)


def _template_ref(template_id: str = "brief_core", version: int = 1) -> TemplateRef:
    return TemplateRef(
        template_id=template_id,
        version=version,
        content_sha256="0" * 64,
    )


def _module(kind: str, template: TemplateRef) -> ModuleReport:
    return ModuleReport(
        module_kind=kind,  # type: ignore[arg-type]
        template=template,
        markdown=f"# {kind}\n",
        hypothesis_ids_visible=["hyp_a"] if kind == "decision_brief" else ["hyp_a", "hyp_b"],
    )


def _selection() -> SelectionSummary:
    return SelectionSummary(
        verified_hypothesis_ids=["hyp_a"],
        material_unresolved_hypothesis_ids=["hyp_b"],
        exclusions=[
            ExclusionRecord(
                target_kind="hypothesis",
                target_id="hyp_c",
                reason_code="request_supplement_verdict",
            )
        ],
    )


def _bundle() -> ReportBundleV2:
    return ReportBundleV2(
        run_id="run_abc",
        report_version=1,
        research_artifact_id="art_1",
        research_artifact_version=3,
        verdict_snapshot_hash="abc123",
        brief_template=_template_ref("brief_core"),
        deep_template=_template_ref("deep_core"),
        decision_brief=_module("decision_brief", _template_ref("brief_core")),
        deep_research=_module("deep_research", _template_ref("deep_core")),
        industry_profile=IndustryProfile(),
        selection=_selection(),
        review_state="auto_completed",
    )


def test_template_ref_requires_positive_version():
    with pytest.raises(ValueError):
        TemplateRef(template_id="x", version=0, content_sha256="0" * 64)


def test_actor_ref_default_is_anonymous_placeholder():
    actor = ActorRef()
    assert actor.actor_kind == "anonymous"
    assert actor.display() == "anonymous"
    named = ActorRef(actor_kind="named", actor_id="alice@example.com")
    assert named.display() == "alice@example.com"


def test_frozen_bundle_rejects_mutation():
    bundle = _bundle()
    bundle.frozen = True
    with pytest.raises(FrozenBundleError):
        bundle.review_state = "human_revised"


def test_unfrozen_bundle_allows_mutation():
    bundle = _bundle()
    bundle.review_state = "human_revised"
    assert bundle.review_state == "human_revised"


def test_selection_summary_formal_ids_combines_verified_and_material_unresolved():
    selection = _selection()
    assert selection.formal_hypothesis_ids == ["hyp_a", "hyp_b"]


def test_degradation_note_requires_known_code():
    note = DegradationNote(
        code=DegradationCode.D6_SECTION_EMPTY,
        message_zh="本轮未形成有效结论。",
        section_id="risks_verified",
    )
    assert note.code is DegradationCode.D6_SECTION_EMPTY


def test_research_task_defaults_to_pending():
    task = ResearchTask(
        run_id="run_abc",
        target_artifact_version=3,
        trigger_kind="human_request_supplement",
        query="补充检索：关联交易",
        idempotency_key="idem_1",
    )
    assert task.status == "pending"
    assert task.completed_at is None


def test_annotation_requires_idempotency_key():
    annotation = ReportAnnotation(
        run_id="run_abc",
        report_version=1,
        target_kind="hypothesis",
        target_id="hyp_a",
        body="质疑该结论",
        actor=ActorRef(),
        idempotency_key="idem_ann_1",
    )
    assert annotation.annotation_id.startswith("ann_")
    assert annotation.idempotency_key == "idem_ann_1"


def test_template_load_and_build_codes_are_disjoint():
    assert TEMPLATE_LOAD_CODES.isdisjoint(BUILD_CODES)
    assert len(TEMPLATE_LOAD_CODES) >= 12
    assert len(BUILD_CODES) >= 7


def test_template_load_error_subclasses_value_error():
    assert issubclass(TemplateLoadError, ValueError)
    assert issubclass(ReportBuildError, ValueError)


def test_bundle_visible_hypothesis_ids_per_module():
    bundle = _bundle()
    assert bundle.visible_hypothesis_ids("decision_brief") == {"hyp_a"}
    assert bundle.visible_hypothesis_ids("deep_research") == {"hyp_a", "hyp_b"}
