"""A2A protocol messages used by the JSON mailbox transport.

Mailbox lines are serialized official ``a2a.types.Message`` values. Runtime-only
routing and lifecycle fields live under the Message ``metadata`` extension.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, TypeAlias
from uuid import uuid4

from a2a.types import Message, Role
from google.protobuf.json_format import MessageToDict, ParseDict


JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
METADATA_NAMESPACE = "prospectalpha"


class MessageType(StrEnum):
    TASK = "task"
    RESULT = "result"
    PEER_REQUEST = "peer_request"
    PEER_RESPONSE = "peer_response"
    IDLE_NOTIFICATION = "idle_notification"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_APPROVED = "shutdown_approved"
    SHUTDOWN_REJECTED = "shutdown_rejected"


TASK_MESSAGE = MessageType.TASK.value
RESULT_MESSAGE = MessageType.RESULT.value
PEER_REQUEST_MESSAGE = MessageType.PEER_REQUEST.value
PEER_RESPONSE_MESSAGE = MessageType.PEER_RESPONSE.value
IDLE_NOTIFICATION_MESSAGE = MessageType.IDLE_NOTIFICATION.value
SHUTDOWN_REQUEST_MESSAGE = MessageType.SHUTDOWN_REQUEST.value
SHUTDOWN_APPROVED_MESSAGE = MessageType.SHUTDOWN_APPROVED.value
SHUTDOWN_REJECTED_MESSAGE = MessageType.SHUTDOWN_REJECTED.value


@dataclass(frozen=True, slots=True)
class ProtocolMessage:
    message_id: str
    context_id: str
    sender: str
    recipient: str
    message_type: MessageType
    payload: JsonValue
    task_id: str | None = None
    request_id: str | None = None
    in_reply_to: str | None = None
    created_at: str = ""
    role: int = Role.ROLE_AGENT

    @classmethod
    def create(
        cls,
        *,
        context_id: str,
        sender: str,
        recipient: str,
        message_type: MessageType,
        payload: JsonValue,
        task_id: str | None = None,
        request_id: str | None = None,
        in_reply_to: str | None = None,
        message_id: str | None = None,
        role: int = Role.ROLE_AGENT,
    ) -> "ProtocolMessage":
        _assert_json_serializable(payload)
        return cls(
            message_id=message_id or str(uuid4()),
            context_id=context_id,
            sender=sender,
            recipient=recipient,
            message_type=message_type,
            payload=payload,
            task_id=task_id,
            request_id=request_id,
            in_reply_to=in_reply_to,
            created_at=datetime.now(timezone.utc).isoformat(),
            role=role,
        )

    def to_a2a_message(self) -> Message:
        routing: dict[str, JsonValue] = {
            "messageType": self.message_type.value,
            "sender": self.sender,
            "recipient": self.recipient,
            "createdAt": self.created_at,
        }
        if self.request_id:
            routing["requestId"] = self.request_id
        if self.in_reply_to:
            routing["inReplyTo"] = self.in_reply_to

        value: dict[str, Any] = {
            "messageId": self.message_id,
            "contextId": self.context_id,
            "role": Role.Name(self.role),
            "parts": [{"data": self.payload}],
            "metadata": {METADATA_NAMESPACE: routing},
        }
        if self.task_id:
            value["taskId"] = self.task_id
        return ParseDict(value, Message())

    def to_dict(self) -> dict[str, Any]:
        return MessageToDict(self.to_a2a_message())

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_a2a_message(cls, message: Message) -> "ProtocolMessage":
        metadata = MessageToDict(message.metadata)
        routing = metadata.get(METADATA_NAMESPACE)
        if not isinstance(routing, dict):
            raise ValueError(f"Message metadata must contain {METADATA_NAMESPACE}")

        sender = routing.get("sender")
        recipient = routing.get("recipient")
        kind = routing.get("messageType")
        if not isinstance(sender, str) or not sender:
            raise ValueError("Message metadata sender is required")
        if not isinstance(recipient, str) or not recipient:
            raise ValueError("Message metadata recipient is required")
        if not isinstance(kind, str):
            raise ValueError("Message metadata messageType is required")
        if not message.message_id or not message.context_id:
            raise ValueError("A2A messageId and contextId are required")
        if not message.parts or message.parts[0].WhichOneof("content") != "data":
            raise ValueError("Mailbox A2A messages require a JSON data part")

        payload = MessageToDict(message.parts[0].data)
        _assert_json_serializable(payload)
        return cls(
            message_id=message.message_id,
            context_id=message.context_id,
            sender=sender,
            recipient=recipient,
            message_type=MessageType(kind),
            payload=payload,
            task_id=message.task_id or None,
            request_id=_optional_string(routing.get("requestId")),
            in_reply_to=_optional_string(routing.get("inReplyTo")),
            created_at=_optional_string(routing.get("createdAt")) or "",
            role=message.role,
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProtocolMessage":
        return cls.from_a2a_message(ParseDict(value, Message()))

    @classmethod
    def from_json(cls, value: str) -> "ProtocolMessage":
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("Mailbox line must be a JSON object")
        return cls.from_dict(parsed)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _assert_json_serializable(value: Any) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TypeError("A2A payload must be JSON serializable") from exc
