"""In-process message bus: routes typed requests to agents and logs each one."""
from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from .audit_log import AuditLog
from .messages import AgentResult


class Agent(Protocol):
    name: str
    action: str
    input_model: type[BaseModel]

    def handle(self, query: BaseModel, bus: "MessageBus") -> AgentResult: ...


class AgentRequestError(Exception):
    """The request could not be delivered: unknown agent or invalid payload."""


class MessageBus:
    def __init__(self, run_id: str | None = None):
        self.log = AuditLog(run_id or uuid4().hex)
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        self._agents[agent.name] = agent

    def request(self, sender: str, recipient: str, payload: BaseModel | dict) -> AgentResult:
        """Validate the payload against the recipient's input model, run the
        agent, and record the exchange."""
        agent = self._agents.get(recipient)
        raw = payload.model_dump_json() if isinstance(payload, BaseModel) else str(payload)
        if agent is None:
            message = f"No agent named {recipient!r}."
            self.log.record(sender, recipient, "unknown", raw, "error", error_message=message)
            raise AgentRequestError(message)

        entry = self.log.open(sender, recipient, agent.action, raw)
        try:
            query = (
                payload
                if isinstance(payload, agent.input_model)
                else agent.input_model.model_validate(
                    payload.model_dump() if isinstance(payload, BaseModel) else payload
                )
            )
        except ValidationError as exc:
            message = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            )
            entry.result_status = "error"
            entry.error_message = message
            raise AgentRequestError(f"Invalid request for {recipient}: {message}") from exc

        result = agent.handle(query, self)
        entry.result_status = "ok"
        entry.result_summary = result.summary()
        return result
