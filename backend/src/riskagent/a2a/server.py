"""Official A2A 1.x Agent Card and route helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from a2a.server.request_handlers import RequestHandler
from a2a.server.request_handlers.response_helpers import build_error_response
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.utils.errors import VersionNotSupportedError
from google.protobuf.json_format import MessageToDict
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


def build_agent_card(
    agent_name: str,
    *,
    role: str = "",
    capabilities: Sequence[str | Mapping[str, Any]] | None = None,
    interface_url: str | None = None,
    input_modes: Sequence[str] | None = None,
    output_modes: Sequence[str] | None = None,
) -> AgentCard:
    skills = [_build_skill(agent_name, index, item) for index, item in enumerate(capabilities or [])]
    if not skills:
        skills = [
            AgentSkill(
                id=f"{_slug(agent_name)}-analysis",
                name=f"{agent_name} analysis",
                description=role or "Performs its assigned prospectus risk analysis module.",
                tags=["prospectus", "risk-analysis"],
            )
        ]
    return AgentCard(
        name=agent_name,
        description=role or "ProspectAlpha expert agent",
        version="1.0.0",
        default_input_modes=list(input_modes or ["application/json"]),
        default_output_modes=list(output_modes or ["application/json"]),
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=interface_url or f"http://localhost/a2a/{agent_name}",
            )
        ],
        skills=skills,
    )


def agent_card_to_dict(card: AgentCard) -> dict[str, Any]:
    return MessageToDict(card)


def build_a2a_routes(
    *,
    agent_card: AgentCard,
    request_handler: RequestHandler,
    rpc_url: str,
    card_url: str,
) -> list[Route]:
    """Create SDK-native discovery and JSON-RPC routes for an existing handler."""

    rpc_route = create_jsonrpc_routes(request_handler, rpc_url)[0]
    dispatch = rpc_route.endpoint

    async def versioned_dispatch(request: Request) -> Response:
        if request.headers.get("A2A-Version") != "1.0":
            return JSONResponse(
                build_error_response(
                    None,
                    VersionNotSupportedError("A2A-Version must be 1.0"),
                )
            )
        return await dispatch(request)

    return [
        *create_agent_card_routes(agent_card, card_url=card_url),
        Route(path=rpc_url, endpoint=versioned_dispatch, methods=["POST"]),
    ]


def _build_skill(
    agent_name: str,
    index: int,
    capability: str | Mapping[str, Any],
) -> AgentSkill:
    if isinstance(capability, str):
        name = capability
        description = capability
        tags = ["prospectus", "risk-analysis"]
        examples: list[str] = []
        skill_id = f"{_slug(agent_name)}-{index + 1}"
    else:
        name = str(capability.get("name") or capability.get("title") or f"Capability {index + 1}")
        description = str(capability.get("description") or name)
        tags = [str(item) for item in capability.get("tags", []) if str(item)]
        examples = [str(item) for item in capability.get("examples", []) if str(item)]
        skill_id = str(capability.get("id") or f"{_slug(agent_name)}-{index + 1}")
    return AgentSkill(
        id=skill_id,
        name=name,
        description=description,
        tags=tags,
        examples=examples,
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return slug or "agent"
