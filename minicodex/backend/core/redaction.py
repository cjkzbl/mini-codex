import json
import re


def redact(text: str, secret: str = "") -> str:
    if secret:
        encoded = secret
        for _ in range(4):
            text = text.replace(encoded, "[REDACTED]")
            encoded = json.dumps(encoded, ensure_ascii=False)[1:-1]
    return re.sub(r"sk-[A-Za-z0-9_-]{16,}", "[REDACTED]", text)
