"""Hidden-risk discovery contracts and prospectus disclosure analysis."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from riskagent.providers.embedding import EmbeddingClient, HashingEmbeddingClient, cosine_similarity


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProspectusBlock(Protocol):
    block_id: str
    page_number: int
    section_path: str
    text: str


class IndustryProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    evidence_pages: list[int] = Field(default_factory=list)
    source: Literal["prospectus_ai", "deterministic_fallback"] = "prospectus_ai"
    user_confirmed: bool = False
    frozen: bool = False


class TextSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str
    kind: str
    value: float
    section_scope: list[str]
    evidence_ids: list[str]
    description: str
    benchmark: float | None = None
    benchmark_status: Literal["available", "unavailable"] = "unavailable"


class TextSignalSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "text-disclosure-v1"
    generated_at: str = Field(default_factory=_now)
    industry_code: str
    signals: list[TextSignal]
    benchmark_note: str


class RiskHypothesis(BaseModel):
    """Minimal stable envelope; risk_type intentionally remains free text."""

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str = Field(default_factory=lambda: f"hyp_{uuid.uuid4().hex[:12]}")
    risk_type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    origin: Literal["rule", "text_signal", "agent_emergent", "external"]
    dimension: Literal["D1", "D2", "D3", "EXT"]
    causal_chain: str = Field(min_length=1)
    industry_relevance: str = Field(min_length=1)
    company_specificity: str = Field(min_length=1)
    expected_market_effects: list[str] = Field(default_factory=list)
    prospectus_evidence_ids: list[str] = Field(min_length=1)
    external_evidence_ids: list[str] = Field(default_factory=list)
    counter_evidence_ids: list[str] = Field(default_factory=list)
    evidence_grade: Literal["direct", "inferential", "lead", "contradicted"] = "lead"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    status: Literal["reported", "corroborated", "disputed", "refuted"] = "reported"
    severity: Literal["low", "medium", "high"] = "medium"

    @field_validator("prospectus_evidence_ids")
    @classmethod
    def _unique_evidence(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not normalized:
            raise ValueError("A hypothesis requires at least one prospectus evidence anchor.")
        return normalized


class ResearchBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depth: Literal["quick", "standard", "deep"]
    hypothesis_limit: int
    search_limit: int

    @classmethod
    def for_depth(cls, depth: str) -> "ResearchBudget":
        values = {
            "quick": (4, 12),
            "standard": (12, 30),
            "deep": (24, 60),
        }
        hypothesis_limit, search_limit = values.get(depth, values["standard"])
        return cls(
            depth=depth if depth in values else "standard",
            hypothesis_limit=hypothesis_limit,
            search_limit=search_limit,
        )


_INDUSTRIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("BIOPHARMA", "生物医药", ("药物", "临床", "药品", "生物制药", "管线", "研发候选")),
    ("TECH", "科技与软件", ("软件", "平台", "算法", "云服务", "人工智能", "数据中心")),
    ("CONSUMER", "消费与零售", ("零售", "门店", "消费者", "品牌", "经销商", "餐饮")),
    ("INDUSTRIAL", "工业制造", ("制造", "生产线", "工业", "原材料", "产能", "工厂")),
    ("FINANCIAL", "金融服务", ("贷款", "保险", "证券", "资产管理", "利息收入", "金融服务")),
    ("PROPERTY", "房地产", ("物业开发", "房地产", "土地储备", "租金", "建筑面积", "交付")),
)


def infer_industry_profile(blocks: list[ProspectusBlock]) -> IndustryProfile:
    """Deterministic fallback used only when the industry model is unavailable."""

    scores: list[tuple[int, str, str, tuple[str, ...]]] = []
    for code, name, terms in _INDUSTRIES:
        score = sum(block.text.lower().count(term.lower()) for block in blocks for term in terms)
        scores.append((score, code, name, terms))
    score, code, name, terms = max(scores, default=(0, "OTHER", "其他行业", ()), key=lambda item: item[0])
    if score <= 0:
        code, name = "OTHER", "其他行业"
    evidence = sorted(
        blocks,
        key=lambda block: sum(block.text.lower().count(term.lower()) for term in terms),
        reverse=True,
    )
    evidence_ids = [
        block.block_id
        for block in evidence
        if block.text.strip()
        and (score <= 0 or any(term.lower() in block.text.lower() for term in terms))
    ][:3]
    if not evidence_ids:
        raise ValueError("Industry inference requires at least one non-empty prospectus block.")
    return IndustryProfile(
        code=code,
        name=name,
        rationale=(
            f"招股书业务描述中与{name}相关的经营、产品和行业术语出现 {score} 次；"
            "行业画像仅用于风险检索和同行分组，需由用户确认后冻结。"
        ),
        evidence_ids=evidence_ids,
        evidence_pages=[block.page_number for block in evidence if block.block_id in evidence_ids],
        source="deterministic_fallback",
    )


async def infer_industry_profile_with_ai(
    blocks: list[ProspectusBlock],
    *,
    client_factory: Callable[[], Any],
) -> IndustryProfile:
    """Infer a free industry profile while accepting only real block anchors."""

    usable = [block for block in blocks if block.text.strip()]
    if not usable:
        raise ValueError("Industry inference requires prospectus text.")
    allowed = {block.block_id for block in usable}
    payload = [
        {
            "block_id": block.block_id,
            "page_number": block.page_number,
            "section_path": block.section_path,
            "text": block.text[:1200],
        }
        for block in usable[:50]
    ]
    raw = await client_factory().chat_json(
        [
            {
                "role": "system",
                "content": (
                    "从招股书业务事实识别最能约束风险研究的主营行业。行业代码和名称可自由填写，"
                    "不得套用固定风险分类。必须解释判断并引用输入中真实存在的 block_id。"
                    "只输出 JSON：{\"code\":str,\"name\":str,\"rationale\":str,"
                    "\"evidence_ids\":[str]}"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.2,
    )
    if not isinstance(raw, dict):
        raise ValueError("Industry model returned a non-object response.")
    evidence_ids = [
        str(item)
        for item in raw.get("evidence_ids", [])
        if str(item) in allowed
    ]
    return IndustryProfile.model_validate(
        {
            "code": raw.get("code"),
            "name": raw.get("name"),
            "rationale": raw.get("rationale"),
            "evidence_ids": evidence_ids,
            "evidence_pages": [
                block.page_number for block in usable if block.block_id in evidence_ids
            ],
            "source": "prospectus_ai",
        }
    )


_VAGUE = ("可能", "若干", "适当", "通常", "视乎", "酌情", "预期", "相信", "力争", "有望")
_EVASIVE = ("不保证", "无法保证", "概不负责", "在可行范围内", "取决于", "并非由本公司控制")
_OPTIMISTIC = ("领先", "显著增长", "巨大潜力", "强劲", "卓越", "高速增长", "广阔前景")
_CAUTION = ("风险", "不确定", "下降", "亏损", "重大不利", "可能无法", "挑战")
_DISCLAIMER = ("前瞻性陈述", "不应依赖", "概不保证", "仅供说明", "可能与实际结果存在重大差异")
_SENTENCE_RE = re.compile(r"[^。！？!?；;]+[。！？!?；;]?")
_NUMBER_RE = re.compile(r"(?<![A-Za-z])(?:\d[\d,.]*%?|[一二三四五六七八九十百千万亿]+(?:元|港元|人民币))")


class DisclosureRiskAnalyzer:
    def __init__(self, embedding_client: EmbeddingClient | None = None) -> None:
        self.embedding_client = embedding_client or HashingEmbeddingClient()

    def analyze(
        self,
        blocks: list[ProspectusBlock],
        industry_profile: IndustryProfile,
        *,
        peer_snapshots: list[TextSignalSnapshot] | None = None,
    ) -> TextSignalSnapshot:
        usable = [block for block in blocks if block.text.strip()]
        if not usable:
            raise ValueError("Disclosure analysis requires prospectus text.")
        all_text = "\n".join(block.text for block in usable)
        chars = max(1, len(re.sub(r"\s+", "", all_text)))
        sentences = [item.strip() for item in _SENTENCE_RE.findall(all_text) if item.strip()]
        long_sentences = [item for item in sentences if len(item) >= 80]
        nested_sentences = [
            item
            for item in sentences
            if sum(item.count(mark) for mark in ("，", ",", "；", ";", "其中", "以及")) >= 3
        ]
        quantified_sentences = [item for item in sentences if _NUMBER_RE.search(item)]

        def marker_signal(kind: str, markers: tuple[str, ...], description: str) -> TextSignal:
            counts = {block.block_id: sum(block.text.count(marker) for marker in markers) for block in usable}
            total = sum(counts.values())
            evidence_ids = [key for key, value in sorted(counts.items(), key=lambda item: item[1], reverse=True) if value][:3]
            return self._signal(kind, round(total * 1000 / chars, 4), usable, evidence_ids, description)

        signals = [
            marker_signal("vagueness_density", _VAGUE, "模糊、对冲和弱承诺措辞每千字密度。"),
            marker_signal("responsibility_evasion_density", _EVASIVE, "责任回避和不可控归因措辞每千字密度。"),
            marker_signal("optimism_density", _OPTIMISTIC, "业务与展望中的乐观强化措辞每千字密度。"),
            marker_signal("caution_density", _CAUTION, "风险和不确定性措辞每千字密度。"),
            marker_signal("disclaimer_density", _DISCLAIMER, "免责声明及前瞻性陈述每千字密度。"),
            self._signal(
                "quantitative_disclosure_density",
                round(len(_NUMBER_RE.findall(all_text)) * 1000 / chars, 4),
                usable,
                [block.block_id for block in sorted(usable, key=lambda item: len(_NUMBER_RE.findall(item.text)), reverse=True) if _NUMBER_RE.search(block.text)][:3],
                "可核验数字、比例和金额披露每千字密度。",
            ),
            self._signal(
                "long_sentence_ratio",
                round(len(long_sentences) / max(1, len(sentences)), 4),
                usable,
                [block.block_id for block in sorted(usable, key=lambda item: max((len(s) for s in _SENTENCE_RE.findall(item.text)), default=0), reverse=True)][:3],
                "超过 80 字的长句占比。",
            ),
            self._signal(
                "nested_sentence_ratio",
                round(len(nested_sentences) / max(1, len(sentences)), 4),
                usable,
                [
                    block.block_id
                    for block in usable
                    if any(
                        sum(sentence.count(mark) for mark in ("，", ",", "；", ";", "其中", "以及")) >= 3
                        for sentence in _SENTENCE_RE.findall(block.text)
                    )
                ][:3],
                "包含多重并列、嵌套限定或从句的句子占比。",
            ),
            self._signal(
                "evidence_specificity",
                round(len(quantified_sentences) / max(1, len(sentences)), 4),
                usable,
                [block.block_id for block in usable if _NUMBER_RE.search(block.text)][:3],
                "包含可核验金额、比例或数量的句子占比。",
            ),
        ]
        optimism = next(item.value for item in signals if item.kind == "optimism_density")
        caution = next(item.value for item in signals if item.kind == "caution_density")
        signals.append(
            self._signal(
                "risk_coverage_asymmetry",
                round(max(0.0, optimism - caution), 4),
                usable,
                [block.block_id for block in usable if any(term in block.text for term in _OPTIMISTIC)][:3],
                "乐观强化披露相对风险披露的密度差。",
            )
        )
        signals.append(self._semantic_consistency(usable))
        signals.append(self._section_sentiment_asymmetry(usable))
        signals.append(self._template_repetition(usable))

        peers = peer_snapshots or []
        if len(peers) >= 3:
            by_kind: dict[str, list[float]] = {}
            for peer in peers:
                for signal in peer.signals:
                    by_kind.setdefault(signal.kind, []).append(signal.value)
            for signal in signals:
                values = by_kind.get(signal.kind, [])
                if len(values) >= 3:
                    signal.benchmark = round(sum(values) / len(values), 4)
                    signal.benchmark_status = "available"
            benchmark_note = f"已使用 {len(peers)} 份冻结的同业招股书文本快照。"
        else:
            benchmark_note = "同业冻结快照少于 3 份，未生成同业基准。"
        return TextSignalSnapshot(
            industry_code=industry_profile.code,
            signals=signals,
            benchmark_note=benchmark_note,
        )

    @staticmethod
    def _signal(
        kind: str,
        value: float,
        blocks: list[ProspectusBlock],
        evidence_ids: list[str],
        description: str,
    ) -> TextSignal:
        sections = list(dict.fromkeys(block.section_path for block in blocks if block.block_id in evidence_ids))
        return TextSignal(
            signal_id=f"sig_{hashlib.sha256(f'{kind}:{value}:{evidence_ids}'.encode()).hexdigest()[:12]}",
            kind=kind,
            value=value,
            section_scope=sections,
            evidence_ids=list(dict.fromkeys(evidence_ids)),
            description=description,
        )

    def _semantic_consistency(self, blocks: list[ProspectusBlock]) -> TextSignal:
        groups: dict[str, list[ProspectusBlock]] = {"risk": [], "business": []}
        for block in blocks:
            scope = block.section_path.lower()
            target = "risk" if ("风险" in scope or "risk" in scope) else "business"
            groups[target].append(block)
        if not groups["risk"] or not groups["business"]:
            return self._signal(
                "cross_section_inconsistency",
                0.0,
                blocks,
                [],
                "业务描述与风险因素章节的语义不一致度；章节不足时不可计算。",
            )
        texts = [
            "\n".join(item.text for item in groups["business"])[:6000],
            "\n".join(item.text for item in groups["risk"])[:6000],
        ]
        first, second = self.embedding_client.embed_batch(texts)
        similarity = max(-1.0, min(1.0, cosine_similarity(first, second)))
        evidence = [groups["business"][0].block_id, groups["risk"][0].block_id]
        return self._signal(
            "cross_section_inconsistency",
            round(1 - max(0.0, similarity), 4),
            blocks,
            evidence,
            "业务描述与风险因素章节的语义不一致度。",
        )

    def _section_sentiment_asymmetry(self, blocks: list[ProspectusBlock]) -> TextSignal:
        business = [item for item in blocks if not ("风险" in item.section_path or "risk" in item.section_path.lower())]
        risk = [item for item in blocks if item not in business]
        business_text = "".join(item.text for item in business)
        risk_text = "".join(item.text for item in risk)
        business_rate = sum(business_text.count(term) for term in _OPTIMISTIC) * 1000 / max(1, len(business_text))
        risk_rate = sum(risk_text.count(term) for term in _CAUTION) * 1000 / max(1, len(risk_text))
        evidence = [item.block_id for item in [*business[:1], *risk[:1]]]
        return self._signal(
            "section_sentiment_asymmetry",
            round(abs(business_rate - risk_rate), 4),
            blocks,
            evidence,
            "业务展望的乐观表达与风险因素负向表达之间的章节情绪差。",
        )

    def _template_repetition(self, blocks: list[ProspectusBlock]) -> TextSignal:
        candidates = [item for item in blocks if len(item.text.strip()) >= 40][:20]
        if len(candidates) < 2:
            return self._signal("template_repetition", 0.0, blocks, [], "不同章节之间的高相似模板化披露程度。")
        vectors = self.embedding_client.embed_batch([item.text[:1500] for item in candidates])
        best = (0.0, 0, 1)
        for left in range(len(vectors)):
            for right in range(left + 1, len(vectors)):
                if candidates[left].section_path == candidates[right].section_path:
                    continue
                similarity = cosine_similarity(vectors[left], vectors[right])
                if similarity > best[0]:
                    best = (similarity, left, right)
        return self._signal(
            "template_repetition",
            round(max(0.0, min(1.0, best[0])), 4),
            blocks,
            [candidates[best[1]].block_id, candidates[best[2]].block_id],
            "不同章节之间的高相似模板化披露程度。",
        )


_HYPOTHESIS_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "vagueness_density": ("模糊披露", "关键经营判断依赖模糊和弱承诺措辞", "模糊措辞增加可验证经营承诺的解释空间，可能掩盖兑现不确定性。"),
    "responsibility_evasion_density": ("责任边界弱化", "发行人反复将关键结果归因于不可控因素", "责任边界弱化会降低管理层承诺的可追责性，并扩大负面事件发生后的解释空间。"),
    "risk_coverage_asymmetry": ("风险披露不对称", "业务乐观表达强于相应风险披露", "乐观叙事与风险覆盖不对称可能使投资者低估经营兑现难度。"),
    "disclaimer_density": ("免责声明依赖", "核心叙事大量依赖免责声明和前瞻性陈述", "高免责声明密度可能意味着关键增长叙事缺少同期可核验事实支撑。"),
    "cross_section_inconsistency": ("跨章节叙事不一致", "业务描述与风险章节存在显著语义偏离", "跨章节叙事不一致可能暴露选择性披露或内部风险判断尚未统一。"),
    "section_sentiment_asymmetry": ("章节情绪不对称", "业务展望与风险因素的情绪强度明显不对称", "章节情绪不对称可能使积极叙事获得更高显著性，而风险约束被弱化。"),
    "template_repetition": ("模板化风险披露", "不同章节出现高相似度模板文本", "重复模板可能降低公司特有风险披露的有效信息密度。"),
}


def hypotheses_from_text_signals(
    snapshot: TextSignalSnapshot,
    industry_profile: IndustryProfile,
    *,
    limit: int,
) -> list[RiskHypothesis]:
    ranked = sorted(snapshot.signals, key=lambda item: item.value, reverse=True)
    hypotheses: list[RiskHypothesis] = []
    for signal in ranked:
        template = _HYPOTHESIS_TEMPLATES.get(signal.kind)
        if not template or not signal.evidence_ids or signal.value <= 0:
            continue
        risk_type, title, chain = template
        hypotheses.append(
            RiskHypothesis(
                risk_type=risk_type,
                title=title,
                statement=f"{signal.description} 本次测得 {signal.value}。",
                origin="text_signal",
                dimension="D3",
                causal_chain=chain,
                industry_relevance=f"需结合{industry_profile.name}行业的披露习惯和经营周期判断。",
                company_specificity=f"该信号直接来自本公司招股书的 {', '.join(signal.section_scope) or '相关章节'}。",
                expected_market_effects=["估值预期修正", "上市后信息披露波动放大"],
                prospectus_evidence_ids=signal.evidence_ids,
                evidence_grade="inferential",
                confidence=0.35,
                severity="medium",
            )
        )
        if len(hypotheses) >= limit:
            break
    return hypotheses


async def generate_agent_hypotheses(
    *,
    company_name: str,
    industry_profile: IndustryProfile,
    text_signals: TextSignalSnapshot,
    blocks: list[ProspectusBlock],
    limit: int,
    client_factory: Callable[[], Any],
) -> list[RiskHypothesis]:
    """Ask the model for rule-independent risks while validating all evidence anchors."""

    allowed = {block.block_id for block in blocks}
    payload = {
        "company_name": company_name,
        "industry_profile": industry_profile.model_dump(),
        "text_signals": text_signals.model_dump(),
        "prospectus_blocks": [
            {
                "block_id": block.block_id,
                "page_number": block.page_number,
                "section_path": block.section_path,
                "text": block.text[:900],
            }
            for block in blocks[:40]
        ],
        "limit": limit,
    }
    raw = await client_factory().chat_json(
        [
            {
                "role": "system",
                "content": (
                    "你是港股招股书隐性风险研究员。规则仅是提示，不是风险白名单。"
                    "自由识别公司或行业特有风险、文本粉饰和叙事矛盾。每条判断必须给出至少一个输入中的 block_id，"
                    "并写清因果链；低置信度允许输出。只输出 JSON：{\"items\":[{\"risk_type\":str,"
                    "\"title\":str,\"statement\":str,\"dimension\":\"D2\"|\"D3\"|\"EXT\","
                    "\"causal_chain\":str,\"industry_relevance\":str,\"company_specificity\":str,"
                    "\"expected_market_effects\":[str],\"prospectus_evidence_ids\":[str],"
                    "\"evidence_grade\":\"direct\"|\"inferential\"|\"lead\",\"confidence\":float,"
                    "\"severity\":\"low\"|\"medium\"|\"high\"}]}"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.4,
    )
    items = raw.get("items", []) if isinstance(raw, dict) else []
    result: list[RiskHypothesis] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        refs = [str(value) for value in item.get("prospectus_evidence_ids", []) if str(value) in allowed]
        if not refs or not str(item.get("causal_chain") or "").strip():
            continue
        try:
            result.append(
                RiskHypothesis.model_validate(
                    {
                        **item,
                        "origin": "agent_emergent",
                        "prospectus_evidence_ids": refs,
                    }
                )
            )
        except ValueError:
            continue
    return result


def hypothesis_snapshot_hash(hypotheses: list[RiskHypothesis]) -> str:
    payload = [item.model_dump(mode="json") for item in hypotheses]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
