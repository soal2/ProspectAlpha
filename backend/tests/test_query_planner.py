from riskagent.retrieval.query_planner import (
    build_evidence_gap_queries,
    build_module_search_queries,
    build_rule_search_queries,
    prioritize_rules_for_context,
    rerank_rule_candidates,
    rule_anchor_terms,
)
from riskagent.risk_rules import RiskRule


def _rule() -> RiskRule:
    return RiskRule(
        rule_id="HKIPO-TEST",
        dimension_code="D1",
        dimension="财务",
        rule_type="quantitative_financial",
        category="持续经营",
        risk_item="持续经营重大不确定性",
        description="现金不足可能影响持续经营",
        trigger_condition="经营现金流连续2期为负且未来十二个月现金不足",
        calculation_or_method="核对现金流量表",
        source_sections="审计报告 / 财务资料 / 风险因素",
    )


def test_rule_query_rewrite_is_specific_and_requests_counter_evidence():
    queries = build_rule_search_queries(_rule(), user_requirement="全面审核 IPO 风险")

    assert len(queries) >= 3
    joined = " ".join(queries)
    assert "持续经营" in joined
    assert "持續經營" in joined
    assert "going concern" in joined
    assert any("报告期" in query for query in queries)
    assert any("反向证据" in query for query in queries)


def test_generic_terms_are_not_rule_anchors():
    anchors = rule_anchor_terms(_rule())

    assert "重大" not in anchors
    assert "持续经营" in anchors


def test_reranker_rejects_high_score_irrelevant_chunk_and_keeps_section_match():
    candidates = [
        {
            "block_id": "irrelevant",
            "page_number": 1,
            "section_path": "封面",
            "text": "本次交易构成重大收购。",
            "score": 0.9,
        },
        {
            "block_id": "relevant",
            "page_number": 108,
            "section_path": "审计报告",
            "text": "经营活动现金流连续两个报告期为负，现有现金预计覆盖十二个月。",
            "score": 0.31,
        },
    ]

    ranked = rerank_rule_candidates(candidates, rule=_rule())

    assert [item["block_id"] for item in ranked] == ["relevant"]
    assert ranked[0]["retrieval_score"] == 0.31
    assert ranked[0]["relevance_score"] > ranked[0]["retrieval_score"]


def test_customer_word_alone_is_not_customer_concentration_evidence():
    rule = RiskRule(
        rule_id="HKIPO-CUSTOMER",
        dimension_code="D2",
        dimension="业务",
        rule_type="qualitative_disclosure",
        category="客户/供应商依赖",
        risk_item="前五大客户集中度过高",
        description="客户集中风险",
        trigger_condition="前五大客户收入占比 > 50% 或 单一客户 > 30%",
        calculation_or_method="前五大客户收入/总收入",
        source_sections="业务章节 / 主要客户",
    )

    ranked = rerank_rule_candidates(
        [
            {
                "block_id": "share-option-plan",
                "page_number": 678,
                "section_path": "附录",
                "text": "本集团的客户、顾问及业务合作伙伴可参与购股权计划，营业收入另见财务资料。",
                "score": 0.9,
            }
        ],
        rule=rule,
    )

    assert ranked == []


def test_module_queries_preserve_specific_user_and_assignment_intent():
    queries = build_module_search_queries(
        module="市场与估值风险",
        goal="分析发行价区间 32.20-34.20 港元的合理性和破发风险",
        expected_outputs=["RiskFinding: 发行定价风险", "股票代号"],
        rules=[_rule()],
        user_requirement="股票代号是什么，并分析发行定价风险",
    )

    joined = " ".join(queries)
    assert "股份代號" in joined
    assert "發售價" in joined
    assert any(len(query) < 40 for query in queries)


def test_gap_queries_are_short_and_include_counter_evidence_terms():
    queries = build_evidence_gap_queries(
        _rule(),
        missing_reason="缺少现金余额和管理层改善说明",
        user_requirement="重点分析现金流",
        module_goal="核查持续经营能力",
    )

    assert any("持續經營" in query for query in queries)
    assert any("改善措施" in query for query in queries)
    assert all(len(query) < 80 for query in queries)


def test_business_assignment_generates_pipeline_specific_queries():
    queries = build_module_search_queries(
        module="业务风险分析",
        goal="分析研发管线依赖和候选药物商业化风险",
        expected_outputs=["RiskFinding: 研发管线依赖风险"],
        rules=[_rule()],
        user_requirement="重点检查候选药物是否均未获批",
    )

    joined = " ".join(queries)
    assert "候選藥物" in joined
    assert "監管批准" in joined or "未取得監管批准" in joined


def test_assignment_context_prioritizes_matching_rules():
    pipeline_rule = _rule().model_copy(
        update={
            "rule_id": "PIPELINE",
            "category": "持续经营",
            "risk_item": "核心产品/管线推进不及预期",
        }
    )
    unrelated_rule = _rule().model_copy(
        update={
            "rule_id": "CUSTOMER",
            "category": "客户/供应商依赖",
            "risk_item": "前五大客户集中度过高",
        }
    )

    ordered = prioritize_rules_for_context(
        [unrelated_rule, pipeline_rule],
        context="核查研发管线依赖风险",
    )

    assert ordered[0].rule_id == "PIPELINE"


def test_patent_expiry_rewrite_and_reranker_accept_traditional_disclosure():
    rule = RiskRule(
        rule_id="HKIPO-088",
        dimension_code="D2",
        dimension="非标隐性风险",
        rule_type="qualitative_disclosure",
        category="核心技术/知识产权",
        risk_item="核心技术专利即将到期",
        description="核心专利保护期即将届满",
        trigger_condition="核心专利剩余保护期 < 3年",
        calculation_or_method="审查核心专利的申请日和有效期",
        source_sections="知识产权 / 风险因素",
    )
    queries = build_rule_search_queries(rule, user_requirement="重点核查专利到期风险")
    candidates = [
        {
            "block_id": "patent-expiry-table",
            "page_number": 302,
            "section_path": "業務 / 知識產權",
            "text": "AT-101的相關專利預計於2025年到期，APG-115的相關專利預計於2032年至2035年到期。",
            "score": 0.406,
        },
        {
            "block_id": "unrelated-finance",
            "page_number": 590,
            "section_path": "財務資料",
            "text": "本集團的現金及現金等價物以及融資活動載於本附註。",
            "score": 0.91,
        },
    ]

    ranked = rerank_rule_candidates(candidates, rule=rule)

    assert "專利 預計到期" in queries
    assert queries[0] == "專利 預計到期"
    assert "重点核查专利到期风险" not in queries[0]
    assert [item["block_id"] for item in ranked] == ["patent-expiry-table"]
    assert {"專利", "到期"}.issubset(set(ranked[0]["matched_anchors"]))


def test_litigation_reranker_preserves_explicit_counter_evidence():
    rule = RiskRule(
        rule_id="HKIPO-072",
        dimension_code="D2",
        dimension="非标隐性风险",
        rule_type="qualitative_disclosure",
        category="诉讼与纠纷",
        risk_item="重大未决诉讼",
        description="公司为被告的未决诉讼金额较大",
        trigger_condition="未决诉讼涉案金额 / 归母净资产 > 10% 或 案件性质严重影响持续经营",
        calculation_or_method="核对诉讼清单和风险敞口",
        source_sections="法律诉讼 / 风险因素 / 或有事项",
    )
    candidates = [
        {
            "block_id": "no-material-litigation",
            "page_number": 333,
            "section_path": "業務 / 法律訴訟",
            "text": "於往績記錄期及直至最後實際可行日期，本集團並無面臨任何法律訴訟，亦無任何未決或威脅的仲裁或行政訴訟可能造成重大不利影響。",
            "score": 0.453,
        }
    ]

    ranked = rerank_rule_candidates(candidates, rule=rule)

    assert [item["block_id"] for item in ranked] == ["no-material-litigation"]
    assert ranked[0]["counter_evidence"] is True
