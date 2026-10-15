from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from threading import Event
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from minicodex.backend.core.models import ToolResult
from minicodex.backend.execution.workspace import Workspace


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    execute: Callable[[dict[str, Any], Workspace, Event], ToolResult]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError("重复工具名称")
        Draft202012Validator.check_schema(spec.parameters)
        self._tools[spec.name] = spec

    def get(self, name: str, arguments: dict[str, Any]) -> ToolSpec:
        if name not in self._tools:
            raise ValueError("未知或未开放的工具")
        spec = self._tools[name]
        errors = list(Draft202012Validator(spec.parameters).iter_errors(arguments))
        if errors:
            # Do not echo raw arguments (they may contain secrets or huge strings).
            raise ValueError("工具参数不符合 JSON Schema")
        return spec

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": deepcopy(spec.parameters),
                },
            }
            for spec in self._tools.values()
        ]

    def copy(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry._tools = dict(self._tools)
        return registry
