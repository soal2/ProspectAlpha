"""Official A2A RequestHandler backed by live JSON-mailbox runtimes."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import RequestHandler
from a2a.server.request_handlers.response_helpers import build_error_response
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.types import (
    AgentCard,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskState,
    TaskStatus,
    TaskPushNotificationConfig,
)
from a2a.utils.errors import (
    InvalidParamsError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
    VersionNotSupportedError,
)
from google.protobuf.json_format import MessageToDict
from google.protobuf.timestamp_pb2 import Timestamp
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from riskagent.a2a.mailbox import JsonMailbox
from riskagent.a2a.protocol import JsonValue, MessageType, ProtocolMessage
from riskagent.a2a.registry import RuntimeDirectory, runtime_directory
from riskagent.a2a.server import build_a2a_routes, build_agent_card
from riskagent.a2a.storage import read_json, resolve_agent_runtime_root
from riskagent.a2a.task_store import JsonTaskStore


_SAFE_CONTEXT_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_ACTIVE_EXPERT_STATES = {"IDLE", "WORKING"}
_TERMINAL_TASK_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
}


class MailboxRequestHandler(RequestHandler):
    """Bridge A2A JSON-RPC calls to active TeamRuntime mailboxes and task stores."""

    def __init__(
        self,
        *,
        agent_name: str,
        agent_card: AgentCard,
        directory: RuntimeDirectory = runtime_directory,
        send_timeout: float = 300.0,
        runtime_root: str | Path | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.agent_card = agent_card
        self.directory = directory
        self.send_timeout = send_timeout
        self.runtime_root = resolve_agent_runtime_root(runtime_root)

    async def on_message_send(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> Task | Message:
        if not params.HasField("message"):
            raise InvalidParamsError("message is required")
        source = params.message
        if not source.message_id:
            raise InvalidParamsError("message.messageId is required")
        if not source.context_id:
            raise InvalidParamsError("message.contextId must identify an active analysis run")
        _validate_context_id(source.context_id)
        task_id = source.task_id or str(uuid4())
        payload = _message_payload(source)
        return_immediately = (
            params.HasField("configuration")
            and params.configuration.return_immediately
        )
        runtime = self.directory.active_runtime(source.context_id, self.agent_name)
        if runtime is not None:
            if source.task_id:
                existing = await runtime.task_store.get(task_id, context)
                if existing is None:
                    raise TaskNotFoundError(f"Task {source.task_id} not found")
                created = False
            else:
                _message, candidate = _submitted_task(
                    source,
                    task_id,
                    payload,
                    self.agent_name,
                )
                existing, created = await runtime.task_store.reserve_for_message(
                    candidate,
                    source.message_id,
                    context,
                    assigned_agent=self.agent_name,
                )
                task_id = existing.id
            if existing is not None:
                _validate_idempotent_request(existing, source)
                if existing.status.state in _TERMINAL_TASK_STATES:
                    return existing
            if created:
                try:
                    await asyncio.to_thread(
                        runtime.assign_task,
                        self.agent_name,
                        task_id,
                        payload,
                        message_id=source.message_id,
                        role=source.role,
                    )
                except Exception as exc:
                    await asyncio.to_thread(
                        runtime.fail_task,
                        task_id,
                        f"task dispatch failed: {exc}",
                    )
                    raise
            if return_immediately:
                task = await runtime.task_store.get(task_id, context)
                if task is None:
                    raise InvalidParamsError(f"Task was not persisted: {task_id}")
                return task
            await asyncio.to_thread(runtime.collect_results, [task_id], self.send_timeout)
            task = await runtime.task_store.get(task_id, context)
        else:
            task = await self._send_through_shared_runtime(
                source,
                task_id,
                payload,
                context,
                return_immediately=return_immediately,
            )
        if task is None:
            raise InvalidParamsError(f"Task was not persisted: {task_id}")
        return task

    async def on_get_task(
        self,
        params: GetTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        store = self.directory.task_store_for_task(self.agent_name, params.id)
        if store is None:
            store = await self._find_shared_task_store(params.id, context)
        if store is None:
            return None
        task = await store.get(params.id, context)
        if task is None or not _task_belongs_to_agent(task, self.agent_name):
            return None
        return _task_with_history_limit(task, params.history_length)

    async def on_list_tasks(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        if not params.context_id:
            raise InvalidParamsError("contextId is required for the per-run JSON task store")
        store = self.directory.task_store_for_context(params.context_id, self.agent_name)
        if store is None:
            store = self._shared_task_store(params.context_id, require_active=False)
        if store is None:
            return ListTasksResponse(page_size=params.page_size, total_size=0)
        return await store.list_for_agent(params, context, self.agent_name)

    async def _send_through_shared_runtime(
        self,
        source: Message,
        task_id: str,
        payload: JsonValue,
        context: ServerCallContext,
        *,
        return_immediately: bool,
    ) -> Task:
        store = self._shared_task_store(source.context_id, require_active=True)
        if store is None:
            raise InvalidParamsError(
                f"No active {self.agent_name} runtime for context {source.context_id}"
            )
        message: ProtocolMessage | None = None
        if source.task_id:
            existing = await store.get(task_id, context)
            if existing is None:
                raise TaskNotFoundError(f"Task {source.task_id} not found")
            created = False
        else:
            message, candidate = _submitted_task(
                source,
                task_id,
                payload,
                self.agent_name,
            )
            existing, created = await store.reserve_for_message(
                candidate,
                source.message_id,
                context,
                assigned_agent=self.agent_name,
            )
            task_id = existing.id
        if existing is not None:
            _validate_idempotent_request(existing, source)
            if existing.status.state in _TERMINAL_TASK_STATES:
                return existing
            if return_immediately:
                return existing
        if created:
            assert message is not None
            JsonMailbox(
                self.runtime_root / source.context_id,
                run_id=source.context_id,
            ).send(message)
            if return_immediately:
                return existing

        deadline = time.monotonic() + self.send_timeout
        while True:
            persisted = await store.get(task_id, context)
            if persisted is not None and persisted.status.state in _TERMINAL_TASK_STATES:
                return persisted
            if time.monotonic() >= deadline:
                if persisted is not None:
                    return persisted
                raise InvalidParamsError(f"Task was not persisted: {task_id}")
            await asyncio.sleep(0.1)

    def _shared_task_store(self, context_id: str, *, require_active: bool) -> JsonTaskStore | None:
        _validate_context_id(context_id)
        run_dir = self.runtime_root / context_id
        team = read_json(run_dir / "team.json")
        expert = _team_expert(team, self.agent_name)
        if expert is None:
            return None
        if require_active and str(expert.get("state")) not in _ACTIVE_EXPERT_STATES:
            return None
        return JsonTaskStore(
            run_dir / "tasks",
            owner_resolver=lambda _context: context_id,
        )

    async def _find_shared_task_store(
        self,
        task_id: str,
        context: ServerCallContext,
    ) -> JsonTaskStore | None:
        if not self.runtime_root.exists():
            return None
        run_dirs = sorted(
            (path for path in self.runtime_root.iterdir() if path.is_dir()),
            key=lambda path: (path / "team.json").stat().st_mtime
            if (path / "team.json").exists()
            else 0,
            reverse=True,
        )
        matches: list[JsonTaskStore] = []
        for run_dir in run_dirs:
            if not _SAFE_CONTEXT_ID.fullmatch(run_dir.name):
                continue
            store = self._shared_task_store(run_dir.name, require_active=False)
            if store is None:
                continue
            task = await store.get(task_id, context)
            if task is not None and _task_belongs_to_agent(task, self.agent_name):
                matches.append(store)
        return matches[0] if len(matches) == 1 else None

    async def on_cancel_task(
        self,
        params: CancelTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        raise TaskNotCancelableError("Mailbox expert tasks cannot be interrupted safely")

    async def on_message_send_stream(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Any, None]:
        raise UnsupportedOperationError("Streaming is not enabled for JSON mailbox experts")
        yield

    async def on_subscribe_to_task(
        self,
        params: SubscribeToTaskRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Any, None]:
        raise UnsupportedOperationError("Task subscriptions are not enabled")
        yield

    async def on_create_task_push_notification_config(
        self,
        params: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        raise PushNotificationNotSupportedError("Push notifications are not enabled")

    async def on_get_task_push_notification_config(
        self,
        params: GetTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        raise PushNotificationNotSupportedError("Push notifications are not enabled")

    async def on_list_task_push_notification_configs(
        self,
        params: ListTaskPushNotificationConfigsRequest,
        context: ServerCallContext,
    ) -> ListTaskPushNotificationConfigsResponse:
        raise PushNotificationNotSupportedError("Push notifications are not enabled")

    async def on_delete_task_push_notification_config(
        self,
        params: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> None:
        raise PushNotificationNotSupportedError("Push notifications are not enabled")

    async def on_get_extended_agent_card(
        self,
        params: GetExtendedAgentCardRequest,
        context: ServerCallContext,
    ) -> AgentCard:
        raise UnsupportedOperationError("Extended Agent Cards are not enabled")


def build_expert_a2a_routes(
    experts: Sequence[Mapping[str, Any]],
    *,
    public_base_url: str,
    directory: RuntimeDirectory = runtime_directory,
    send_timeout: float = 300.0,
    runtime_root: str | Path | None = None,
) -> list[Route]:
    routes: list[Route] = []
    base_url = public_base_url.rstrip("/")
    for expert in experts:
        agent_name = str(expert["agent_name"])
        card = build_agent_card(
            agent_name,
            role=str(expert.get("role") or expert.get("description") or expert.get("title") or ""),
            capabilities=expert.get("capabilities") or [],
            interface_url=f"{base_url}/a2a/{agent_name}",
            input_modes=expert.get("input_modes") or None,
            output_modes=expert.get("output_modes") or None,
        )
        handler = MailboxRequestHandler(
            agent_name=agent_name,
            agent_card=card,
            directory=directory,
            send_timeout=send_timeout,
            runtime_root=runtime_root,
        )
        routes.extend(
            build_a2a_routes(
                agent_card=card,
                request_handler=handler,
                rpc_url=f"/a2a/{agent_name}",
                card_url=f"/a2a/{agent_name}/.well-known/agent-card.json",
            )
        )
    return routes


def build_dynamic_expert_a2a_routes(
    expert_resolver: Callable[[], Sequence[Mapping[str, Any]]],
    *,
    public_base_url: str,
    directory: RuntimeDirectory = runtime_directory,
    send_timeout: float = 300.0,
    runtime_root: str | Path | None = None,
) -> list[Route]:
    """Serve experts added after startup through the official JSON-RPC dispatcher."""

    base_url = public_base_url.rstrip("/")

    def resolve(agent_name: str) -> tuple[AgentCard, MailboxRequestHandler] | None:
        expert = next(
            (
                item
                for item in expert_resolver()
                if str(item.get("agent_name") or "") == agent_name
            ),
            None,
        )
        if expert is None:
            return None
        card = build_agent_card(
            agent_name,
            role=str(
                expert.get("role")
                or expert.get("description")
                or expert.get("title")
                or ""
            ),
            capabilities=expert.get("capabilities") or [],
            interface_url=f"{base_url}/a2a/{agent_name}",
            input_modes=expert.get("input_modes") or None,
            output_modes=expert.get("output_modes") or None,
        )
        return card, MailboxRequestHandler(
            agent_name=agent_name,
            agent_card=card,
            directory=directory,
            send_timeout=send_timeout,
            runtime_root=runtime_root,
        )

    async def get_agent_card(request: Request) -> Response:
        resolved = await asyncio.to_thread(resolve, request.path_params["agent_name"])
        if resolved is None:
            return JSONResponse({"detail": "Agent not found"}, status_code=404)
        card, _handler = resolved
        return JSONResponse(MessageToDict(card))

    async def handle_jsonrpc(request: Request) -> Response:
        if request.headers.get("A2A-Version") != "1.0":
            return JSONResponse(
                build_error_response(
                    None,
                    VersionNotSupportedError("A2A-Version must be 1.0"),
                )
            )
        resolved = await asyncio.to_thread(resolve, request.path_params["agent_name"])
        if resolved is None:
            return JSONResponse({"detail": "Agent not found"}, status_code=404)
        _card, handler = resolved
        return await JsonRpcDispatcher(request_handler=handler).handle_requests(request)

    return [
        Route(
            "/a2a/{agent_name}/.well-known/agent-card.json",
            get_agent_card,
            methods=["GET"],
        ),
        Route("/a2a/{agent_name}", handle_jsonrpc, methods=["POST"]),
    ]


def _message_payload(message: Message) -> JsonValue:
    if not message.parts:
        return {}
    parts = [MessageToDict(part) for part in message.parts]
    if len(parts) == 1:
        part = parts[0]
        if "data" in part:
            return part["data"]
        if "text" in part:
            return {"text": part["text"]}
    return {"parts": parts}


def _submitted_task(
    source: Message,
    task_id: str,
    payload: JsonValue,
    agent_name: str,
) -> tuple[ProtocolMessage, Task]:
    message = ProtocolMessage.create(
        context_id=source.context_id,
        sender="a2a_client",
        recipient=agent_name,
        message_type=MessageType.TASK,
        task_id=task_id,
        payload=payload,
        message_id=source.message_id,
        role=source.role,
    )
    task = Task(
        id=task_id,
        context_id=source.context_id,
        status=TaskStatus(
            state=TaskState.TASK_STATE_SUBMITTED,
            message=message.to_a2a_message(),
            timestamp=_timestamp_now(),
        ),
        history=[message.to_a2a_message()],
    )
    task.metadata.update({"assignedAgent": agent_name})
    return message, task


def _task_with_history_limit(task: Task, history_length: int) -> Task:
    copied = Task()
    copied.CopyFrom(task)
    if history_length > 0:
        history = list(copied.history[-history_length:])
        del copied.history[:]
        copied.history.extend(history)
    elif history_length == 0:
        del copied.history[:]
    return copied


def _team_expert(team: Any, agent_name: str) -> dict[str, Any] | None:
    experts = team.get("experts", []) if isinstance(team, dict) else []
    return next(
        (
            item
            for item in experts
            if isinstance(item, dict) and item.get("agent_name") == agent_name
        ),
        None,
    )


def _validate_context_id(context_id: str) -> None:
    if not _SAFE_CONTEXT_ID.fullmatch(context_id):
        raise InvalidParamsError("contextId contains unsupported path characters")


def _timestamp_now() -> Timestamp:
    timestamp = Timestamp()
    timestamp.GetCurrentTime()
    return timestamp


def _task_belongs_to_agent(task: Task, agent_name: str) -> bool:
    return MessageToDict(task.metadata).get("assignedAgent") == agent_name


def _validate_idempotent_request(task: Task, source: Message) -> None:
    if source.message_id and any(
        item.message_id == source.message_id for item in task.history
    ):
        return
    raise InvalidParamsError(f"Task id already exists: {task.id}")
