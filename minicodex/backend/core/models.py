from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from minicodex.backend.core.events import RuntimeEvent

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


def new_id() -> str:
    return uuid4().hex


class TurnStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LIMIT_EXCEEDED = "limit_exceeded"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, JsonValue]

    def __post_init__(self) -> None:
        if not self.call_id or not self.name:
            raise ValueError("Tool call must have a call_id and name")


@dataclass(frozen=True, slots=True)
class ToolResult:
    call_id: str
    success: bool
    content: str
    exit_code: int | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.call_id:
            raise ValueError("Tool result must have a call_id")


@dataclass(frozen=True, slots=True)
class Message:
    role: str
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(slots=True)
class ThreadState:
    workspace: Path
    thread_id: str = field(default_factory=new_id)
    history: list[Message] = field(default_factory=list)
    title: str = "新对话"
    title_is_custom: bool = False
    turns: list[TurnState] = field(default_factory=list)


@dataclass(slots=True)
class TurnState:
    thread_id: str
    turn_id: str = field(default_factory=new_id)
    status: TurnStatus = TurnStatus.PENDING
    model_calls: int = 0
    tool_calls: int = 0
    error: str | None = None
    prompt: str = ""
    events: list[RuntimeEvent] = field(default_factory=list)
    duration_s: float = 0.0
    edited_files: set[str] = field(default_factory=set)
    file_metrics_complete: bool = True
    input_tokens_estimated: int = 0
    context_tokens_estimated: int = 0
    context_compactions: int = 0
    model_name: str = ""
    execution_mode: str = "readonly"

    def record_edits(self, paths: Iterable[str]) -> None:
        known = {os.path.normcase(path) for path in self.edited_files}
        for path in paths:
            if os.path.normcase(path) not in known:
                self.edited_files.add(path)
                known.add(os.path.normcase(path))


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
