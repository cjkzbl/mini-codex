from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StartTurn:
    thread_id: str
    prompt: str

    def __post_init__(self) -> None:
        if not self.thread_id or not self.prompt.strip():
            raise ValueError("Thread and nonempty prompt are required")
        if len(self.prompt) > 8_000:
            raise ValueError("输入不能超过 8000 个字符")
