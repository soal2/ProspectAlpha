"""Process-local directory connecting A2A HTTP requests to mailbox runtimes."""

from __future__ import annotations

import threading
import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from riskagent.a2a.task_store import JsonTaskStore

if TYPE_CHECKING:
    from riskagent.a2a.runtime import TeamRuntime


@dataclass(slots=True)
class RuntimeRecord:
    run_id: str
    task_store: JsonTaskStore
    runtime_ref: weakref.ReferenceType["TeamRuntime"]
    active: bool = True
    agents: set[str] = field(default_factory=set)
    task_agents: dict[str, str] = field(default_factory=dict)

    def active_runtime(self) -> "TeamRuntime | None":
        return self.runtime_ref() if self.active else None


class RuntimeDirectory:
    """Tracks active mailbox runtimes and their durable task stores."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, RuntimeRecord] = {}

    def register(self, runtime: "TeamRuntime") -> None:
        with self._lock:
            self._records[runtime.run_id] = RuntimeRecord(
                run_id=runtime.run_id,
                task_store=runtime.task_store,
                runtime_ref=weakref.ref(runtime),
            )

    def register_expert(self, run_id: str, agent_name: str) -> None:
        with self._lock:
            self._records[run_id].agents.add(agent_name)

    def register_task(self, run_id: str, agent_name: str, task_id: str) -> None:
        with self._lock:
            record = self._records[run_id]
            record.agents.add(agent_name)
            record.task_agents[task_id] = agent_name

    def mark_inactive(self, run_id: str) -> None:
        with self._lock:
            record = self._records.get(run_id)
            if record:
                record.active = False

    def active_runtime(self, run_id: str, agent_name: str) -> "TeamRuntime | None":
        with self._lock:
            record = self._records.get(run_id)
            if record is None or agent_name not in record.agents:
                return None
            return record.active_runtime()

    def task_store_for_context(self, run_id: str, agent_name: str) -> JsonTaskStore | None:
        with self._lock:
            record = self._records.get(run_id)
            if record is None or agent_name not in record.agents:
                return None
            return record.task_store

    def task_store_for_task(self, agent_name: str, task_id: str) -> JsonTaskStore | None:
        with self._lock:
            matches = [
                record.task_store
                for record in self._records.values()
                if record.task_agents.get(task_id) == agent_name
            ]
        return matches[0] if len(matches) == 1 else None

    def task_stores_for_agent(self, agent_name: str) -> list[JsonTaskStore]:
        with self._lock:
            return [
                record.task_store
                for record in self._records.values()
                if agent_name in record.agents
            ]


runtime_directory = RuntimeDirectory()
