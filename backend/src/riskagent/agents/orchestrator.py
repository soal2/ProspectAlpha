"""Controller agent boundary."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, field_validator

from riskagent.providers.llm import get_default_llm_client


_ALLOWED_AGENTS = {"financial_agent", "legal_agent", "business_agent", "market_agent"}


class OrchestraStage(BaseModel):
    key: str
    label: str
    agent: str
    goal: str
    depends_on: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)

    @field_validator("agent")
    @classmethod
    def validate_agent(cls, value: str) -> str:
        if value not in _ALLOWED_AGENTS:
            raise ValueError("agent must be one of financial_agent, legal_agent, business_agent, market_agent")
        return value


class OrchestraPlan(BaseModel):
    summary: str
    focus_areas: list[str]
    stages: list[OrchestraStage]


async def orchestrate(
    *,
    user_requirement: str,
    company_name: str,
    document_summary: str,
    available_agents: list[dict[str, Any]],
) -> OrchestraPlan:
    agent_names = [str(item.get("agent_name", "")).strip() for item in available_agents]
    try:
        messages = _messages(
            user_requirement=user_requirement,
            company_name=company_name,
            document_summary=document_summary[:2000],
            available_agents=available_agents,
        )
        raw = await get_default_llm_client().chat_json(messages, temperature=0.2)
        plan = OrchestraPlan.model_validate(raw)
        available = {name for name in agent_names if name}
        if not plan.stages or any(stage.agent not in available for stage in plan.stages):
            return default_plan(agent_names)
        if not any(not stage.depends_on for stage in plan.stages):
            return default_plan(agent_names)
        return plan
    except Exception:
        return default_plan(agent_names)


def default_plan(agent_names: list[str]) -> OrchestraPlan:
    names = [name for name in agent_names if name in _ALLOWED_AGENTS] or [
        "financial_agent",
        "legal_agent",
        "business_agent",
        "market_agent",
    ]
    stages = []
    for index, agent_name in enumerate(names, start=1):
        key = f"stage_{index}_{agent_name.replace('_agent', '')}"
        depends_on = [] if index == 1 else [stages[-1].key]
        stages.append(
            OrchestraStage(
                key=key,
                label=_label_for_agent(agent_name),
                agent=agent_name,
                goal=_goal_for_agent(agent_name),
                depends_on=depends_on,
                expected_outputs=_outputs_for_agent(agent_name),
            )
        )
    return OrchestraPlan(
        summary="按默认线性编队完成招股书风险分析。",
        focus_areas=["财务风险", "法律合规", "业务经营", "市场竞争"],
        stages=stages,
    )


def _messages(
    *,
    user_requirement: str,
    company_name: str,
    document_summary: str,
    available_agents: list[dict[str, Any]],
) -> list[dict[str, str]]:
    roster = json.dumps(available_agents, ensure_ascii=False)
    return [
        {
            "role": "system",
            "content": (
                "你是 ProspectAlpha 的首席指挥官「沈北辰」，负责基于用户需求与招股书摘要，组织一支多阶段分析编队。"
                "约束：stages 数量 3-6；每个 stage.agent 必须来自 available_agents 的 agent_name；"
                "必须包含至少 1 个起始 stage（depends_on=[]）。"
                '输出 JSON schema exactly {"summary":str,"focus_areas":[str],"stages":[{"key","label","agent","goal","depends_on":[],"expected_outputs":[]}]}。'
            ),
        },
        {
            "role": "user",
            "content": (
                f"公司名称：{company_name}\n"
                f"用户需求：{user_requirement}\n"
                f"招股书摘要：{document_summary}\n"
                f"可用 Agent 编队：{roster}"
            ),
        },
    ]


def _label_for_agent(agent_name: str) -> str:
    labels = {
        "financial_agent": "财务风险扫描",
        "legal_agent": "法律合规核验",
        "business_agent": "业务经营分析",
        "market_agent": "市场竞争研判",
    }
    return labels.get(agent_name, agent_name)


def _goal_for_agent(agent_name: str) -> str:
    goals = {
        "financial_agent": "识别财务指标、现金流、偿债能力和盈利质量风险。",
        "legal_agent": "识别诉讼、监管、关联交易、担保和特殊权利安排风险。",
        "business_agent": "识别业务模式、客户供应商、持续经营和运营稳定性风险。",
        "market_agent": "识别行业竞争、市场空间、政策变化和外部环境风险。",
    }
    return goals.get(agent_name, "完成指定专业风险分析。")


def _outputs_for_agent(agent_name: str) -> list[str]:
    outputs = {
        "financial_agent": ["财务风险 finding", "财务证据片段"],
        "legal_agent": ["合规风险 finding", "法律证据片段"],
        "business_agent": ["经营风险 finding", "业务证据片段"],
        "market_agent": ["市场风险 finding", "行业证据片段"],
    }
    return outputs.get(agent_name, ["风险 finding", "证据片段"])
