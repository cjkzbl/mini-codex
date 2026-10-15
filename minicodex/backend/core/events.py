from dataclasses import dataclass
from enum import StrEnum


class EventKind(StrEnum):
    TURN_STARTED = "turn.started"
    MODEL_DELTA = "model.delta"
    STEP_STARTED = "step.started"
    TOOL_STARTED = "tool.started"
    TOOL_FINISHED = "tool.finished"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"
    COMMAND_OUTPUT = "command.output"
    DIFF_PREVIEW = "diff.preview"
    TURN_COMPLETED = "turn.completed"
    TURN_CANCELLED = "turn.cancelled"
    TURN_FAILED = "turn.failed"
    TURN_LIMIT_EXCEEDED = "turn.limit_exceeded"
    TURN_INTERRUPTED = "turn.interrupted"
    CONTEXT_COMPACTED = "context.compact"
    INSTRUCTIONS_LOADED = "instructions.loaded"
    MODEL_REQUESTED = "model.request"
    MODEL_RESPONDED = "model.response"


TERMINAL_EVENTS = frozenset(
    {
        EventKind.TURN_COMPLETED,
        EventKind.TURN_CANCELLED,
        EventKind.TURN_FAILED,
        EventKind.TURN_LIMIT_EXCEEDED,
        EventKind.TURN_INTERRUPTED,
    }
)


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    kind: EventKind
    thread_id: str
    turn_id: str
    step_id: str
    sequence: int
    text: str = ""
    call_id: str | None = None

    def __post_init__(self) -> None:
        if not self.thread_id or not self.turn_id or not self.step_id or self.sequence < 1:
            raise ValueError("Event identifiers and positive sequence are required")
