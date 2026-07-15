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


def test_peer_context_sustains_multi_round_debate_via_fresh_tasks(tmp_path):
    """Each debate round dispatches a fresh TASK so the per-task peer-round budget
    never trips even though the overall debate exceeds MAX_PEER_ROUNDS_PER_TASK.

    Simulates a 3-expert, 2-round debate: round 1 has each expert issue 2 peer
    critiques (one per peer), round 2 has each expert issue 2 revision requests.
    Each round is a separate ``assign_task`` call, so ``PeerContext`` budgets
    reset and the run completes cleanly.
    """

    runtime = TeamRuntime("run-debate-fresh-tasks", root_dir=tmp_path)
    experts = ["financial_agent", "legal_agent", "market_agent"]
    critique_log: list[tuple[str, str]] = []
    revision_log: list[tuple[str, str]] = []

    def make_handler(self_name: str):
        def handler(payload, peer):
            # Distinguish TASK invocations from PEER-REQUEST responses.
            # Tasks carry ``task_phase``; peer requests just echo back.
            task_phase = payload.get("task_phase")
            if task_phase is None:
                # We are responding to a peer request — echo so the requester unblocks.
                return {"responder": self_name, "echo": payload}

            # We are running as a TASK — issue peer requests to every other expert.
            responses = []
            for other in experts:
                if other == self_name:
                    continue
                response = peer.request(
                    other,
                    {"from": self_name, "to": other, "kind": task_phase},
                    timeout=3.0,
                )
                responses.append(response)
                if task_phase == "critique":
                    critique_log.append((self_name, other))
                else:
                    revision_log.append((self_name, other))
            return {"task_phase": task_phase, "responses": responses}

        return handler

    for name in experts:
        runtime.start_expert(name, make_handler(name))
    assert runtime.wait_until_idle(experts, timeout=2.0)

    # Round 1: each expert issues a critique task → 3 tasks, each with N-1 = 2 peer requests.
    for name in experts:
        runtime.assign_task(name, f"{name}-critique", {"task_phase": "critique"})
    critique_results = runtime.collect_results(
        [f"{name}-critique" for name in experts], timeout=8.0
    )
    assert all(
        critique_results[f"{name}-critique"]["status"] == "completed"
        for name in experts
    )

    # Round 2: each expert issues a revision task → fresh per-task peer budget.
    for name in experts:
        runtime.assign_task(name, f"{name}-revision", {"task_phase": "revision"})
    revision_results = runtime.collect_results(
        [f"{name}-revision" for name in experts], timeout=8.0
    )
    assert all(
        revision_results[f"{name}-revision"]["status"] == "completed"
        for name in experts
    )

    # 3 experts × 2 peers = 6 critiques and 6 revision-direction messages each direction.
    assert len(critique_log) == 6
    assert len(revision_log) == 6
    runtime.shutdown_all(timeout=3.0)


def test_expert_peer_request_to_strategic_agent_is_answered_by_handler(tmp_path):
    runtime = TeamRuntime("run-strategy-qa", root_dir=tmp_path)
    runtime.set_strategy_handler(
        lambda payload, sender: {
            "kind": payload["kind"],
            "sender_seen": sender,
            "guidance": "keep evidence discipline",
        }
    )

    def expert_handler(payload, peer):
        peer_names = {profile["name"] for profile in peer.available_peers}
        assert STRATEGIC_AGENT in peer_names
        guidance = peer.request(
            STRATEGIC_AGENT,
            {"kind": "needs_more_evidence", "rule_ids": ["R1"]},
            timeout=5.0,
        )
        return {"guidance": guidance}

    runtime.start_expert("financial_agent", expert_handler)
    assert runtime.wait_until_idle(["financial_agent"], timeout=2.0)
    runtime.assign_task("financial_agent", "fin-task", {"module": "financial"})

    result = runtime.collect_results(["fin-task"], timeout=8.0)["fin-task"]
    assert result["status"] == "completed"
    assert result["result"]["guidance"] == {
        "kind": "needs_more_evidence",
        "sender_seen": "financial_agent",
        "guidance": "keep evidence discipline",
    }
    strategy_events = [
        event
        for event in runtime.events
        if event["event_type"] == "strategy_peer_response_sent"
    ]
    assert strategy_events and strategy_events[0]["status"] == "completed"
    assert strategy_events[0]["recipient"] == "financial_agent"
    runtime.shutdown_all(timeout=3.0)


def test_strategy_handler_exception_surfaces_as_peer_request_error(tmp_path):
    runtime = TeamRuntime("run-strategy-fail", root_dir=tmp_path)

    def broken_handler(payload, sender):
        raise RuntimeError("planner unavailable")

    runtime.set_strategy_handler(broken_handler)

    def expert_handler(payload, peer):
        try:
            peer.request(STRATEGIC_AGENT, {"kind": "plan_status"}, timeout=5.0)
        except PeerRequestError as exc:
            return {"error": str(exc)}
        raise AssertionError("handler failure should surface as PeerRequestError")

    runtime.start_expert("legal_agent", expert_handler)
    assert runtime.wait_until_idle(["legal_agent"], timeout=2.0)
    runtime.assign_task("legal_agent", "legal-task", {})

    result = runtime.collect_results(["legal-task"], timeout=8.0)["legal-task"]
    assert result["status"] == "completed"
    assert "RuntimeError: planner unavailable" in result["result"]["error"]
    strategy_events = [
        event
        for event in runtime.events
        if event["event_type"] == "strategy_peer_response_sent"
    ]
    assert strategy_events and strategy_events[0]["status"] == "failed"
    runtime.shutdown_all(timeout=3.0)


def test_missing_strategy_handler_returns_failed_peer_response(tmp_path):
    runtime = TeamRuntime("run-strategy-nohandler", root_dir=tmp_path)

    def expert_handler(payload, peer):
        try:
            peer.request(STRATEGIC_AGENT, {"kind": "plan_status"}, timeout=5.0)
        except PeerRequestError as exc:
            return {"error": str(exc)}
        raise AssertionError("missing handler should surface as PeerRequestError")

    runtime.start_expert("market_agent", expert_handler)
    assert runtime.wait_until_idle(["market_agent"], timeout=2.0)
    runtime.assign_task("market_agent", "market-task", {})

    result = runtime.collect_results(["market-task"], timeout=8.0)["market-task"]
    assert result["status"] == "completed"
    assert "no registered handler" in result["result"]["error"]
    runtime.shutdown_all(timeout=3.0)


def test_broadcast_and_read_broadcast_responses_round_trip(tmp_path):
    runtime = TeamRuntime("run-broadcast", root_dir=tmp_path)
    experts = ["financial_agent", "legal_agent"]

    def make_handler(self_name: str):
        def handler(payload, peer):
            if peer.message_type == MessageType.PEER_REQUEST:
                return {"agent": self_name, "ack": payload["kind"]}
            return {"agent": self_name}

        return handler

    for name in experts:
        runtime.start_expert(name, make_handler(name))
    assert runtime.wait_until_idle(experts, timeout=2.0)

    request_ids = runtime.broadcast({"kind": "mission_brief"})
    assert set(request_ids) == set(experts)
    assert len(set(request_ids.values())) == len(experts)

    responses = runtime.read_broadcast_responses(request_ids, timeout=5.0)
    assert set(responses) == set(experts)
    for name, payload in responses.items():
        assert payload["status"] == "completed"
        assert payload["result"] == {"agent": name, "ack": "mission_brief"}

    event_types = [event["event_type"] for event in runtime.events]
    assert "strategy_broadcast_sent" in event_types
    assert event_types.count("strategy_broadcast_response") == 2
    runtime.shutdown_all(timeout=3.0)


def test_read_broadcast_responses_returns_partial_on_timeout(tmp_path):
    runtime = TeamRuntime("run-broadcast-partial", root_dir=tmp_path)

    def fast_handler(payload, peer):
        return {"agent": "fast_agent"}

    def slow_handler(payload, peer):
        time.sleep(2.0)
        return {"agent": "slow_agent"}

    runtime.start_expert("fast_agent", fast_handler)
    runtime.start_expert("slow_agent", slow_handler)
    assert runtime.wait_until_idle(["fast_agent", "slow_agent"], timeout=2.0)

    request_ids = runtime.broadcast({"kind": "mission_brief"})
    responses = runtime.read_broadcast_responses(request_ids, timeout=0.8)
    assert set(responses) == {"fast_agent"}
    runtime.shutdown_all(timeout=6.0)


def test_peer_profiles_expose_strategic_agent_card(tmp_path, monkeypatch):
    monkeypatch.setenv("A2A_PUBLIC_BASE_URL", "https://agents.example.test")
    runtime = TeamRuntime("run-profiles", root_dir=tmp_path)
    runtime.start_expert("financial_agent", lambda payload, peer: {})
    assert runtime.wait_until_idle(["financial_agent"], timeout=2.0)

    names = [profile["name"] for profile in runtime.peer_profiles()]
    assert "financial_agent" in names
    assert STRATEGIC_AGENT in names

    excluded = [
        profile["name"]
        for profile in runtime.peer_profiles(excluding=STRATEGIC_AGENT)
    ]
    assert STRATEGIC_AGENT not in excluded

    strategy_card = next(
        profile for profile in runtime.peer_profiles() if profile["name"] == STRATEGIC_AGENT
    )
    assert strategy_card["supportedInterfaces"][0]["url"] == (
        f"https://agents.example.test/a2a/{STRATEGIC_AGENT}"
    )

    with pytest.raises(TypeError):
        runtime.set_strategy_handler("not-callable")
    runtime.shutdown_all(timeout=3.0)
