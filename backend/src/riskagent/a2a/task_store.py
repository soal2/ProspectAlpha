"""Durable JSON implementation of the official A2A SDK TaskStore."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.server.tasks import TaskStore
from a2a.types import ListTasksRequest, ListTasksResponse, Task
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE
from a2a.utils.errors import InvalidParamsError
from a2a.utils.task import decode_page_token, encode_page_token
from google.protobuf.json_format import MessageToDict, ParseDict

from riskagent.a2a.storage import (
    atomic_write_json,
    ensure_private_directory,
    exclusive_file_lock,
    read_json,
)


class JsonTaskStore(TaskStore):
    """A process-safe TaskStore whose canonical records are official Task JSON."""

    def __init__(
        self,
        root_dir: str | Path,
        *,
        owner_resolver: OwnerResolver = resolve_user_scope,
    ) -> None:
        self.root_dir = ensure_private_directory(Path(root_dir))
        self._owners_dir = ensure_private_directory(self.root_dir / "owners")
        self._lock_path = self.root_dir / ".task-store.lock"
        self._owner_resolver = owner_resolver

    async def save(self, task: Task, context: ServerCallContext) -> None:
        if not task.id:
            raise ValueError("A2A Task.id is required")
        owner = self._owner_resolver(context)
        await asyncio.to_thread(self._save_sync, task, owner)

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        owner = self._owner_resolver(context)
        return await asyncio.to_thread(self._get_sync, task_id, owner)

    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        owner = self._owner_resolver(context)
        return await asyncio.to_thread(self._list_sync, params, owner)

    async def list_for_agent(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
        agent_name: str,
    ) -> ListTasksResponse:
        owner = self._owner_resolver(context)
        return await asyncio.to_thread(
            self._list_sync,
            params,
            owner,
            agent_name,
        )

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        owner = self._owner_resolver(context)
        await asyncio.to_thread(self._delete_sync, task_id, owner)

    async def reserve_for_message(
        self,
        task: Task,
        message_id: str,
        context: ServerCallContext,
        *,
        assigned_agent: str,
    ) -> tuple[Task, bool]:
        """Atomically reserve one Task for a client message idempotency key."""

        owner = self._owner_resolver(context)
        return await asyncio.to_thread(
            self._reserve_for_message_sync,
            task,
            message_id,
            owner,
            assigned_agent,
        )

    def _save_sync(self, task: Task, owner: str) -> None:
        with exclusive_file_lock(self._lock_path):
            owner_dir = ensure_private_directory(self._owner_dir(owner))
            atomic_write_json(
                owner_dir / f"{_digest(task.id)}.json",
                {"owner": owner, "task": MessageToDict(task)},
            )

    def _get_sync(self, task_id: str, owner: str) -> Task | None:
        with exclusive_file_lock(self._lock_path):
            return self._read_task(self._task_path(owner, task_id), expected_owner=owner)

    def _list_sync(
        self,
        params: ListTasksRequest,
        owner: str,
        assigned_agent: str | None = None,
    ) -> ListTasksResponse:
        with exclusive_file_lock(self._lock_path):
            owner_dir = self._owner_dir(owner)
            tasks = [] if not owner_dir.exists() else [
                task
                for path in owner_dir.glob("*.json")
                if (task := self._read_task(path, expected_owner=owner)) is not None
            ]

        if params.context_id:
            tasks = [task for task in tasks if task.context_id == params.context_id]
        if assigned_agent:
            tasks = [
                task
                for task in tasks
                if MessageToDict(task.metadata).get("assignedAgent") == assigned_agent
            ]
        if params.status:
            tasks = [task for task in tasks if task.status.state == params.status]
        if params.HasField("status_timestamp_after"):
            threshold = params.status_timestamp_after.ToJsonString()
            tasks = [
                task
                for task in tasks
                if task.HasField("status")
                and task.status.HasField("timestamp")
                and task.status.timestamp.ToJsonString() >= threshold
            ]

        tasks.sort(key=_task_sort_key, reverse=True)
        total_size = len(tasks)
        start_index = _page_start(tasks, params.page_token)
        page_size = params.page_size or DEFAULT_LIST_TASKS_PAGE_SIZE
        if page_size < 1:
            raise InvalidParamsError("page_size must be positive")
        end_index = start_index + page_size
        next_page_token = encode_page_token(tasks[end_index].id) if end_index < total_size else ""
        page = [self._list_copy(task, params) for task in tasks[start_index:end_index]]
        return ListTasksResponse(
            tasks=page,
            next_page_token=next_page_token,
            page_size=page_size,
            total_size=total_size,
        )

    def _delete_sync(self, task_id: str, owner: str) -> None:
        with exclusive_file_lock(self._lock_path):
            self._task_path(owner, task_id).unlink(missing_ok=True)

    def _reserve_for_message_sync(
        self,
        task: Task,
        message_id: str,
        owner: str,
        assigned_agent: str,
    ) -> tuple[Task, bool]:
        with exclusive_file_lock(self._lock_path):
            owner_dir = ensure_private_directory(self._owner_dir(owner))
            matches = [
                existing
                for path in owner_dir.glob("*.json")
                if (existing := self._read_task(path, expected_owner=owner)) is not None
                and any(item.message_id == message_id for item in existing.history)
                and MessageToDict(existing.metadata).get("assignedAgent")
                == assigned_agent
            ]
            if len(matches) > 1:
                raise ValueError(
                    f"Message id is associated with multiple tasks: {message_id}"
                )
            if matches:
                return matches[0], False
            if not any(item.message_id == message_id for item in task.history):
                raise ValueError("Reserved task history must contain the client message id")
            if MessageToDict(task.metadata).get("assignedAgent") != assigned_agent:
                raise ValueError("Reserved task assignedAgent does not match")
            atomic_write_json(
                owner_dir / f"{_digest(task.id)}.json",
                {"owner": owner, "task": MessageToDict(task)},
            )
            return task, True

    def _read_task(self, path: Path, *, expected_owner: str) -> Task | None:
        value = read_json(path)
        if value is None:
            return None
        if not isinstance(value, dict) or value.get("owner") != expected_owner:
            raise ValueError(f"Invalid task record: {path}")
        task_value = value.get("task")
        if not isinstance(task_value, dict):
            raise ValueError(f"Invalid task record: {path}")
        return ParseDict(task_value, Task())

    def _list_copy(self, task: Task, params: ListTasksRequest) -> Task:
        copied = Task()
        copied.CopyFrom(task)
        if not params.include_artifacts:
            del copied.artifacts[:]
        history_length = params.history_length
        if history_length > 0:
            history = list(copied.history[-history_length:])
            del copied.history[:]
            copied.history.extend(history)
        else:
            del copied.history[:]
        return copied

    def _owner_dir(self, owner: str) -> Path:
        return self._owners_dir / _digest(owner)

    def _task_path(self, owner: str, task_id: str) -> Path:
        return self._owner_dir(owner) / f"{_digest(task_id)}.json"


def _task_sort_key(task: Task) -> tuple[bool, str, str]:
    has_timestamp = task.HasField("status") and task.status.HasField("timestamp")
    timestamp = task.status.timestamp.ToJsonString() if has_timestamp else ""
    return has_timestamp, timestamp, task.id


def _page_start(tasks: list[Task], token: str) -> int:
    if not token:
        return 0
    task_id = decode_page_token(token)
    for index, task in enumerate(tasks):
        if task.id == task_id:
            return index
    raise InvalidParamsError(f"Invalid page token: {token}")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
