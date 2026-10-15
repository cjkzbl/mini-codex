"""Scripted decisions + real patch/test execution; not a model-quality benchmark."""

import difflib
import json
import sys
from threading import Event

import pytest

from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.core.events import EventKind
from minicodex.backend.core.history import validate_history
from minicodex.backend.core.models import (
    ModelResponse,
    ThreadState,
    ToolCall,
    TurnState,
    TurnStatus,
)
from minicodex.backend.core.runtime import AgentRuntime
from minicodex.backend.execution.approval import ApprovalManager
from minicodex.backend.llm.fake import FakeLLMProvider

CASES = [
    ("add", "a, b", "a - b", "a + b", "func(2, 3)", "5"),
    ("divide", "a, b", "a // b", "a / b", "func(3, 2)", "1.5"),
    ("even", "n", "n % 2 == 1", "n % 2 == 0", "func(4)", "True"),
    ("last", "values", "values[0]", "values[-1]", "func([1, 2])", "2"),
    ("clamp", "x, lo, hi", "min(lo, max(hi, x))", "max(lo, min(hi, x))", "func(5, 0, 10)", "5"),
    (
        "dedupe",
        "values",
        "list(values)",
        "list(dict.fromkeys(values))",
        "func([1, 1, 2])",
        "[1, 2]",
    ),
    (
        "average",
        "values",
        "sum(values) / (len(values) + 1)",
        "sum(values) / len(values)",
        "func([2, 4])",
        "3",
    ),
    ("inclusive", "n", "sum(range(n))", "sum(range(n + 1))", "func(3)", "6"),
    ("reverse", "text", "text[::1]", "text[::-1]", "func('abc')", "'cba'"),
    (
        "contains",
        "values, x",
        "all(v == x for v in values)",
        "x in values",
        "func([1, 2], 2)",
        "True",
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
async def test_real_read_fail_patch_retest_loop(tmp_path, case):
    name, parameters, broken, fixed, expression, expected = case
    project = tmp_path / ("中文 项目 " + name)
    project.mkdir()
    before = f"# existing user comment\ndef func({parameters}):\n    return {broken}\n"
    after = f"# existing user comment\ndef func({parameters}):\n    return {fixed}\n"
    (project / "calc.py").write_bytes(before.replace("\n", "\r\n").encode())
    (project / "test_calc.py").write_text(
        "import unittest\nfrom calc import func\nclass TestBug(unittest.TestCase):\n"
        "    def test_bug(self):\n        self.assertEqual(" + expression + ", " + expected + ")\n",
        "utf-8",
    )
    patch = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="a/calc.py",
            tofile="b/calc.py",
        )
    )
    test_args = {"argv": [sys.executable, "-m", "unittest", "-v", "test_calc"]}
    provider = FakeLLMProvider(
        [
            ModelResponse(tool_calls=(ToolCall("read", "read_file", {"path": "calc.py"}),)),
            ModelResponse(tool_calls=(ToolCall("test-before", "run_command", test_args),)),
            ModelResponse(tool_calls=(ToolCall("patch", "apply_patch", {"patch": patch}),)),
            ModelResponse(tool_calls=(ToolCall("test-after", "run_command", test_args),)),
            ModelResponse(text="Fixed and verified."),
        ],
        delay_s=0,
    )
    approvals = ApprovalManager()
    settings = ModelSettings(execution_mode="native")
    runtime = AgentRuntime(provider, settings=settings, approvals=approvals)
    thread, events = ThreadState(project), []
    turn = TurnState(thread.thread_id)

    async def emit(event):
        events.append(event)
        if event.kind == EventKind.APPROVAL_REQUESTED:
            request = json.loads(event.text)
            await approvals.respond(request["approval_id"], turn.turn_id, True)

    await runtime.run_turn(thread, turn, "Fix bug and run tests", Event(), emit)
    assert turn.status == TurnStatus.COMPLETED
    receipts = {m.tool_call_id: json.loads(m.content) for m in thread.history if m.role == "tool"}
    assert not receipts["test-before"]["success"]
    assert "AssertionError" in json.loads(receipts["test-before"]["result"])["stderr"]
    assert receipts["test-after"]["success"]
    assert json.loads(receipts["test-after"]["result"])["exit_code"] == 0
    assert "AssertionError" in provider.requests[2][-1].content
    assert (project / "calc.py").read_bytes() == after.replace("\n", "\r\n").encode()
    assert len([e for e in events if e.kind == EventKind.APPROVAL_REQUESTED]) == 3
    assert turn.model_calls == 5 and turn.tool_calls == 4
    assert turn.edited_files == {"calc.py"} and turn.duration_s > 0
    assert thread.turns == [turn] and turn.events == events
    validate_history(thread.history)
