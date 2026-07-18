"""Planner tests: nine-dimension coverage, language dedup, focus bump."""

from __future__ import annotations

import pytest

from riskagent.research.models import BASELINE_DIMENSIONS
from riskagent.research.planner import (
    AssignmentHint,
    apply_user_focus,
    build_baseline_plan,
    canonical_form,
    expand_queries,
)


class TestCanonicalForm:
    def test_trad_simp_collapse(self):
        assert canonical_form("騰訊") == canonical_form("腾讯")
        assert canonical_form("監管處罰") == canonical_form("监管处罚")

    def test_whitespace_and_punctuation_stripped(self):
        assert canonical_form("  Tencent / 腾讯 ") == canonical_form("tencent腾讯")
        assert canonical_form("a-b_c") == canonical_form("abc")

    def test_empty(self):
        assert canonical_form("") == ""
        assert canonical_form("   ") == ""


class TestExpandQueries:
    def test_script_equivalents_collapse_to_one_variant(self):
        # 腾讯 (zh-Hans) and 騰訊 (zh-Hant) share canonical_form — only one
        # variant is emitted.  The Bocha backend treats both forms as the
        # same query; emitting duplicates would inflate coverage.
        hans = expand_queries("腾讯 监管")
        hant = expand_queries("騰訊 監管")
        assert len(hans) == 1
        assert len(hant) == 1
        assert hans[0].canonical_form == hant[0].canonical_form

    def test_mechanical_derivation_suppressed_when_canonical_collapses(self):
        # The folding-based derivation is intentional no-op when both forms
        # would dedupe — that is the explicit D9 design choice in the plan.
        out = expand_queries("腾讯 监管")
        assert len(out) == 1
        assert out[0].language in {"zh-Hans", "zh-Hant"}

    def test_pure_english_emits_one_en_variant(self):
        out = expand_queries("Tencent earnings")
        assert len(out) == 1
        assert out[0].language == "en"

    def test_composite_zh_en_emits_both_variants(self):
        out = expand_queries("监管处罚 / regulatory action")
        languages = {q.language for q in out}
        assert languages == {"zh-Hans", "en"}

    def test_dedup_by_canonical_form_across_inputs(self):
        # Same query repeated as separate bases collapses to one
        out = expand_queries("腾讯", "腾讯")
        assert len(out) == 1

    def test_canonical_form_populated(self):
        out = expand_queries("腾讯")
        for q in out:
            assert q.canonical_form
            assert q.canonical_form == canonical_form(q.text)


class TestBuildBaselinePlan:
    def test_quick_returns_empty(self):
        tasks = build_baseline_plan(
            company_name="某公司", analysis_depth="quick"
        )
        assert tasks == []

    def test_standard_covers_all_nine_dimensions(self):
        tasks = build_baseline_plan(
            company_name="某公司", analysis_depth="standard"
        )
        assert len(tasks) == len(BASELINE_DIMENSIONS)
        covered = {t.dimension for t in tasks}
        assert covered == set(BASELINE_DIMENSIONS)

    def test_deep_also_covers_all_nine(self):
        tasks = build_baseline_plan(
            company_name="某公司", analysis_depth="deep"
        )
        assert {t.dimension for t in tasks} == set(BASELINE_DIMENSIONS)

    def test_deep_has_more_queries_than_standard(self):
        std = build_baseline_plan(company_name="某公司", analysis_depth="standard")
        deep = build_baseline_plan(company_name="某公司", analysis_depth="deep")
        # Deep uses 3 templates per dimension vs 2 for standard
        assert all(len(t.queries) >= 2 for t in std)
        assert any(
            len(d.queries) > len(s.queries)
            for d, s in zip(
                sorted(deep, key=lambda t: t.dimension),
                sorted(std, key=lambda t: t.dimension),
            )
        )

    def test_priority_high_dimensions(self):
        tasks = build_baseline_plan(company_name="某公司", analysis_depth="standard")
        by_dim = {t.dimension: t for t in tasks}
        assert by_dim["财务"].priority == "high"
        assert by_dim["治理"].priority == "high"
        assert by_dim["法务监管"].priority == "high"
        assert by_dim["声誉"].priority == "medium"

    def test_no_company_returns_empty(self):
        assert build_baseline_plan(company_name="", analysis_depth="standard") == []
        assert build_baseline_plan(company_name="   ", analysis_depth="deep") == []

    def test_module_id_mapped_when_assignment_matches(self):
        assignments = [
            AssignmentHint(module_id="mod_a", module="法务监管", goal="..."),
            AssignmentHint(module_id="mod_b", module="财务与会计", goal="..."),
        ]
        tasks = build_baseline_plan(
            company_name="某公司",
            assignments=assignments,
            analysis_depth="standard",
        )
        by_dim = {t.dimension: t for t in tasks}
        assert by_dim["法务监管"].module_id == "mod_a"
        assert by_dim["财务"].module_id == "mod_b"

    def test_company_substituted_into_queries(self):
        tasks = build_baseline_plan(company_name="某公司", analysis_depth="standard")
        all_texts = " ".join(q.text for t in tasks for q in t.queries)
        assert "某公司" in all_texts


class TestApplyUserFocus:
    def test_focus_bumps_priority_not_drops(self):
        tasks = build_baseline_plan(company_name="某公司", analysis_depth="standard")
        original_count = len(tasks)
        # All tasks should still be present after focus
        tasks = apply_user_focus(tasks, "声誉")
        assert len(tasks) == original_count
        by_dim = {t.dimension: t for t in tasks}
        assert by_dim["声誉"].priority == "high"
        # Others unchanged
        assert by_dim["估值"].priority == "medium"

    def test_empty_focus_is_noop(self):
        tasks = build_baseline_plan(company_name="某公司", analysis_depth="standard")
        before = [t.priority for t in tasks]
        apply_user_focus(tasks, "")
        after = [t.priority for t in tasks]
        assert before == after

    def test_focus_does_not_remove_dimensions(self):
        tasks = build_baseline_plan(company_name="某公司", analysis_depth="standard")
        before_dims = {t.dimension for t in tasks}
        apply_user_focus(tasks, "只看财务")
        after_dims = {t.dimension for t in tasks}
        assert before_dims == after_dims
