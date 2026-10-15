from minicodex.backend.core.models import Message


def validate_history(messages: list[Message]) -> None:
    pending: set[str] = set()
    seen: set[str] = set()
    for message in messages:
        if message.role == "tool":
            if message.tool_call_id not in pending:
                raise ValueError("工具回执缺失请求、重复或 call_id 不匹配")
            pending.remove(message.tool_call_id)
        else:
            if pending:
                raise ValueError("工具调用尚未获得完整回执")
            if message.tool_calls:
                if message.role != "assistant":
                    raise ValueError("只有 assistant 可以发出工具调用")
                for call in message.tool_calls:
                    if call.call_id in seen:
                        raise ValueError("重复的工具 call_id")
                    seen.add(call.call_id)
                    pending.add(call.call_id)
    if pending:
        raise ValueError("历史中存在悬空工具调用")
