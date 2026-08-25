import asyncio
from dataclasses import dataclass

from riskagent.hidden_risk import (
    DisclosureRiskAnalyzer,
    IndustryProfile,
    RiskHypothesis,
    hypotheses_from_text_signals,
    infer_industry_profile,
    infer_industry_profile_with_ai,
)


@dataclass
class Block:
    block_id: str
    page_number: int
    section_path: str
    text: str


def _blocks() -> list[Block]:
    return [
        Block(
            "block_business",
            10,
            "业务",
            "公司拥有巨大潜力并预期高速增长，相信平台将保持领先。"
            "相关结果取决于市场情况，本公司无法保证一定实现。" * 3,
        ),
        Block(
            "block_risk",
            35,
            "风险因素",
            "生物制药公司的药物仍在临床阶段，多项研发候选管线存在研发失败、持续亏损及重大不利变化风险。收入为0港元。",
        ),
    ]


def test_disclosure_signals_keep_prospectus_anchors_and_no_fake_peer_benchmark():
    blocks = _blocks()
    industry = infer_industry_profile(blocks)
    snapshot = DisclosureRiskAnalyzer().analyze(blocks, industry)

    assert industry.code == "BIOPHARMA"
    assert industry.evidence_ids
    assert industry.evidence_pages == [35]
    assert snapshot.benchmark_note.startswith("同业冻结快照少于 3 份")
    vague = next(item for item in snapshot.signals if item.kind == "vagueness_density")
    assert vague.value > 0
    assert vague.evidence_ids == ["block_business"]
    assert all(item.benchmark_status == "unavailable" for item in snapshot.signals)


def test_free_risk_type_and_low_confidence_are_valid_with_an_evidence_anchor():
    hypothesis = RiskHypothesis(
        risk_type="未经规则登记的叙事错配",
        title="增长叙事与临床进度错配",
        statement="增长叙事强于可验证的临床进展。",
        origin="agent_emergent",
        dimension="D3",
        causal_chain="叙事强化导致预期抬升，临床节点延误时估值可能快速修正。",
        industry_relevance="生物医药估值高度依赖临床里程碑。",
        company_specificity="该公司尚无产品销售收入。",
        prospectus_evidence_ids=["block_risk"],
        evidence_grade="lead",
        confidence=0.08,
    )

    assert hypothesis.risk_type == "未经规则登记的叙事错配"
    assert hypothesis.confidence == 0.08


def test_text_signal_hypotheses_are_not_rule_id_bound():
    blocks = _blocks()
    profile = IndustryProfile(
        code="BIOPHARMA",
        name="生物医药",
        rationale="招股书显示临床研发业务。",
        evidence_ids=["block_risk"],
    )
    snapshot = DisclosureRiskAnalyzer().analyze(blocks, profile)
    hypotheses = hypotheses_from_text_signals(snapshot, profile, limit=4)

    assert hypotheses
    assert all(item.origin == "text_signal" for item in hypotheses)
    assert all(item.prospectus_evidence_ids for item in hypotheses)


def test_ai_industry_profile_rejects_unowned_anchors_and_keeps_pages():
    class Client:
        async def chat_json(self, *_args, **_kwargs):
            return {
                "code": "PRE_REVENUE_BIOPHARMA",
                "name": "临床阶段生物医药",
                "rationale": "主营业务依赖临床研发管线。",
                "evidence_ids": ["block_risk", "invented"],
            }

    profile = asyncio.run(
        infer_industry_profile_with_ai(_blocks(), client_factory=Client)
    )

    assert profile.source == "prospectus_ai"
    assert profile.evidence_ids == ["block_risk"]
    assert profile.evidence_pages == [35]
