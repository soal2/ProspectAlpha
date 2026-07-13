from a2a.types import AgentCard, Message

from riskagent.a2a import (
    MessageType,
    ProtocolMessage,
    agent_card_to_dict,
    build_a2a_routes,
    build_agent_card,
)


def test_protocol_message_round_trip_is_an_official_a2a_message():
    original = ProtocolMessage.create(
        context_id="run-1",
        sender="strategic_agent",
        recipient="legal_agent",
        message_type=MessageType.TASK,
        task_id="legal-module",
        payload={"goal": "Review litigation"},
        request_id="request-1",
    )

    official = original.to_a2a_message()
    restored = ProtocolMessage.from_json(original.to_json())

    assert isinstance(official, Message)
    assert restored.message_id == original.message_id
    assert restored.payload == {"goal": "Review litigation"}
    assert restored.request_id == "request-1"
    serialized = original.to_dict()
    assert serialized["metadata"]["prospectalpha"]["messageType"] == "task"
    assert "sender" not in serialized
    assert "recipient" not in serialized


def test_build_agent_card_returns_sdk_model_with_jsonrpc_interface():
    card = build_agent_card(
        "business_agent",
        role="Business continuity analyst",
        capabilities=[{"id": "customer-risk", "name": "Customer concentration"}],
    )

    assert isinstance(card, AgentCard)
    value = agent_card_to_dict(card)
    assert value["supportedInterfaces"][0] == {
        "url": "http://localhost/a2a/business_agent",
        "protocolBinding": "JSONRPC",
        "protocolVersion": "1.0",
    }
    assert value["skills"][0]["id"] == "customer-risk"


def test_route_helper_returns_sdk_agent_card_and_jsonrpc_routes():
    routes = build_a2a_routes(
        agent_card=build_agent_card("legal_agent"),
        request_handler=object(),
        rpc_url="/a2a/legal_agent",
        card_url="/a2a/legal_agent/.well-known/agent-card.json",
    )

    assert [route.path for route in routes] == [
        "/a2a/legal_agent/.well-known/agent-card.json",
        "/a2a/legal_agent",
    ]
