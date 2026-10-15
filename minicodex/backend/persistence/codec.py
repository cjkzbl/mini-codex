"""Versioned JSON records; no pickle or executable deserialization."""

from dataclasses import asdict, fields
from typing import Any

from minicodex.backend.core.events import EventKind, RuntimeEvent
from minicodex.backend.core.models import Message, ToolCall, TurnState, TurnStatus


def message_record(message: Message) -> dict[str, Any]:
    return asdict(message)


def read_message(data: dict[str, Any]) -> Message:
    return Message(
        data["role"],
        data["content"],
        tuple(
            ToolCall(c["call_id"], c["name"], c["arguments"]) for c in data.get("tool_calls", [])
        ),
        data.get("tool_call_id"),
    )


def event_record(event: RuntimeEvent) -> dict[str, Any]:
    return asdict(event)


def read_event(data: dict[str, Any]) -> RuntimeEvent:
    return RuntimeEvent(
        EventKind(data["kind"]),
        data["thread_id"],
        data["turn_id"],
        data["step_id"],
        data["sequence"],
        data.get("text", ""),
        data.get("call_id"),
    )


def turn_record(turn: TurnState) -> dict[str, Any]:
    data = {f.name: getattr(turn, f.name) for f in fields(turn) if f.name != "events"}
    data["edited_files"] = sorted(turn.edited_files)
    return data


def read_turn(data: dict[str, Any]) -> TurnState:
    data = dict(data)
    data["status"] = TurnStatus(data["status"])
    data["edited_files"] = set(data.get("edited_files", []))
    return TurnState(**data)
