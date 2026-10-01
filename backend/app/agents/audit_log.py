"""Audit log of the messages exchanged during one chat turn (the agent trace)."""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel

MAX_INPUT_CHARS = 300


class AuditEntry(BaseModel):
    timestamp: str
    run_id: str
    step: int
    sender: str
    target: str
    action: str
    input_value: str | None = None
    result_status: str = "pending"
    result_summary: str = ""
    error_message: str | None = None


def sanitize(value: str | None) -> str | None:
    if value is None:
        return None
    if "password" in value.lower():
        return "[redacted]"
    if len(value) > MAX_INPUT_CHARS:
        return value[:MAX_INPUT_CHARS] + "..."
    return value


class AuditLog:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.entries: list[AuditEntry] = []

    def open(self, sender: str, target: str, action: str, input_value: str | None = None) -> AuditEntry:
        """Add an entry when a request starts, so nested requests stay in call order."""
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            run_id=self.run_id,
            step=len(self.entries) + 1,
            sender=sender,
            target=target,
            action=action,
            input_value=sanitize(input_value),
        )
        self.entries.append(entry)
        return entry

    def record(
        self,
        sender: str,
        target: str,
        action: str,
        input_value: str | None = None,
        result_status: str = "ok",
        result_summary: str = "",
        error_message: str | None = None,
    ) -> AuditEntry:
        entry = self.open(sender, target, action, input_value)
        entry.result_status = result_status
        entry.result_summary = result_summary
        entry.error_message = error_message
        return entry
