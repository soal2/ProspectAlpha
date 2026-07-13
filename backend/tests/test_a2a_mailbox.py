import json
import multiprocessing
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from riskagent.a2a import JsonMailbox, MessageType, ProtocolMessage


def _message(run_id: str, index: int, *, message_id: str | None = None) -> ProtocolMessage:
    return ProtocolMessage.create(
        context_id=run_id,
        sender="strategic_agent",
        recipient="legal_agent",
        message_type=MessageType.TASK,
        task_id=f"task-{index}",
        payload={"index": index},
        message_id=message_id,
    )


def _claim_in_process(run_dir, ready_queue, start_event, result_queue, consumer_id):
    mailbox = JsonMailbox(
        run_dir,
        run_id="run-1",
        consumer_id=consumer_id,
        claim_lease_seconds=10,
    )
    ready_queue.put(consumer_id)
    start_event.wait(timeout=5)
    delivery = mailbox.receive("legal_agent")
    result_queue.put(
        (
            consumer_id,
            delivery.message.message_id if delivery is not None else None,
        )
    )


def test_json_mailbox_keeps_all_concurrent_writes(tmp_path):
    mailbox = JsonMailbox(tmp_path / "run", run_id="run-1")
    messages = [_message("run-1", index) for index in range(200)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(mailbox.send, messages))

    received = []
    while delivery := mailbox.receive("legal_agent"):
        received.append(delivery.message)
        mailbox.acknowledge(delivery)

    assert {message.message_id for message in received} == {
        message.message_id for message in messages
    }
    assert len(received) == 200


def test_json_mailbox_deduplicates_repeated_message_id(tmp_path):
    mailbox = JsonMailbox(tmp_path / "run", run_id="run-1")
    message = _message("run-1", 1, message_id="stable-message-id")
    mailbox.send(message)
    mailbox.send(message)

    delivery = mailbox.receive("legal_agent")
    assert delivery is not None
    mailbox.acknowledge(delivery)

    assert mailbox.receive("legal_agent") is None
    state = json.loads(mailbox.cursor_path("legal_agent").read_text(encoding="utf-8"))
    assert state["processed_ids"] == ["stable-message-id"]
    assert state["offset"] == mailbox.inbox_path("legal_agent").stat().st_size


def test_json_mailbox_dead_letters_invalid_lines_and_continues(tmp_path):
    mailbox = JsonMailbox(tmp_path / "run", run_id="run-1")
    mailbox.register("legal_agent")
    with mailbox.inbox_path("legal_agent").open("ab") as handle:
        handle.write(b"not-json\n")
    mailbox.send(_message("run-1", 2))

    delivery = mailbox.receive("legal_agent")
    assert delivery is not None
    assert delivery.message.task_id == "task-2"
    mailbox.acknowledge(delivery)

    records = [
        json.loads(line)
        for line in mailbox.dead_letter_path("legal_agent").read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["raw"] == "not-json"


def test_json_mailbox_can_ack_correlated_response_out_of_order(tmp_path):
    mailbox = JsonMailbox(tmp_path / "run", run_id="run-1")
    first = ProtocolMessage.create(
        context_id="run-1",
        sender="legal_agent",
        recipient="financial_agent",
        message_type=MessageType.PEER_RESPONSE,
        request_id="request-1",
        payload={"result": 1},
    )
    second = ProtocolMessage.create(
        context_id="run-1",
        sender="legal_agent",
        recipient="financial_agent",
        message_type=MessageType.PEER_RESPONSE,
        request_id="request-2",
        payload={"result": 2},
    )
    mailbox.send(first)
    mailbox.send(second)

    second_delivery = mailbox.receive(
        "financial_agent", lambda message: message.request_id == "request-2"
    )
    assert second_delivery is not None
    mailbox.acknowledge(second_delivery)
    first_delivery = mailbox.receive(
        "financial_agent", lambda message: message.request_id == "request-1"
    )
    assert first_delivery is not None
    mailbox.acknowledge(first_delivery)

    assert mailbox.receive("financial_agent") is None
    state = json.loads(mailbox.cursor_path("financial_agent").read_text(encoding="utf-8"))
    assert state["offset"] == mailbox.inbox_path("financial_agent").stat().st_size


def test_json_mailbox_claim_is_atomic_across_processes(tmp_path):
    run_dir = tmp_path / "run"
    mailbox = JsonMailbox(run_dir, run_id="run-1")
    message = _message("run-1", 1)
    mailbox.send(message)

    context = multiprocessing.get_context("spawn")
    ready_queue = context.Queue()
    result_queue = context.Queue()
    start_event = context.Event()
    processes = [
        context.Process(
            target=_claim_in_process,
            args=(run_dir, ready_queue, start_event, result_queue, f"consumer-{index}"),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    for _ in processes:
        ready_queue.get(timeout=10)
    start_event.set()
    results = [result_queue.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert [message_id for _, message_id in results].count(message.message_id) == 1
    assert [message_id for _, message_id in results].count(None) == 1
    state = json.loads(mailbox.cursor_path("legal_agent").read_text(encoding="utf-8"))
    claim = state["claims"][message.message_id]
    assert claim["consumer_id"] in {"consumer-0", "consumer-1"}
    assert claim["lease_expires_at"] > claim["claimed_at"]
    assert state["offset"] == 0


def test_json_mailbox_acknowledges_claims_and_advances_cursor_atomically(tmp_path):
    mailbox = JsonMailbox(tmp_path / "run", run_id="run-1")
    first = _message("run-1", 1)
    second = _message("run-1", 2)
    mailbox.send(first)
    mailbox.send(second)

    first_delivery = mailbox.receive("legal_agent", consumer_id="consumer-1")
    second_delivery = mailbox.receive("legal_agent", consumer_id="consumer-2")
    assert first_delivery is not None
    assert second_delivery is not None
    assert first_delivery.message.message_id == first.message_id
    assert second_delivery.message.message_id == second.message_id

    mailbox.acknowledge(second_delivery)
    state = json.loads(mailbox.cursor_path("legal_agent").read_text(encoding="utf-8"))
    assert state["offset"] == 0
    assert set(state["claims"]) == {first.message_id}

    mailbox.acknowledge(first_delivery)
    state = json.loads(mailbox.cursor_path("legal_agent").read_text(encoding="utf-8"))
    assert state["offset"] == mailbox.inbox_path("legal_agent").stat().st_size
    assert state["claims"] == {}
    assert set(state["processed_ids"]) == {first.message_id, second.message_id}


def test_json_mailbox_redelivers_an_expired_claim_after_consumer_crash(tmp_path):
    run_dir = tmp_path / "run"
    first_consumer = JsonMailbox(
        run_dir,
        run_id="run-1",
        consumer_id="crashed-consumer",
        claim_lease_seconds=0.03,
    )
    message = _message("run-1", 1)
    first_consumer.send(message)
    abandoned_delivery = first_consumer.receive("legal_agent")
    assert abandoned_delivery is not None

    time.sleep(0.06)
    with pytest.raises(ValueError, match="claim is no longer active"):
        first_consumer.acknowledge(abandoned_delivery)

    replacement_consumer = JsonMailbox(
        run_dir,
        run_id="run-1",
        consumer_id="replacement-consumer",
        claim_lease_seconds=1,
    )
    redelivery = replacement_consumer.receive("legal_agent")
    assert redelivery is not None
    assert redelivery.message.message_id == abandoned_delivery.message.message_id
    assert redelivery.claim_id != abandoned_delivery.claim_id
    assert redelivery.consumer_id == "replacement-consumer"

    with pytest.raises(ValueError, match="claim is no longer active"):
        first_consumer.acknowledge(abandoned_delivery)
    replacement_consumer.acknowledge(redelivery)

    assert replacement_consumer.receive("legal_agent") is None
    state = json.loads(
        replacement_consumer.cursor_path("legal_agent").read_text(encoding="utf-8")
    )
    assert state["offset"] == replacement_consumer.inbox_path("legal_agent").stat().st_size
    assert state["claims"] == {}


def test_json_mailbox_renews_long_running_delivery_lease(tmp_path):
    run_dir = tmp_path / "run"
    worker = JsonMailbox(
        run_dir,
        run_id="run-1",
        consumer_id="worker",
        claim_lease_seconds=0.05,
    )
    worker.send(_message("run-1", 1))
    delivery = worker.receive("legal_agent")
    assert delivery is not None

    time.sleep(0.02)
    renewed = worker.renew(delivery, lease_seconds=0.2)
    time.sleep(0.06)
    competitor = JsonMailbox(
        run_dir,
        run_id="run-1",
        consumer_id="competitor",
        claim_lease_seconds=0.05,
    )
    assert competitor.receive("legal_agent") is None

    worker.acknowledge(renewed)
    assert competitor.receive("legal_agent") is None


def test_json_mailbox_reject_dead_letters_once_and_preserves_cursor_order(tmp_path):
    mailbox = JsonMailbox(tmp_path / "run", run_id="run-1")
    first = _message("run-1", 1)
    second = _message("run-1", 2)
    mailbox.send(first)
    mailbox.send(second)
    first_delivery = mailbox.receive("legal_agent", consumer_id="consumer-1")
    second_delivery = mailbox.receive("legal_agent", consumer_id="consumer-2")
    assert first_delivery is not None
    assert second_delivery is not None

    mailbox.acknowledge(second_delivery)
    mailbox.reject(first_delivery, "invalid expert task")

    state = json.loads(mailbox.cursor_path("legal_agent").read_text(encoding="utf-8"))
    assert state["offset"] == mailbox.inbox_path("legal_agent").stat().st_size
    assert state["claims"] == {}
    records = [
        json.loads(line)
        for line in mailbox.dead_letter_path("legal_agent").read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["raw"] == first_delivery.raw_line
    assert records[0]["error"] == "invalid expert task"

    with pytest.raises(ValueError, match="claim is no longer active"):
        mailbox.reject(first_delivery, "duplicate rejection")
    records_after_stale_reject = mailbox.dead_letter_path("legal_agent").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(records_after_stale_reject) == 1
