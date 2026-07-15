"""Threaded expert-team runtime over per-run A2A JSON mailboxes."""

from __future__ import annotations

import asyncio
import fcntl
import inspect
import json
import os
import re
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeAlias
from uuid import uuid4

from a2a.server.context import ServerCallContext
from a2a.types import AgentCard, Artifact, Role, Task, TaskState, TaskStatus
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.timestamp_pb2 import Timestamp

from riskagent.a2a.mailbox import JsonMailbox, MailboxDelivery
from riskagent.a2a.protocol import JsonValue, MessageType, ProtocolMessage
from riskagent.a2a.registry import runtime_directory
from riskagent.a2a.server import agent_card_to_dict, build_agent_card
from riskagent.a2a.storage import (
    atomic_write_json,
    ensure_private_directory,
    read_json,
    resolve_agent_runtime_root,
)
from riskagent.a2a.task_store import JsonTaskStore


STRATEGIC_AGENT = "strategic_agent"
POLL_INTERVAL_SECONDS = 0.5
_RESPONSE_POLL_INTERVAL_SECONDS = 0.05
MAX_PEER_ROUNDS_PER_TASK = 3
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class ExpertState(StrEnum):
    CREATED = "CREATED"
    IDLE = "IDLE"
    WORKING = "WORKING"
    SHUTDOWN = "SHUTDOWN"


class PeerRequestError(RuntimeError):
    pass


ExpertHandler: TypeAlias = Callable[
    [JsonValue, "PeerContext"],
    JsonValue | Awaitable[JsonValue],
]
StrategyHandler: TypeAlias = Callable[[JsonValue, str], JsonValue]
EventCallback: TypeAlias = Callable[[dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class _PendingTask:
    assigned_agent: str
    request_message_id: str
    module_id: str | None


@dataclass(frozen=True, slots=True)
class _PendingPeerRequest:
    requester: str
    responder: str
    request_message_id: str


class PeerContext:
    """Handler-facing peer messaging API with request-id correlation."""

    def __init__(self, runtime: "TeamRuntime", worker: "_ExpertWorker", message: ProtocolMessage) -> None:
        self._runtime = runtime
        self._worker = worker
        self._message = message
        self._peer_rounds = 0

    @property
    def agent_name(self) -> str:
        return self._worker.agent_name

    @property
    def origin_agent(self) -> str:
        return self._message.sender

    @property
    def task_id(self) -> str | None:
        return self._message.task_id

    @property
    def message_type(self) -> MessageType:
        return self._message.message_type

    @property
    def request_id(self) -> str | None:
        return self._message.request_id

    @property
    def available_peers(self) -> list[dict[str, Any]]:
        """Return live Agent Card profiles for the other experts in this team."""

        return self._runtime.peer_profiles(excluding=self.agent_name)

    def send(self, recipient: str, payload: JsonValue) -> str:
        self._peer_rounds += 1
        if self._peer_rounds > MAX_PEER_ROUNDS_PER_TASK:
            raise PeerRequestError(
                f"A task may initiate at most {MAX_PEER_ROUNDS_PER_TASK} peer requests"
            )
        request_id = str(uuid4())
        self._runtime._send_peer_request(
            sender=self.agent_name,
            recipient=recipient,
            task_id=self.task_id,
            request_id=request_id,
            payload=payload,
        )
        return request_id

    def read(self, request_id: str, timeout: float = 30.0) -> JsonValue:
        response = self._worker.wait_for_peer_response(request_id, timeout=timeout)
        status = response.get("status") if isinstance(response, dict) else None
        if status != "completed":
            error = response.get("error", "Peer request failed") if isinstance(response, dict) else "Peer request failed"
            raise PeerRequestError(str(error))
        return response.get("result")

    def request(self, recipient: str, payload: JsonValue, timeout: float = 30.0) -> JsonValue:
        return self.read(self.send(recipient, payload), timeout=timeout)


class TeamRuntime:
    """Owns one dynamically selected team and its independent expert threads."""

    def __init__(
        self,
        run_id: str,
        root_dir: str | Path | None = None,
        event_callback: EventCallback | None = None,
    ) -> None:
        if not run_id or not _SAFE_RUN_ID.fullmatch(run_id):
            raise ValueError("run_id must contain only letters, digits, dot, underscore, or hyphen")
        base_dir = resolve_agent_runtime_root(root_dir)
        self.run_id = run_id
        self.run_dir = ensure_private_directory(base_dir / run_id)
        self._runtime_lock_fd = os.open(
            self.run_dir / ".team-runtime.lock",
            os.O_CREAT | os.O_RDWR,
            0o600,
        )
        os.fchmod(self._runtime_lock_fd, 0o600)
        try:
            fcntl.flock(
                self._runtime_lock_fd,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as exc:
            os.close(self._runtime_lock_fd)
            self._runtime_lock_fd = None
            raise RuntimeError(
                f"A TeamRuntime is already active for run {run_id}"
            ) from exc
        self.mailbox = JsonMailbox(self.run_dir, run_id=run_id)
        self.task_store = JsonTaskStore(
            self.run_dir / "tasks",
            owner_resolver=lambda _context: self.run_id,
        )
        self.mailbox.register(STRATEGIC_AGENT)

        self._event_callback = event_callback
        self._condition = threading.Condition(threading.RLock())
        self._experts: dict[str, _ExpertWorker] = {}
        self._states: dict[str, ExpertState] = {}
        self._cards: dict[str, AgentCard] = {}
        self._events: list[dict[str, Any]] = []
        self._result_cache: dict[str, JsonValue] = {}
        self._pending_tasks: dict[str, _PendingTask] = {}
        self._pending_peer_requests: dict[str, _PendingPeerRequest] = {}
        self._shutdown_requests: dict[str, tuple[str, str]] = {}
        self._shutdown_approved: set[str] = set()
        self._strategy_handler: StrategyHandler | None = None
        self._broadcast_requests: dict[str, str] = {}
        self._broadcast_responses: dict[str, JsonValue] = {}
        public_base_url = (
            os.getenv("A2A_PUBLIC_BASE_URL", "").strip() or "http://localhost:8000"
        ).rstrip("/")
        self._strategy_card = build_agent_card(
            STRATEGIC_AGENT,
            role="战略编排 Agent「沈北辰」：统筹任务分配、答复专家的计划/模块/同伴状态咨询。",
            capabilities=["mission planning", "team coordination", "task guidance"],
            interface_url=f"{public_base_url}/a2a/{STRATEGIC_AGENT}",
        )
        self._task_context = ServerCallContext()
        self._write_team_file()
        runtime_directory.register(self)

    def start_expert(
        self,
        agent_name: str,
        handler: ExpertHandler,
        *,
        role: str = "",
        capabilities: Sequence[str | Mapping[str, Any]] | None = None,
        input_modes: Sequence[str] | None = None,
        output_modes: Sequence[str] | None = None,
    ) -> AgentCard:
        if not callable(handler):
            raise TypeError("handler must be callable")
        public_base_url = (
            os.getenv("A2A_PUBLIC_BASE_URL", "").strip() or "http://localhost:8000"
        ).rstrip("/")
        card = build_agent_card(
            agent_name,
            role=role,
            capabilities=capabilities,
            interface_url=f"{public_base_url}/a2a/{agent_name}",
            input_modes=input_modes,
            output_modes=output_modes,
        )
        with self._condition:
            if agent_name in self._experts:
                raise ValueError(f"Expert already started: {agent_name}")
            self.mailbox.register(agent_name)
            worker = _ExpertWorker(runtime=self, agent_name=agent_name, handler=handler)
            self._experts[agent_name] = worker
            self._states[agent_name] = ExpertState.CREATED
            self._cards[agent_name] = card
            self._write_team_file_locked()
            self._condition.notify_all()
        self._emit("expert_created", agent_name=agent_name, state=ExpertState.CREATED.value)
        runtime_directory.register_expert(self.run_id, agent_name)
        worker.start()
        return card

    def wait_until_idle(self, names: Sequence[str], timeout: float) -> bool:
        requested = set(names)
        with self._condition:
            missing = requested.difference(self._experts)
            if missing:
                raise KeyError(f"Unknown experts: {', '.join(sorted(missing))}")
            return self._condition.wait_for(
                lambda: all(self._states[name] == ExpertState.IDLE for name in requested),
                timeout=timeout,
            )

    def assign_task(
        self,
        agent_name: str,
        task_id: str,
        payload: JsonValue,
        *,
        message_id: str | None = None,
        role: int = Role.ROLE_USER,
    ) -> str:
        if not task_id:
            raise ValueError("task_id is required")
        self._require_running_expert(agent_name)
        message = ProtocolMessage.create(
            context_id=self.run_id,
            sender=STRATEGIC_AGENT,
            recipient=agent_name,
            message_type=MessageType.TASK,
            task_id=task_id,
            payload=payload,
            message_id=message_id,
            role=role,
        )
        with self._condition:
            if task_id in self._pending_tasks or task_id in self._result_cache:
                raise ValueError(f"Task is already pending or completed: {task_id}")
            self._pending_tasks[task_id] = _PendingTask(
                assigned_agent=agent_name,
                request_message_id=message.message_id,
                module_id=_assignment_module_id(payload),
            )
        try:
            self._record_task_submitted(message)
            runtime_directory.register_task(self.run_id, agent_name, task_id)
            message_id = self.mailbox.send(message)
        except Exception:
            with self._condition:
                self._pending_tasks.pop(task_id, None)
            raise
        self._emit(
            "task_assigned",
            agent_name=agent_name,
            task_id=task_id,
            module_id=_assignment_module_id(payload),
            message_id=message_id,
        )
        return message_id

    def collect_results(self, expected_task_ids: Sequence[str], timeout: float) -> dict[str, JsonValue]:
        expected = list(dict.fromkeys(expected_task_ids))
        if not expected:
            return {}
        deadline = time.monotonic() + timeout
        while True:
            if all(task_id in self._result_cache for task_id in expected):
                return {task_id: self._result_cache[task_id] for task_id in expected}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                missing = [task_id for task_id in expected if task_id not in self._result_cache]
                raise TimeoutError(f"Timed out waiting for task results: {', '.join(missing)}")
            delivery = self.mailbox.receive(STRATEGIC_AGENT)
            if delivery is None:
                time.sleep(min(_RESPONSE_POLL_INTERVAL_SECONDS, remaining))
                continue
            self._consume_strategy_delivery(delivery)

    def fail_task(self, task_id: str, error: str) -> bool:
        """Persist a terminal failure for a timeout or post-result validation error."""

        with self._condition:
            pending = self._pending_tasks.pop(task_id, None)
        task = asyncio.run(self.task_store.get(task_id, self._task_context))
        if task is None:
            return False
        if pending is None and task.status.state in {
            TaskState.TASK_STATE_COMPLETED,
            TaskState.TASK_STATE_FAILED,
            TaskState.TASK_STATE_CANCELED,
            TaskState.TASK_STATE_REJECTED,
        }:
            return False
        assigned_agent = (
            pending.assigned_agent
            if pending is not None
            else str(MessageToDict(task.metadata).get("assignedAgent") or "")
        )
        request_message_id = (
            pending.request_message_id
            if pending is not None
            else next(
                (item.message_id for item in task.history if item.message_id),
                "",
            )
        )
        if not assigned_agent or not request_message_id:
            return False
        result: dict[str, JsonValue] = {"status": "failed", "error": error}
        with self._condition:
            self._result_cache[task_id] = result
        response = ProtocolMessage.create(
            context_id=self.run_id,
            sender=STRATEGIC_AGENT,
            recipient=assigned_agent,
            message_type=MessageType.RESULT,
            task_id=task_id,
            payload=result,
            in_reply_to=request_message_id,
            message_id=f"{request_message_id}:failed",
            role=Role.ROLE_AGENT,
        )
        task.status.CopyFrom(
            TaskStatus(
                state=TaskState.TASK_STATE_FAILED,
                message=response.to_a2a_message(),
                timestamp=_timestamp_now(),
            )
        )
        if not any(item.message_id == response.message_id for item in task.history):
            task.history.append(response.to_a2a_message())
        failure_artifact_id = f"{task_id}:failure"
        if not any(item.artifact_id == failure_artifact_id for item in task.artifacts):
            task.artifacts.append(
                ParseDict(
                    {
                        "artifactId": failure_artifact_id,
                        "name": f"{task_id} expert failure",
                        "parts": [{"data": result}],
                    },
                    Artifact(),
                )
            )
        asyncio.run(self.task_store.save(task, self._task_context))
        self._emit(
            "task_failed",
            agent_name=assigned_agent,
            task_id=task_id,
            module_id=pending.module_id if pending is not None else None,
            error=error,
        )
        return True

    def _task_is_pending(self, request: ProtocolMessage, agent_name: str) -> bool:
        if not request.task_id:
            return False
        with self._condition:
            pending = self._pending_tasks.get(request.task_id)
            return bool(
                pending
                and pending.assigned_agent == agent_name
                and pending.request_message_id == request.message_id
            )

    def _claim_task_request(
        self,
        request: ProtocolMessage,
        agent_name: str,
    ) -> bool:
        """Validate/register a task delivered by either this process or the shared API."""

        if not request.task_id:
            return False
        with self._condition:
            pending = self._pending_tasks.get(request.task_id)
            if pending is not None:
                return (
                    pending.assigned_agent == agent_name
                    and pending.request_message_id == request.message_id
                )
        task = asyncio.run(self.task_store.get(request.task_id, self._task_context))
        if (
            task is None
            or task.context_id != self.run_id
            or MessageToDict(task.metadata).get("assignedAgent") != agent_name
            or not any(
                message.message_id == request.message_id for message in task.history
            )
            or task.status.state
            not in {
                TaskState.TASK_STATE_SUBMITTED,
                TaskState.TASK_STATE_WORKING,
            }
        ):
            return False
        with self._condition:
            existing = self._pending_tasks.get(request.task_id)
            if existing is not None:
                return (
                    existing.assigned_agent == agent_name
                    and existing.request_message_id == request.message_id
                )
            self._pending_tasks[request.task_id] = _PendingTask(
                assigned_agent=agent_name,
                request_message_id=request.message_id,
                module_id=_assignment_module_id(request.payload),
            )
        return True

    def shutdown_all(self, timeout: float) -> None:
        with self._condition:
            workers = list(self._experts.values())
            pending_task_ids = list(self._pending_tasks)
        for task_id in pending_task_ids:
            self.fail_task(task_id, "team shutdown before task completion")
        requested: set[str] = set()
        for worker in workers:
            if worker.is_alive() and self.state(worker.agent_name) != ExpertState.SHUTDOWN:
                request_id = str(uuid4())
                requested.add(worker.agent_name)
                request = ProtocolMessage.create(
                    context_id=self.run_id,
                    sender=STRATEGIC_AGENT,
                    recipient=worker.agent_name,
                    message_type=MessageType.SHUTDOWN_REQUEST,
                    payload={"reason": "team_runtime_shutdown"},
                    request_id=request_id,
                    role=Role.ROLE_USER,
                )
                self._shutdown_requests[worker.agent_name] = (
                    request_id,
                    request.message_id,
                )
                self.mailbox.send(request)
        deadline = time.monotonic() + timeout
        for worker in workers:
            worker.join(timeout=max(0.0, deadline - time.monotonic()))
        survivors = [worker.agent_name for worker in workers if worker.is_alive()]
        self._drain_strategy_mailbox()
        if survivors:
            raise TimeoutError(f"Expert threads did not shut down: {', '.join(survivors)}")
        missing_approvals = sorted(requested - self._shutdown_approved)
        if missing_approvals:
            raise RuntimeError(
                "Expert shutdown completed without correlated approval: "
                + ", ".join(missing_approvals)
            )
        runtime_directory.mark_inactive(self.run_id)
        self._release_runtime_lock()

    def state(self, agent_name: str) -> ExpertState:
        with self._condition:
            if agent_name not in self._states:
                raise KeyError(agent_name)
            return self._states[agent_name]

    @property
    def states(self) -> dict[str, str]:
        with self._condition:
            return {name: state.value for name, state in self._states.items()}

    @property
    def events(self) -> list[dict[str, Any]]:
        with self._condition:
            return [dict(event) for event in self._events]

    def peer_profiles(self, *, excluding: str | None = None) -> list[dict[str, Any]]:
        with self._condition:
            profiles = [
                agent_card_to_dict(card)
                for name, card in sorted(self._cards.items())
                if name != excluding and self._states.get(name) != ExpertState.SHUTDOWN
            ]
        if excluding != STRATEGIC_AGENT:
            profiles.append(agent_card_to_dict(self._strategy_card))
        return profiles

    def set_strategy_handler(self, handler: StrategyHandler) -> None:
        """Register the callable answering expert peer requests sent to the orchestrator.

        The handler receives (payload, sender_agent_name) and must return a
        JSON-serializable value. It runs inline on the thread draining the
        strategic mailbox (collect_results / read_broadcast_responses), so it
        must be deterministic and fast — no nested LLM calls.
        """

        if not callable(handler):
            raise TypeError("handler must be callable")
        self._strategy_handler = handler

    def broadcast(self, payload: JsonValue) -> dict[str, str]:
        """Send one peer request from the orchestrator to every running expert.

        Returns agent_name → request_id for use with read_broadcast_responses.
        """

        with self._condition:
            recipients = [
                name
                for name, worker in self._experts.items()
                if worker.is_alive() and self._states[name] != ExpertState.SHUTDOWN
            ]
        request_ids: dict[str, str] = {}
        for name in sorted(recipients):
            request_id = str(uuid4())
            self._send_peer_request(
                sender=STRATEGIC_AGENT,
                recipient=name,
                task_id=None,
                request_id=request_id,
                payload=payload,
            )
            with self._condition:
                self._broadcast_requests[request_id] = name
                self._broadcast_responses.pop(name, None)
            request_ids[name] = request_id
        self._emit(
            "strategy_broadcast_sent",
            agent_name=STRATEGIC_AGENT,
            recipients=sorted(request_ids),
        )
        return request_ids

    def read_broadcast_responses(
        self,
        request_ids: Mapping[str, str],
        timeout: float,
    ) -> dict[str, JsonValue]:
        """Collect broadcast replies until all arrive or timeout; returns partial results."""

        expected = set(request_ids)
        deadline = time.monotonic() + timeout
        while True:
            with self._condition:
                done = {
                    name: self._broadcast_responses[name]
                    for name in expected
                    if name in self._broadcast_responses
                }
            if len(done) == len(expected):
                return done
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return done
            delivery = self.mailbox.receive(STRATEGIC_AGENT)
            if delivery is None:
                time.sleep(min(_RESPONSE_POLL_INTERVAL_SECONDS, remaining))
                continue
            self._consume_strategy_delivery(delivery)

    def thread_for(self, agent_name: str) -> threading.Thread:
        with self._condition:
            return self._experts[agent_name].thread

    def __enter__(self) -> "TeamRuntime":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.shutdown_all(timeout=10.0)

    def __del__(self) -> None:
        descriptor = getattr(self, "_runtime_lock_fd", None)
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            except Exception:
                pass
            self._runtime_lock_fd = None

    def _require_running_expert(self, agent_name: str) -> None:
        with self._condition:
            worker = self._experts.get(agent_name)
            if worker is None or not worker.is_alive() or self._states[agent_name] == ExpertState.SHUTDOWN:
                raise ValueError(f"Expert is not running: {agent_name}")

    def _release_runtime_lock(self) -> None:
        descriptor = self._runtime_lock_fd
        if descriptor is None:
            return
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        self._runtime_lock_fd = None

    def _send_peer_request(
        self,
        *,
        sender: str,
        recipient: str,
        task_id: str | None,
        request_id: str,
        payload: JsonValue,
    ) -> None:
        # The orchestrator is not an expert worker; its mailbox is always live.
        if recipient != STRATEGIC_AGENT:
            self._require_running_expert(recipient)
        message = ProtocolMessage.create(
            context_id=self.run_id,
            sender=sender,
            recipient=recipient,
            message_type=MessageType.PEER_REQUEST,
            task_id=task_id,
            request_id=request_id,
            payload=payload,
            role=Role.ROLE_AGENT,
        )
        with self._condition:
            if request_id in self._pending_peer_requests:
                raise PeerRequestError(f"Peer request_id is already pending: {request_id}")
            self._pending_peer_requests[request_id] = _PendingPeerRequest(
                requester=sender,
                responder=recipient,
                request_message_id=message.message_id,
            )
        try:
            message_id = self.mailbox.send(message)
        except Exception:
            with self._condition:
                self._pending_peer_requests.pop(request_id, None)
            raise
        self._emit(
            "peer_request_sent",
            agent_name=sender,
            recipient=recipient,
            task_id=task_id,
            request_id=request_id,
            message_id=message_id,
        )

    def _consume_strategy_delivery(self, delivery: MailboxDelivery) -> None:
        message = delivery.message
        if message.message_type == MessageType.RESULT:
            if not message.task_id:
                self.mailbox.reject(delivery, "Result message is missing task_id")
                return
            module_id: str | None = None
            with self._condition:
                pending = self._pending_tasks.get(message.task_id)
                if pending is None:
                    rejection_reason = "Result does not match a pending task"
                elif message.sender != pending.assigned_agent:
                    rejection_reason = "Result sender does not match the assigned agent"
                elif message.in_reply_to != pending.request_message_id:
                    rejection_reason = "Result in_reply_to does not match the task message_id"
                else:
                    rejection_reason = None
                    module_id = pending.module_id
                    self._result_cache[message.task_id] = message.payload
                    self._pending_tasks.pop(message.task_id, None)
            if rejection_reason:
                self.mailbox.reject(delivery, rejection_reason)
                return
            self.mailbox.acknowledge(delivery)
            self._emit(
                "task_result_received",
                agent_name=message.sender,
                task_id=message.task_id,
                module_id=module_id,
                message_id=message.message_id,
            )
            return
        if message.message_type == MessageType.PEER_REQUEST:
            self._answer_strategy_peer_request(delivery)
            return
        if message.message_type == MessageType.PEER_RESPONSE:
            if not message.request_id:
                self.mailbox.reject(delivery, "Peer response is missing request_id")
                return
            with self._condition:
                expected_sender = self._broadcast_requests.get(message.request_id)
            if expected_sender is None or expected_sender != message.sender:
                self.mailbox.reject(
                    delivery,
                    "Peer response does not match a pending broadcast request",
                )
                return
            if not self._claim_peer_response(STRATEGIC_AGENT, delivery):
                return
            with self._condition:
                self._broadcast_requests.pop(message.request_id, None)
                self._broadcast_responses[message.sender] = message.payload
            self._emit(
                "strategy_broadcast_response",
                agent_name=message.sender,
                request_id=message.request_id,
            )
            return
        if message.message_type in {
            MessageType.IDLE_NOTIFICATION,
            MessageType.SHUTDOWN_APPROVED,
            MessageType.SHUTDOWN_REJECTED,
        }:
            if message.message_type in {
                MessageType.SHUTDOWN_APPROVED,
                MessageType.SHUTDOWN_REJECTED,
            }:
                expected = self._shutdown_requests.get(message.sender)
                if (
                    not expected
                    or message.request_id != expected[0]
                    or message.in_reply_to != expected[1]
                ):
                    self.mailbox.reject(
                        delivery,
                        "Shutdown response does not match request_id/in_reply_to",
                    )
                    return
                if message.message_type == MessageType.SHUTDOWN_REJECTED:
                    self.mailbox.reject(delivery, "Expert rejected the shutdown request")
                    return
                self._shutdown_approved.add(message.sender)
            self.mailbox.acknowledge(delivery)
            self._emit(
                message.message_type.value,
                agent_name=message.sender,
                message_id=message.message_id,
            )
            return
        self.mailbox.reject(delivery, f"Unexpected strategic mailbox message: {message.message_type.value}")

    def _answer_strategy_peer_request(self, delivery: MailboxDelivery) -> None:
        """Answer an expert's PEER_REQUEST addressed to the orchestrator."""

        message = delivery.message
        if not message.request_id:
            self.mailbox.reject(delivery, "Peer request is missing request_id")
            return
        handler = self._strategy_handler
        if handler is None:
            result: dict[str, JsonValue] = {
                "status": "failed",
                "error": "The strategic agent has no registered handler",
            }
        else:
            try:
                value = handler(message.payload, message.sender)
                json.dumps(value, ensure_ascii=False)
                result = {"status": "completed", "result": value}
            except Exception as exc:
                result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        self.mailbox.send(
            ProtocolMessage.create(
                context_id=self.run_id,
                sender=STRATEGIC_AGENT,
                recipient=message.sender,
                message_type=MessageType.PEER_RESPONSE,
                task_id=message.task_id,
                request_id=message.request_id,
                in_reply_to=message.message_id,
                payload=result,
                message_id=f"{message.message_id}:response",
                role=Role.ROLE_AGENT,
            )
        )
        self.mailbox.acknowledge(delivery)
        self._emit(
            "strategy_peer_response_sent",
            agent_name=STRATEGIC_AGENT,
            recipient=message.sender,
            task_id=message.task_id,
            request_id=message.request_id,
            status=result["status"],
        )

    def _claim_peer_response(self, requester: str, delivery: MailboxDelivery) -> bool:
        message = delivery.message
        if not message.request_id:
            self.mailbox.reject(delivery, "Peer response is missing request_id")
            return False
        with self._condition:
            pending = self._pending_peer_requests.get(message.request_id)
            if pending is None:
                rejection_reason = "Peer response does not match a pending request_id"
            elif pending.requester != requester:
                rejection_reason = "Peer response recipient does not match the requesting agent"
            elif message.sender != pending.responder:
                rejection_reason = "Peer response sender does not match the expected responder"
            elif message.in_reply_to != pending.request_message_id:
                rejection_reason = (
                    "Peer response in_reply_to does not match the peer request message_id"
                )
            else:
                rejection_reason = None
                self._pending_peer_requests.pop(message.request_id, None)
        if rejection_reason:
            self.mailbox.reject(delivery, rejection_reason)
            return False
        self.mailbox.acknowledge(delivery)
        return True

    def _drain_strategy_mailbox(self) -> None:
        while delivery := self.mailbox.receive(STRATEGIC_AGENT):
            self._consume_strategy_delivery(delivery)

    def _set_state(self, agent_name: str, state: ExpertState, **details: Any) -> None:
        with self._condition:
            self._states[agent_name] = state
            self._write_team_file_locked()
            self._condition.notify_all()
        self._emit("expert_state_changed", agent_name=agent_name, state=state.value, **details)

    def _emit(self, event_type: str, **details: Any) -> None:
        event = {
            "event_type": event_type,
            "run_id": self.run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **details,
        }
        json.dumps(event, ensure_ascii=False)
        with self._condition:
            self._events.append(event)
        if self._event_callback:
            try:
                self._event_callback(dict(event))
            except Exception:
                return

    def _write_team_file(self) -> None:
        with self._condition:
            self._write_team_file_locked()

    def _write_team_file_locked(self) -> None:
        atomic_write_json(
            self.run_dir / "team.json",
            {
                "run_id": self.run_id,
                "strategy_agent": STRATEGIC_AGENT,
                "experts": [
                    {
                        "agent_name": name,
                        "state": self._states[name].value,
                        "agent_card": agent_card_to_dict(self._cards[name]),
                    }
                    for name in sorted(self._cards)
                ],
            },
        )

    def _record_task_submitted(self, message: ProtocolMessage) -> None:
        task = Task(
            id=message.task_id,
            context_id=self.run_id,
            status=TaskStatus(
                state=TaskState.TASK_STATE_SUBMITTED,
                message=message.to_a2a_message(),
                timestamp=_timestamp_now(),
            ),
            history=[message.to_a2a_message()],
        )
        task.metadata.update({"assignedAgent": message.recipient})
        asyncio.run(self.task_store.save(task, self._task_context))

    def _record_task_working(self, message: ProtocolMessage) -> None:
        task = asyncio.run(self.task_store.get(message.task_id or "", self._task_context))
        if task is None:
            task = Task(id=message.task_id, context_id=self.run_id, history=[message.to_a2a_message()])
        task.status.CopyFrom(
            TaskStatus(
                state=TaskState.TASK_STATE_WORKING,
                message=message.to_a2a_message(),
                timestamp=_timestamp_now(),
            )
        )
        asyncio.run(self.task_store.save(task, self._task_context))

    def _record_task_result(
        self,
        request: ProtocolMessage,
        response: ProtocolMessage,
        result: dict[str, JsonValue],
    ) -> None:
        task = asyncio.run(self.task_store.get(request.task_id or "", self._task_context))
        if task is None:
            task = Task(id=request.task_id, context_id=self.run_id, history=[request.to_a2a_message()])
        state = (
            TaskState.TASK_STATE_COMPLETED
            if result["status"] == "completed"
            else TaskState.TASK_STATE_FAILED
        )
        task.status.CopyFrom(
            TaskStatus(
                state=state,
                message=response.to_a2a_message(),
                timestamp=_timestamp_now(),
            )
        )
        if not any(item.message_id == response.message_id for item in task.history):
            task.history.append(response.to_a2a_message())
        if not any(item.artifact_id == f"{request.task_id}:result" for item in task.artifacts):
            task.artifacts.append(
                ParseDict(
                    {
                        "artifactId": f"{request.task_id}:result",
                        "name": f"{request.task_id} expert result",
                        "parts": [{"data": result}],
                    },
                    Artifact(),
                )
            )
        asyncio.run(self.task_store.save(task, self._task_context))


class _ExpertWorker:
    def __init__(self, *, runtime: TeamRuntime, agent_name: str, handler: ExpertHandler) -> None:
        self.runtime = runtime
        self.agent_name = agent_name
        self.handler = handler
        self.thread = threading.Thread(
            target=self._run,
            name=f"a2a-expert-{runtime.run_id}-{agent_name}",
            daemon=False,
        )
        self._response_cache: dict[str, JsonValue] = {}

    def start(self) -> None:
        self.thread.start()

    def join(self, timeout: float) -> None:
        self.thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self.thread.is_alive()

    def wait_for_peer_response(self, request_id: str, *, timeout: float) -> JsonValue:
        deadline = time.monotonic() + timeout
        while True:
            if request_id in self._response_cache:
                return self._response_cache.pop(request_id)

            response_delivery = self.runtime.mailbox.receive(
                self.agent_name,
                lambda item: item.message_type == MessageType.PEER_RESPONSE and item.request_id == request_id,
            )
            if response_delivery is not None:
                if self.runtime._claim_peer_response(self.agent_name, response_delivery):
                    return response_delivery.message.payload
                continue

            peer_request = self.runtime.mailbox.receive(
                self.agent_name,
                lambda item: item.message_type == MessageType.PEER_REQUEST,
            )
            if peer_request is not None:
                self._process_peer_request(peer_request, nested=True)
                continue

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Timed out waiting for peer response: {request_id}")
            time.sleep(min(_RESPONSE_POLL_INTERVAL_SECONDS, remaining))

    def _run(self) -> None:
        try:
            self._load_identity()
            self.runtime._emit("expert_thread_started", agent_name=self.agent_name, daemon=False)
            self._become_idle()
            while True:
                delivery = self.runtime.mailbox.receive(self.agent_name)
                if delivery is None:
                    time.sleep(POLL_INTERVAL_SECONDS)
                    continue
                try:
                    if self._dispatch(delivery):
                        return
                except Exception as exc:
                    self.runtime._emit(
                        "mailbox_delivery_retry",
                        agent_name=self.agent_name,
                        message_id=delivery.message.message_id,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    time.sleep(POLL_INTERVAL_SECONDS)
        except BaseException as exc:
            self.runtime._set_state(self.agent_name, ExpertState.SHUTDOWN)
            self.runtime._emit(
                "expert_thread_failed",
                agent_name=self.agent_name,
                error=f"{type(exc).__name__}: {exc}",
            )
            self.runtime._emit("teammate_terminated", agent_name=self.agent_name)

    def _dispatch(self, delivery: MailboxDelivery) -> bool:
        message = delivery.message
        if message.message_type == MessageType.TASK:
            if not self.runtime._claim_task_request(message, self.agent_name):
                self.runtime.mailbox.reject(
                    delivery,
                    "Task message does not match a submitted task assigned to this expert",
                )
                return False
            self.runtime._set_state(self.agent_name, ExpertState.WORKING, task_id=message.task_id)
            self.runtime._record_task_working(message)
            if self._process_task(delivery):
                return True
            self._become_idle()
            return False
        if message.message_type == MessageType.PEER_REQUEST:
            self.runtime._set_state(self.agent_name, ExpertState.WORKING, task_id=message.task_id)
            if self._process_peer_request(delivery, nested=False):
                return True
            self._become_idle()
            return False
        if message.message_type == MessageType.PEER_RESPONSE:
            if self.runtime._claim_peer_response(self.agent_name, delivery):
                assert message.request_id is not None
                self._response_cache[message.request_id] = message.payload
            return False
        if message.message_type == MessageType.SHUTDOWN_REQUEST:
            return self._approve_shutdown(delivery)
        self.runtime.mailbox.reject(delivery, f"Unsupported expert message: {message.message_type.value}")
        return False

    def _process_task(self, delivery: MailboxDelivery) -> bool:
        message = delivery.message
        result = self._execute_delivery_handler(delivery)
        if result is None:
            self.runtime.mailbox.acknowledge(delivery)
            return True
        if not self.runtime._task_is_pending(message, self.agent_name):
            self.runtime.mailbox.acknowledge(delivery)
            self.runtime._emit(
                "late_task_result_ignored",
                agent_name=self.agent_name,
                task_id=message.task_id,
            )
            return False
        response = ProtocolMessage.create(
            context_id=self.runtime.run_id,
            sender=self.agent_name,
            recipient=STRATEGIC_AGENT,
            message_type=MessageType.RESULT,
            task_id=message.task_id,
            payload=result,
            in_reply_to=message.message_id,
            message_id=f"{message.message_id}:result",
        )
        self.runtime.mailbox.send(response)
        self.runtime._record_task_result(message, response, result)
        self.runtime.mailbox.acknowledge(delivery)
        self.runtime._emit(
            "expert_task_finished",
            agent_name=self.agent_name,
            task_id=message.task_id,
            status=result["status"],
        )
        return False

    def _execute_delivery_handler(
        self,
        delivery: MailboxDelivery,
        *,
        allow_shutdown: bool = True,
    ) -> dict[str, JsonValue] | None:
        """Run handler work behind a daemon boundary while renewing its claim."""

        result: list[dict[str, JsonValue]] = []
        finished = threading.Event()

        def invoke() -> None:
            try:
                result.append(self._execute_handler(delivery.message))
            except BaseException as exc:
                result.append(
                    {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
                )
            finally:
                finished.set()

        threading.Thread(
            target=invoke,
            name=(
                f"a2a-handler-{self.runtime.run_id}-{self.agent_name}-"
                f"{delivery.message.task_id}"
            ),
            daemon=True,
        ).start()
        active_delivery = delivery
        while not finished.wait(POLL_INTERVAL_SECONDS):
            active_delivery = self.runtime.mailbox.renew(active_delivery)
            if allow_shutdown:
                shutdown = self.runtime.mailbox.receive(
                    self.agent_name,
                    lambda item: item.message_type == MessageType.SHUTDOWN_REQUEST,
                )
                if shutdown is not None:
                    self._approve_shutdown(shutdown)
                    return None
        return result[0]

    def _approve_shutdown(self, delivery: MailboxDelivery) -> bool:
        message = delivery.message
        self.runtime.mailbox.send(
            ProtocolMessage.create(
                context_id=self.runtime.run_id,
                sender=self.agent_name,
                recipient=STRATEGIC_AGENT,
                message_type=MessageType.SHUTDOWN_APPROVED,
                payload={"status": "approved"},
                request_id=message.request_id,
                in_reply_to=message.message_id,
                message_id=f"{message.message_id}:approved",
            )
        )
        self.runtime.mailbox.acknowledge(delivery)
        self.runtime._set_state(self.agent_name, ExpertState.SHUTDOWN)
        self.runtime._emit("expert_thread_terminated", agent_name=self.agent_name)
        self.runtime._emit("teammate_terminated", agent_name=self.agent_name)
        return True

    def _process_peer_request(self, delivery: MailboxDelivery, *, nested: bool) -> bool:
        message = delivery.message
        if not message.request_id:
            self.runtime.mailbox.reject(delivery, "Peer request is missing request_id")
            return False
        result = self._execute_delivery_handler(
            delivery,
            allow_shutdown=not nested,
        )
        if result is None:
            self.runtime.mailbox.acknowledge(delivery)
            return True
        self.runtime.mailbox.send(
            ProtocolMessage.create(
                context_id=self.runtime.run_id,
                sender=self.agent_name,
                recipient=message.sender,
                message_type=MessageType.PEER_RESPONSE,
                task_id=message.task_id,
                request_id=message.request_id,
                in_reply_to=message.message_id,
                payload=result,
                message_id=f"{message.message_id}:response",
            )
        )
        self.runtime.mailbox.acknowledge(delivery)
        self.runtime._emit(
            "peer_response_sent",
            agent_name=self.agent_name,
            recipient=message.sender,
            task_id=message.task_id,
            request_id=message.request_id,
            nested=nested,
            status=result["status"],
        )
        return False

    def _execute_handler(self, message: ProtocolMessage) -> dict[str, JsonValue]:
        context = PeerContext(self.runtime, self, message)
        try:
            value = self.handler(message.payload, context)
            if inspect.isawaitable(value):
                value = asyncio.run(value)
            json.dumps(value, ensure_ascii=False)
            return {"status": "completed", "result": value}
        except Exception as exc:
            return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    def _become_idle(self) -> None:
        self.runtime.mailbox.send(
            ProtocolMessage.create(
                context_id=self.runtime.run_id,
                sender=self.agent_name,
                recipient=STRATEGIC_AGENT,
                message_type=MessageType.IDLE_NOTIFICATION,
                payload={"state": ExpertState.IDLE.value},
            )
        )
        self.runtime._set_state(self.agent_name, ExpertState.IDLE)

    def _load_identity(self) -> None:
        team = read_json(self.runtime.run_dir / "team.json")
        experts = team.get("experts", []) if isinstance(team, dict) else []
        if not any(
            isinstance(item, dict) and item.get("agent_name") == self.agent_name
            for item in experts
        ):
            raise ValueError(f"Expert is missing from team registry: {self.agent_name}")
        self.runtime._emit("expert_initialized", agent_name=self.agent_name)


def _timestamp_now() -> Timestamp:
    timestamp = Timestamp()
    timestamp.GetCurrentTime()
    return timestamp


def _assignment_module_id(payload: JsonValue) -> str | None:
    if not isinstance(payload, dict):
        return None
    assignment = payload.get("assignment")
    if not isinstance(assignment, dict):
        return None
    module_id = assignment.get("module_id")
    return module_id if isinstance(module_id, str) and module_id else None
