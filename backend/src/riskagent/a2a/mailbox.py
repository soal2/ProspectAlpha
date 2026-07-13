"""Concurrent-safe JSONL mailboxes carrying official A2A Message objects."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from riskagent.a2a.protocol import ProtocolMessage
from riskagent.a2a.storage import (
    append_json_line,
    atomic_write_json,
    ensure_private_directory,
    ensure_private_file,
    exclusive_file_lock,
    read_json,
)


_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True, slots=True)
class MailboxDelivery:
    recipient: str
    message: ProtocolMessage
    start_offset: int
    end_offset: int
    raw_line: str
    claim_id: str
    consumer_id: str
    lease_expires_at: float


class JsonMailbox:
    """Per-run JSONL mailboxes with durable cursor and message-id dedupe state."""

    def __init__(
        self,
        run_dir: str | Path,
        *,
        run_id: str,
        consumer_id: str | None = None,
        claim_lease_seconds: float = 300.0,
    ) -> None:
        if claim_lease_seconds <= 0:
            raise ValueError("claim_lease_seconds must be greater than zero")
        self.run_dir = ensure_private_directory(Path(run_dir))
        self.run_id = run_id
        self.consumer_id = consumer_id or _default_consumer_id()
        if not self.consumer_id.strip():
            raise ValueError("consumer_id must not be blank")
        self.claim_lease_seconds = float(claim_lease_seconds)
        self.inboxes_dir = ensure_private_directory(self.run_dir / "inboxes")
        self.cursors_dir = ensure_private_directory(self.run_dir / "cursors")
        self.dead_letter_dir = ensure_private_directory(self.run_dir / "dead-letter")
        self.locks_dir = ensure_private_directory(self.run_dir / ".locks")

    def register(self, recipient: str) -> None:
        recipient = _validate_component(recipient, "recipient")
        ensure_private_file(self.inbox_path(recipient))
        with exclusive_file_lock(self._lock_path(recipient)):
            state_path = self.cursor_path(recipient)
            if not state_path.exists():
                atomic_write_json(
                    state_path,
                    {"offset": 0, "processed_ids": [], "claims": {}},
                )

    def send(self, message: ProtocolMessage) -> str:
        if message.context_id != self.run_id:
            raise ValueError("Message context_id does not match this mailbox run")
        recipient = _validate_component(message.recipient, "recipient")
        self.register(recipient)
        with exclusive_file_lock(self._lock_path(recipient)):
            append_json_line(self.inbox_path(recipient), message.to_dict())
        return message.message_id

    def receive(
        self,
        recipient: str,
        predicate: Callable[[ProtocolMessage], bool] | None = None,
        *,
        consumer_id: str | None = None,
        lease_seconds: float | None = None,
    ) -> MailboxDelivery | None:
        """Atomically claim and return one delivery, optionally scanning for a match.

        A predicate permits request/response correlation without acknowledging
        earlier unrelated messages. Active claims are skipped, so concurrent
        consumers can never receive the same message at the same time. The cursor
        advances only over acknowledged or dead-lettered lines. An expired claim is
        eligible for redelivery, preserving at-least-once delivery after a crash.
        """

        recipient = _validate_component(recipient, "recipient")
        resolved_consumer_id = consumer_id or self.consumer_id
        if not resolved_consumer_id.strip():
            raise ValueError("consumer_id must not be blank")
        resolved_lease_seconds = (
            self.claim_lease_seconds if lease_seconds is None else float(lease_seconds)
        )
        if resolved_lease_seconds <= 0:
            raise ValueError("lease_seconds must be greater than zero")
        self.register(recipient)
        with exclusive_file_lock(self._lock_path(recipient)):
            state = self._load_state(recipient)
            changed = self._expire_claims(state)
            changed = self._advance_cursor(recipient, state) or changed
            with self.inbox_path(recipient).open("rb") as handle:
                handle.seek(state["offset"])
                while True:
                    start_offset = handle.tell()
                    raw = handle.readline()
                    if not raw:
                        break
                    end_offset = handle.tell()
                    message, line_changed = self._parse_line(recipient, start_offset, raw, state)
                    changed = changed or line_changed
                    if message is None or message.message_id in state["processed_ids"]:
                        continue
                    if predicate is not None and not predicate(message):
                        continue
                    if message.message_id in state["claims"]:
                        continue
                    claimed_at = time.time()
                    claim_id = uuid.uuid4().hex
                    lease_expires_at = claimed_at + resolved_lease_seconds
                    state["claims"][message.message_id] = {
                        "claim_id": claim_id,
                        "consumer_id": resolved_consumer_id,
                        "start_offset": start_offset,
                        "end_offset": end_offset,
                        "claimed_at": claimed_at,
                        "lease_expires_at": lease_expires_at,
                    }
                    self._save_state(recipient, state)
                    return MailboxDelivery(
                        recipient=recipient,
                        message=message,
                        start_offset=start_offset,
                        end_offset=end_offset,
                        raw_line=raw.decode("utf-8", errors="replace").rstrip("\n"),
                        claim_id=claim_id,
                        consumer_id=resolved_consumer_id,
                        lease_expires_at=lease_expires_at,
                    )
            if changed:
                self._advance_cursor(recipient, state)
                self._save_state(recipient, state)
        return None

    def acknowledge(self, delivery: MailboxDelivery) -> None:
        recipient = _validate_component(delivery.recipient, "recipient")
        if delivery.message.context_id != self.run_id:
            raise ValueError("Delivery context_id does not match this mailbox run")
        with exclusive_file_lock(self._lock_path(recipient)):
            state = self._load_state(recipient)
            self._validate_active_claim(delivery, state)
            state["processed_ids"].add(delivery.message.message_id)
            state["claims"].pop(delivery.message.message_id, None)
            self._advance_cursor(recipient, state)
            self._save_state(recipient, state)

    def renew(
        self,
        delivery: MailboxDelivery,
        *,
        lease_seconds: float | None = None,
    ) -> MailboxDelivery:
        """Extend an active claim while a long-running expert handler is working."""

        recipient = _validate_component(delivery.recipient, "recipient")
        resolved_lease_seconds = (
            self.claim_lease_seconds if lease_seconds is None else float(lease_seconds)
        )
        if resolved_lease_seconds <= 0:
            raise ValueError("lease_seconds must be greater than zero")
        with exclusive_file_lock(self._lock_path(recipient)):
            state = self._load_state(recipient)
            self._validate_active_claim(delivery, state)
            lease_expires_at = time.time() + resolved_lease_seconds
            state["claims"][delivery.message.message_id][
                "lease_expires_at"
            ] = lease_expires_at
            self._save_state(recipient, state)
        return MailboxDelivery(
            recipient=delivery.recipient,
            message=delivery.message,
            start_offset=delivery.start_offset,
            end_offset=delivery.end_offset,
            raw_line=delivery.raw_line,
            claim_id=delivery.claim_id,
            consumer_id=delivery.consumer_id,
            lease_expires_at=lease_expires_at,
        )

    def reject(self, delivery: MailboxDelivery, reason: str) -> None:
        recipient = _validate_component(delivery.recipient, "recipient")
        if delivery.message.context_id != self.run_id:
            raise ValueError("Delivery context_id does not match this mailbox run")
        with exclusive_file_lock(self._lock_path(recipient)):
            state = self._load_state(recipient)
            self._validate_active_claim(delivery, state)
            self._write_dead_letter(
                recipient,
                offset=delivery.start_offset,
                raw_line=delivery.raw_line,
                error=reason,
            )
            state["processed_ids"].add(delivery.message.message_id)
            state["claims"].pop(delivery.message.message_id, None)
            self._advance_cursor(recipient, state)
            self._save_state(recipient, state)

    def inbox_path(self, recipient: str) -> Path:
        return self.inboxes_dir / f"{_validate_component(recipient, 'recipient')}.jsonl"

    def cursor_path(self, recipient: str) -> Path:
        return self.cursors_dir / f"{_validate_component(recipient, 'recipient')}.json"

    def dead_letter_path(self, recipient: str) -> Path:
        return self.dead_letter_dir / f"{_validate_component(recipient, 'recipient')}.jsonl"

    def _lock_path(self, recipient: str) -> Path:
        return self.locks_dir / f"{recipient}.lock"

    def _load_state(self, recipient: str) -> dict[str, Any]:
        value = read_json(
            self.cursor_path(recipient),
            {"offset": 0, "processed_ids": [], "claims": {}},
        )
        if not isinstance(value, dict):
            raise ValueError(f"Invalid cursor state for {recipient}")
        offset = value.get("offset", 0)
        processed_ids = value.get("processed_ids", [])
        claims = value.get("claims", {})
        if (
            not isinstance(offset, int)
            or offset < 0
            or not isinstance(processed_ids, list)
            or not isinstance(claims, dict)
        ):
            raise ValueError(f"Invalid cursor state for {recipient}")
        normalized_claims: dict[str, dict[str, Any]] = {}
        for message_id, claim in claims.items():
            if not isinstance(message_id, str) or not _valid_claim(claim):
                raise ValueError(f"Invalid cursor claim state for {recipient}")
            normalized_claims[message_id] = dict(claim)
        return {
            "offset": offset,
            "processed_ids": {str(item) for item in processed_ids},
            "claims": normalized_claims,
        }

    def _save_state(self, recipient: str, state: dict[str, Any]) -> None:
        atomic_write_json(
            self.cursor_path(recipient),
            {
                "offset": state["offset"],
                "processed_ids": sorted(state["processed_ids"]),
                "claims": {
                    message_id: state["claims"][message_id]
                    for message_id in sorted(state["claims"])
                    if message_id not in state["processed_ids"]
                },
            },
        )

    def _expire_claims(self, state: dict[str, Any]) -> bool:
        now = time.time()
        expired_ids = [
            message_id
            for message_id, claim in state["claims"].items()
            if claim["lease_expires_at"] <= now
            or message_id in state["processed_ids"]
        ]
        for message_id in expired_ids:
            state["claims"].pop(message_id, None)
        return bool(expired_ids)

    def _validate_active_claim(
        self,
        delivery: MailboxDelivery,
        state: dict[str, Any],
    ) -> None:
        claim = state["claims"].get(delivery.message.message_id)
        if (
            claim is None
            or claim["claim_id"] != delivery.claim_id
            or claim["consumer_id"] != delivery.consumer_id
            or claim["start_offset"] != delivery.start_offset
            or claim["end_offset"] != delivery.end_offset
            or claim["lease_expires_at"] <= time.time()
        ):
            raise ValueError("Mailbox delivery claim is no longer active")

    def _advance_cursor(self, recipient: str, state: dict[str, Any]) -> bool:
        changed = False
        with self.inbox_path(recipient).open("rb") as handle:
            handle.seek(state["offset"])
            while True:
                start_offset = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                end_offset = handle.tell()
                message, line_changed = self._parse_line(recipient, start_offset, raw, state)
                changed = changed or line_changed
                if message is not None and message.message_id not in state["processed_ids"]:
                    break
                state["offset"] = end_offset
                changed = True
        return changed

    def _parse_line(
        self,
        recipient: str,
        offset: int,
        raw: bytes,
        state: dict[str, Any],
    ) -> tuple[ProtocolMessage | None, bool]:
        try:
            raw_text = raw.decode("utf-8").rstrip("\n")
            message = ProtocolMessage.from_json(raw_text)
            if message.context_id != self.run_id:
                raise ValueError("Message context_id does not match mailbox run")
            if message.recipient != recipient:
                raise ValueError("Message recipient does not match mailbox file")
            return message, False
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError, ValueError) as exc:
            raw_text = raw.decode("utf-8", errors="replace").rstrip("\n")
            invalid_id = _invalid_line_id(offset, raw)
            if invalid_id in state["processed_ids"]:
                return None, False
            self._write_dead_letter(recipient, offset=offset, raw_line=raw_text, error=str(exc))
            state["processed_ids"].add(invalid_id)
            return None, True

    def _write_dead_letter(self, recipient: str, *, offset: int, raw_line: str, error: str) -> None:
        append_json_line(
            self.dead_letter_path(recipient),
            {
                "offset": offset,
                "raw": raw_line,
                "error": error,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            },
        )


def _validate_component(value: str, label: str) -> str:
    if not value or not _SAFE_COMPONENT.fullmatch(value):
        raise ValueError(f"{label} must contain only letters, digits, dot, underscore, or hyphen")
    return value


def _invalid_line_id(offset: int, raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    return f"invalid:{offset}:{digest}"


def _default_consumer_id() -> str:
    return (
        f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}:"
        f"{uuid.uuid4().hex}"
    )


def _valid_claim(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        isinstance(value.get("claim_id"), str)
        and bool(value["claim_id"])
        and isinstance(value.get("consumer_id"), str)
        and bool(value["consumer_id"])
        and isinstance(value.get("start_offset"), int)
        and value["start_offset"] >= 0
        and isinstance(value.get("end_offset"), int)
        and value["end_offset"] > value["start_offset"]
        and isinstance(value.get("claimed_at"), (int, float))
        and isinstance(value.get("lease_expires_at"), (int, float))
        and value["lease_expires_at"] >= value["claimed_at"]
    )
