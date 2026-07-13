import asyncio
from datetime import datetime, timezone

from a2a.server.context import ServerCallContext
from a2a.types import Artifact, ListTasksRequest, Message, Part, Role, Task, TaskState, TaskStatus
from google.protobuf.timestamp_pb2 import Timestamp

from riskagent.a2a import JsonTaskStore


def _task(task_id: str, context_id: str, timestamp: datetime) -> Task:
    proto_timestamp = Timestamp()
    proto_timestamp.FromDatetime(timestamp)
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED, timestamp=proto_timestamp),
        artifacts=[Artifact(artifact_id=f"artifact-{task_id}", parts=[Part(text="result")])],
        history=[
            Message(
                message_id=f"message-{task_id}",
                context_id=context_id,
                task_id=task_id,
                role=Role.ROLE_AGENT,
                parts=[Part(text="history")],
            )
        ],
    )


def test_json_task_store_implements_sdk_contract_and_returns_copies(tmp_path):
    async def scenario():
        store = JsonTaskStore(tmp_path / "tasks", owner_resolver=lambda context: context.tenant)
        context = ServerCallContext(tenant="tenant-a")
        other_context = ServerCallContext(tenant="tenant-b")
        first = _task("task-1", "run-1", datetime(2026, 1, 1, tzinfo=timezone.utc))
        second = _task("task-2", "run-1", datetime(2026, 1, 2, tzinfo=timezone.utc))

        await store.save(first, context)
        await store.save(second, context)
        loaded = await store.get("task-1", context)
        assert loaded is not None
        loaded.status.state = TaskState.TASK_STATE_FAILED
        reloaded = await store.get("task-1", context)
        assert reloaded is not None
        assert reloaded.status.state == TaskState.TASK_STATE_COMPLETED
        assert await store.get("task-1", other_context) is None

        page = await store.list(
            ListTasksRequest(context_id="run-1", page_size=1, history_length=1),
            context,
        )
        assert page.total_size == 2
        assert page.page_size == 1
        assert page.tasks[0].id == "task-2"
        assert len(page.tasks[0].history) == 1
        assert len(page.tasks[0].artifacts) == 0
        assert page.next_page_token

        next_page = await store.list(
            ListTasksRequest(
                context_id="run-1",
                page_size=1,
                page_token=page.next_page_token,
                include_artifacts=True,
            ),
            context,
        )
        assert [task.id for task in next_page.tasks] == ["task-1"]
        assert len(next_page.tasks[0].artifacts) == 1

        await store.delete("task-1", context)
        assert await store.get("task-1", context) is None

    asyncio.run(scenario())


def test_json_task_store_atomically_reserves_one_task_per_message_id(tmp_path):
    async def scenario():
        store = JsonTaskStore(tmp_path / "tasks", owner_resolver=lambda _context: "run-1")
        context = ServerCallContext()
        candidates = [
            _task(
                f"candidate-{index}",
                "run-1",
                datetime(2026, 1, index + 1, tzinfo=timezone.utc),
            )
            for index in range(2)
        ]
        for candidate in candidates:
            candidate.history[0].message_id = "stable-client-message"
            candidate.metadata.update({"assignedAgent": "legal_agent"})

        reservations = await asyncio.gather(
            *(
                store.reserve_for_message(
                    candidate,
                    "stable-client-message",
                    context,
                    assigned_agent="legal_agent",
                )
                for candidate in candidates
            )
        )

        assert sum(created for _task_value, created in reservations) == 1
        assert len({task.id for task, _created in reservations}) == 1
        page = await store.list(
            ListTasksRequest(context_id="run-1", page_size=10),
            context,
        )
        assert page.total_size == 1

    asyncio.run(scenario())
