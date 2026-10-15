"""Exercise frozen httpx/JSON Schema/SSL imports and tool loop over real localhost HTTP."""

import argparse
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--bundle", type=Path, default=root / "dist/0.5.3/MiniCodex")
args = parser.parse_args()
report = root / "artifacts" / "package-http-smoke.json"
requests: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests.append(body)
        if body["messages"][-1]["role"] == "user":
            message = {
                "content": None,
                "tool_calls": [
                    {
                        "id": "package-list",
                        "type": "function",
                        "function": {"name": "list_files", "arguments": "{}"},
                    }
                ],
            }
            finish = "tool_calls"
        else:
            result = json.loads(json.loads(body["messages"][-1]["content"])["result"])
            message = {
                "content": "本机 HTTP 测试服务已收到真实工具结果：" + ", ".join(result["files"])
            }
            finish = "stop"
        data = json.dumps({"choices": [{"message": message, "finish_reason": finish}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
environment = dict(os.environ)
environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
environment["QT_QPA_PLATFORM"] = "offscreen"
startup = subprocess.STARTUPINFO()
startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
startup.wShowWindow = subprocess.SW_HIDE
try:
    completed = subprocess.run(
        [
            str(args.bundle.resolve() / "MiniCodex.exe"),
            "--smoke-test",
            str(report),
            "--smoke-api",
            f"http://127.0.0.1:{server.server_port}/v1",
        ],
        env=environment,
        startupinfo=startup,
        timeout=30,
        check=True,
    )
    result = json.loads(report.read_text("utf-8"))
    if not result["passed"] or not result["frozen"] or len(requests) != 2:
        raise RuntimeError("Frozen HTTP smoke failed")
    print(
        json.dumps({"passed": True, "http_requests": len(requests), **result}, ensure_ascii=False)
    )
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
