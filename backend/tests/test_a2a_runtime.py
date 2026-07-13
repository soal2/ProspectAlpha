import asyncio
import json
import threading
import time

import pytest
from a2a.server.context import ServerCallContext
from a2a.types import TaskState

from riskagent.a2a import (
    ExpertState,
    MessageType,
    PeerContext,
    PeerRequestError,
    ProtocolMessage,
    STRATEGIC_AGENT,
    TeamRuntime,
    agent_card_to_dict,
)


def _dead_letters(runtime, recipient):
    path = runtime.mailbox.dead_letter_path(recipient)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _wait_for_dead_letters(runtime, recipient, expected_count, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        entries = _dead_letters(runtime, recipient)
        if len(entries) >= expected_count:
            return entries
        time.sleep(0.02)
    return _dead_letters(runtime, recipient)


def test_team_runtime_lifecycle_task_result_and_shutdown(tmp_path, monkeypatch):
    monkeypatch.setenv("A2A_PUBLIC_BASE_URL", "https://agents.example.test/base/")
    callback_events = []
    runtime = TeamRuntime("run-lifecycle", root_dir=tmp_path, event_callback=callback_events.append)

    def handler(payload, peer):
        assert isinstance(peer, PeerContext)
        return {"module": payload["module"], "finding_count": 2}

    card = runtime.start_expert(
        "legal_agent",
        handler,
        role="Legal risk analyst",
        capabilities=["litigation and regulatory review"],
    )
    assert runtime.thread_for("legal_agent").daemon is False
    assert runtime.wait_until_idle(["legal_agent"], timeout=2.0)
    assert runtime.state("legal_agent") == ExpertState.IDLE
    assert agent_card_to_dict(card)["skills"][0]["name"] == "litigation and regulatory review"
    assert agent_card_to_dict(card)["supportedInterfaces"][0]["url"] == (
        "https://agents.example.test/base/a2a/legal_agent"
    )
    assert runtime.peer_profiles()[0]["supportedInterfaces"][0]["url"] == (
        "https://agents.example.test/base/a2a/legal_agent"
    )
    team = json.loads((runtime.run_dir / "team.json").read_text(encoding="utf-8"))
    assert team["experts"][0]["agent_card"]["supportedInterfaces"][0]["url"] == (
        "https://agents.example.test/base/a2a/legal_agent"
    )

    runtime.assign_task(
        "legal_agent",
        "legal-module",
        {"module": "legal", "assignment": {"module_id": "legal-risk"}},
    )
    results = runtime.collect_results(["legal-module"], timeout=3.0)
    assert results == {
        "legal-module": {
            "status": "completed",
            "result": {"module": "legal", "finding_count": 2.0},
        }
    }
    assert runtime.wait_until_idle(["legal_agent"], timeout=2.0)

    stored_task = asyncio.run(runtime.task_store.get("legal-module", ServerCallContext()))
    assert stored_task is not None
    assert stored_task.status.state == TaskState.TASK_STATE_COMPLETED
    assert len(stored_task.artifacts) == 1
    assert len(stored_task.history) == 2

    runtime.shutdown_all(timeout=3.0)
    assert runtime.state("legal_agent") == ExpertState.SHUTDOWN
    assert not runtime.thread_for("legal_agent").is_alive()
    event_types = {event["event_type"] for event in callback_events}
    assert {
        "expert_created",
        "expert_state_changed",
        "task_assigned",
        "expert_task_finished",
        "task_result_received",
        "teammate_terminated",
    }.issubset(event_types)
    task_events = {
        event["event_type"]: event
        for event in callback_events
        if event["event_type"] in {"task_assigned", "task_result_received"}
    }
    assert task_events["task_assigned"]["module_id"] == "legal-risk"
    assert task_events["task_result_received"]["module_id"] == "legal-risk"


def test_runtime_uses_shared_runtime_root_resolver_by_default(tmp_path, monkeypatch):
    runtime_root = tmp_path / "shared-runtime"
    monkeypatch.setenv("AGENT_RUNTIME_DATA_DIR", str(runtime_root))
    runtime = TeamRuntime("run-shared-root")

    assert runtime.run_dir == runtime_root / "run-shared-root"
    runtime.shutdown_all(timeout=1.0)


def test_only_one_team_runtime_can_own_a_run_directory(tmp_path):
    runtime = TeamRuntime("run-exclusive", root_dir=tmp_path)
    with pytest.raises(RuntimeError, match="already active"):
        TeamRuntime("run-exclusive", root_dir=tmp_path)

    runtime.shutdown_all(timeout=1.0)
    replacement = TeamRuntime("run-exclusive", root_dir=tmp_path)
    replacement.shutdown_all(timeout=1.0)


def test_peer_requests_are_correlated_when_read_out_of_order(tmp_path):
    runtime = TeamRuntime("run-peer", root_dir=tmp_path)

    def financial_handler(payload, peer):
        first_id = peer.send("legal_agent", {"sequence": 1})
        second_id = peer.send("legal_agent", {"sequence": 2})
        second = peer.read(second_id, timeout=3.0)
        first = peer.read(first_id, timeout=3.0)
        return {"first": first, "second": second}

    def legal_handler(payload, peer):
        assert peer.message_type == MessageType.PEER_REQUEST
        return {"sequence": payload["sequence"], "responder": peer.agent_name}

    runtime.start_expert("financial_agent", financial_handler)
    runtime.start_expert("legal_agent", legal_handler)
    assert runtime.wait_until_idle(["financial_agent", "legal_agent"], timeout=2.0)

    runtime.assign_task("financial_agent", "financial-module", {"module": "financial"})
    results = runtime.collect_results(["financial-module"], timeout=5.0)
    assert results["financial-module"]["status"] == "completed"
    assert results["financial-module"]["result"] == {
        "first": {"sequence": 1.0, "responder": "legal_agent"},
        "second": {"sequence": 2.0, "responder": "legal_agent"},
    }

    peer_events = [event for event in runtime.events if event["event_type"] == "peer_response_sent"]
    assert len(peer_events) == 2
    assert len({event["request_id"] for event in peer_events}) == 2
    runtime.shutdown_all(timeout=3.0)


def test_task_result_requires_assigned_agent_and_original_message_id(tmp_path):
    runtime = TeamRuntime("run-task-correlation", root_dir=tmp_path)
    handler_started = threading.Event()
    allow_result = threading.Event()

    def legal_handler(payload, peer):
        handler_started.set()
        if not allow_result.wait(3.0):
            raise TimeoutError("test did not release the legal handler")
        return {"source": "legal_agent"}

    runtime.start_expert("legal_agent", legal_handler)
    assert runtime.wait_until_idle(["legal_agent"], timeout=2.0)
    task_message_id = runtime.assign_task(
        "legal_agent",
        "legal-module",
        {},
        message_id="legal-task-message",
    )
    assert handler_started.wait(2.0)

    runtime.mailbox.send(
        ProtocolMessage.create(
            context_id=runtime.run_id,
            sender="market_agent",
            recipient=STRATEGIC_AGENT,
            message_type=MessageType.RESULT,
            task_id="legal-module",
            in_reply_to=task_message_id,
            message_id="forged-result-wrong-agent",
            payload={"status": "completed", "result": {"source": "forged-agent"}},
        )
    )
    runtime.mailbox.send(
        ProtocolMessage.create(
            context_id=runtime.run_id,
            sender="legal_agent",
            recipient=STRATEGIC_AGENT,
            message_type=MessageType.RESULT,
            task_id="legal-module",
            in_reply_to="wrong-task-message",
            message_id="forged-result-wrong-reply",
            payload={"status": "completed", "result": {"source": "forged-reply"}},
        )
    )
    allow_result.set()

    result = runtime.collect_results(["legal-module"], timeout=3.0)["legal-module"]
    assert result == {"status": "completed", "result": {"source": "legal_agent"}}

    runtime.mailbox.send(
        ProtocolMessage.create(
            context_id=runtime.run_id,
            sender="legal_agent",
            recipient=STRATEGIC_AGENT,
            message_type=MessageType.RESULT,
            task_id="legal-module",
            in_reply_to=task_message_id,
            message_id="forged-result-after-completion",
            payload={"status": "completed", "result": {"source": "late-forgery"}},
        )
    )
    runtime._drain_strategy_mailbox()
    assert runtime.collect_results(["legal-module"], timeout=0.1)["legal-module"] == result

    dead_letters = _dead_letters(runtime, STRATEGIC_AGENT)
    errors = [entry["error"] for entry in dead_letters]
    assert "Result sender does not match the assigned agent" in errors
    assert "Result in_reply_to does not match the task message_id" in errors
    assert "Result does not match a pending task" in errors
    runtime.shutdown_all(timeout=3.0)


def test_peer_response_requires_request_responder_and_original_message_id(tmp_path):
    runtime = TeamRuntime("run-peer-correlation", root_dir=tmp_path)
    peer_request_sent = threading.Event()
    legal_handler_started = threading.Event()
    allow_peer_read = threading.Event()
    allow_legal_response = threading.Event()
    request_ids = {}

    def financial_handler(payload, peer):
        request_id = peer.send("legal_agent", {"review": "financial"})
        request_ids["request_id"] = request_id
        peer_request_sent.set()
        if not allow_peer_read.wait(3.0):
            raise TimeoutError("test did not release peer response reading")
        return {"peer": peer.read(request_id, timeout=3.0)}

    def legal_handler(payload, peer):
        legal_handler_started.set()
        if not allow_legal_response.wait(3.0):
            raise TimeoutError("test did not release the legal response")
        return {"source": "legal_agent"}

    runtime.start_expert("financial_agent", financial_handler)
    runtime.start_expert("legal_agent", legal_handler)
    assert runtime.wait_until_idle(["financial_agent", "legal_agent"], timeout=2.0)
    runtime.assign_task("financial_agent", "financial-module", {})
    assert peer_request_sent.wait(2.0)
    assert legal_handler_started.wait(2.0)

    peer_event = next(
        event
        for event in runtime.events
        if event["event_type"] == "peer_request_sent"
        and event["request_id"] == request_ids["request_id"]
    )
    request_id = request_ids["request_id"]
    request_message_id = peer_event["message_id"]
    forged_payload = {"status": "completed", "result": {"source": "forged"}}
    runtime.mailbox.send(
        ProtocolMessage.create(
            context_id=runtime.run_id,
            sender="market_agent",
            recipient="financial_agent",
            message_type=MessageType.PEER_RESPONSE,
            task_id="financial-module",
            request_id=request_id,
            in_reply_to=request_message_id,
            message_id="forged-peer-wrong-responder",
            payload=forged_payload,
        )
    )
    runtime.mailbox.send(
        ProtocolMessage.create(
            context_id=runtime.run_id,
            sender="legal_agent",
            recipient="financial_agent",
            message_type=MessageType.PEER_RESPONSE,
            task_id="financial-module",
            request_id=request_id,
            in_reply_to="wrong-peer-request-message",
            message_id="forged-peer-wrong-reply",
            payload=forged_payload,
        )
    )
    runtime.mailbox.send(
        ProtocolMessage.create(
            context_id=runtime.run_id,
            sender="legal_agent",
            recipient="financial_agent",
            message_type=MessageType.PEER_RESPONSE,
            task_id="financial-module",
            request_id="wrong-request-id",
            in_reply_to=request_message_id,
            message_id="forged-peer-wrong-request",
            payload=forged_payload,
        )
    )

    allow_peer_read.set()
    assert len(_wait_for_dead_letters(runtime, "financial_agent", 2)) >= 2
    allow_legal_response.set()
    result = runtime.collect_results(["financial-module"], timeout=5.0)["financial-module"]
    assert result == {
        "status": "completed",
        "result": {"peer": {"source": "legal_agent"}},
    }

    dead_letters = _wait_for_dead_letters(runtime, "financial_agent", 3)
    errors = [entry["error"] for entry in dead_letters]
    assert "Peer response sender does not match the expected responder" in errors
    assert "Peer response in_reply_to does not match the peer request message_id" in errors
    assert "Peer response does not match a pending request_id" in errors
    runtime.shutdown_all(timeout=3.0)


def test_handler_failure_is_returned_as_structured_result(tmp_path):
    runtime = TeamRuntime("run-failure", root_dir=tmp_path)

    def handler(payload, peer):
        raise RuntimeError("analysis unavailable")

    runtime.start_expert("market_agent", handler)
    assert runtime.wait_until_idle(["market_agent"], timeout=2.0)
    runtime.assign_task("market_agent", "market-module", {})

    result = runtime.collect_results(["market-module"], timeout=3.0)["market-module"]
    assert result == {"status": "failed", "error": "RuntimeError: analysis unavailable"}
    runtime.shutdown_all(timeout=3.0)


def test_timed_out_task_is_failed_and_worker_can_shutdown(tmp_path):
    runtime = TeamRuntime("run-timeout", root_dir=tmp_path)
    handler_started = threading.Event()
    release_handler = threading.Event()

    def handler(payload, peer):
        handler_started.set()
        release_handler.wait(5.0)
        return {"late": True}

    runtime.start_expert("legal_agent", handler)
    assert runtime.wait_until_idle(["legal_agent"], timeout=2.0)
    runtime.assign_task("legal_agent", "timeout-task", {})
    assert handler_started.wait(2.0)

    with pytest.raises(TimeoutError):
        runtime.collect_results(["timeout-task"], timeout=0.05)
    assert runtime.fail_task("timeout-task", "expert result timeout") is True
    stored = asyncio.run(
        runtime.task_store.get("timeout-task", ServerCallContext())
    )
    assert stored is not None
    assert stored.status.state == TaskState.TASK_STATE_FAILED

    runtime.shutdown_all(timeout=2.0)
    assert not runtime.thread_for("legal_agent").is_alive()
    release_handler.set()


def test_peer_requests_are_limited_to_three_rounds_per_task(tmp_path):
    runtime = TeamRuntime("run-peer-limit", root_dir=tmp_path)

    def financial_handler(payload, peer):
        responses = [
            peer.request("legal_agent", {"sequence": index}, timeout=3.0)
            for index in range(3)
        ]
        try:
            peer.send("legal_agent", {"sequence": 4})
        except PeerRequestError as exc:
            return {"responses": responses, "limit_error": str(exc)}
        raise AssertionError("fourth peer round should not be accepted")

    def legal_handler(payload, peer):
        return payload["sequence"]

    runtime.start_expert("financial_agent", financial_handler)
    runtime.start_expert("legal_agent", legal_handler)
    assert runtime.wait_until_idle(["financial_agent", "legal_agent"], timeout=2.0)
    runtime.assign_task("financial_agent", "financial-module", {})

    result = runtime.collect_results(["financial-module"], timeout=8.0)["financial-module"]
    assert result["status"] == "completed"
    assert result["result"]["responses"] == [0.0, 1.0, 2.0]
    assert "at most 3 peer requests" in result["result"]["limit_error"]
    runtime.shutdown_all(timeout=3.0)
