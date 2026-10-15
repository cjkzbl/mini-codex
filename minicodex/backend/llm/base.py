from collections.abc import AsyncIterator
from typing import Any, Protocol

from minicodex.backend.core.models import Message, ModelResponse


class LLMProvider(Protocol):
    async def generate(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> ModelResponse: ...

    def stream(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[str | ModelResponse]: ...
