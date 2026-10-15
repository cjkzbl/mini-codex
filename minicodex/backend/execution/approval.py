import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from threading import Event

from minicodex.backend.core.models import new_id


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    turn_id: str
    call_id: str
    tool: str
    mode: str
    summary: str
    fingerprint: str
    expires_at: float

    def wire(self) -> str:
        return json.dumps(
            {
                "approval_id": self.approval_id,
                "turn_id": self.turn_id,
                "call_id": self.call_id,
                "tool": self.tool,
                "mode": self.mode,
                "summary": self.summary,
            },
            ensure_ascii=False,
        )


class ApprovalManager:
    def __init__(self) -> None:
        self.pending: dict[str, tuple[ApprovalRequest, asyncio.Future[bool]]] = {}
        self.denied: set[tuple[str, str]] = set()

    async def ask(
        self,
        *,
        turn_id: str,
        call_id: str,
        tool: str,
        mode: str,
        summary: str,
        fingerprint: str,
        cancel: Event,
        publish: Callable[[str, str], Awaitable[None]],
        timeout_s: float = 120,
    ) -> bool:
        if cancel.is_set() or (turn_id, fingerprint) in self.denied:
            return False
        request = ApprovalRequest(
            new_id(),
            turn_id,
            call_id,
            tool,
            mode,
            summary,
            fingerprint,
            time.monotonic() + timeout_s,
        )
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self.pending[request.approval_id] = request, future
        state = "已失效"
        try:
            await publish("requested", request.wire())
            async with asyncio.timeout(timeout_s):
                approved = await future
            approved = approved and not cancel.is_set() and time.monotonic() < request.expires_at
            state = "已批准" if approved else "已拒绝"
            if not approved:
                self.denied.add((turn_id, fingerprint))
            return approved
        except TimeoutError:
            self.denied.add((turn_id, fingerprint))
            return False
        finally:
            self.pending.pop(request.approval_id, None)
            future.cancel()
            await publish(
                "resolved",
                json.dumps(
                    {"approval_id": request.approval_id, "state": state}, ensure_ascii=False
                ),
            )

    async def respond(self, approval_id: str, turn_id: str, approved: bool) -> None:
        item = self.pending.get(approval_id)
        if item is None:
            return
        request, future = item
        if request.turn_id != turn_id or future.done():
            return
        future.set_result(approved and time.monotonic() < request.expires_at)

    def end_turn(self, turn_id: str) -> None:
        self.denied = {item for item in self.denied if item[0] != turn_id}
