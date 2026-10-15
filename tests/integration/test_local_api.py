import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from minicodex.backend.application.facade import ApplicationFacade
from minicodex.backend.application.settings import ModelSettings
from minicodex.backend.core.history import validate_history
from minicodex.frontend.ui.main_window import MainWindow
from minicodex.frontend.ui.settings_dialog import SettingsDialog


@pytest.fixture
def local_api():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            last = body["messages"][-1]
            if last["role"] == "user":
                calls = [
                    {
                        "id": f"read-{len(requests)}",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": "calc.py"}),
                        },
                    },
                    {
                        "id": f"search-{len(requests)}",
                        "type": "function",
                        "function": {
                            "name": "search_code",
                            "arguments": json.dumps({"pattern": "return"}),
                        },
                    },
                ]
                message, finish = {"content": None, "tool_calls": calls}, "tool_calls"
            else:
                read = json.loads(json.loads(body["messages"][-2]["content"])["result"])
                message, finish = {"content": "calc.py 的真实内容：\n" + read["content"]}, "stop"
            response = json.dumps(
                {"choices": [{"message": message, "finish_reason": finish}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", requests
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_real_http_transport_gui_tools_and_two_turns(qtbot, tmp_path, local_api):
    base_url, requests = local_api
    (tmp_path / "calc.py").write_text("def answer():\n    return 42\n", "utf-8")
    facade = ApplicationFacade()
    facade.configure(
        ModelSettings(mode="compatible", base_url=base_url, model="local-fixture", streaming=False)
    )
    window = MainWindow(facade)
    qtbot.addWidget(window)
    window.show()
    try:
        window.open_workspace(tmp_path)
        for _ in range(2):
            window.prompt.setPlainText("解释 answer 函数")
            window.send_turn()
            qtbot.waitUntil(lambda: window.status_label.text() == "已完成", timeout=5000)
        assert "return 42" in window.chat.toPlainText()
        assert "read_file" in window.chat.toPlainText()
        assert "search_code" in window.chat.toPlainText()
        assert "local-fixture" in window.model_button.text()
        validate_history(facade.thread.history)
        assert len(requests) == 4
        assert len(requests[2]["messages"]) > len(requests[0]["messages"])
        assert {tool["function"]["name"] for tool in requests[0]["tools"]} == {
            "list_files",
            "read_file",
            "search_code",
            "git_status",
            "git_diff",
        }
    finally:
        facade.close()


def test_settings_dialog_validation_memory_only_and_new_thread(qtbot, tmp_path):
    facade = ApplicationFacade()
    try:
        before = facade.select_workspace(tmp_path).thread_id
        dialog = SettingsDialog(facade.settings)
        qtbot.addWidget(dialog)
        dialog.mode.setCurrentIndex(1)
        dialog.base_url.setText("http://insecure.example/v1")
        dialog.model.setText("test-model")
        dialog.save()
        assert dialog.result_settings is None and dialog.error_label.text()
        dialog.base_url.setText("http://localhost:12345/v1")
        dialog.key.setText("fake-secret-key")
        dialog.save()
        assert dialog.result_settings is not None
        assert "fake-secret-key" not in repr(dialog.result_settings)
        facade.configure(dialog.result_settings)
        assert facade.thread.thread_id != before
        assert not facade.thread.history
        assert not list(tmp_path.iterdir())
    finally:
        facade.close()


def test_deepseek_preset_sets_model_without_replacing_key(qtbot):
    dialog = SettingsDialog(ModelSettings())
    qtbot.addWidget(dialog)
    dialog.key.setText("existing-private-key")
    dialog.apply_deepseek_preset()
    assert dialog.base_url.text() == "https://api.deepseek.com"
    assert dialog.model.text() == "deepseek-flash"
    assert dialog.key.text() == "existing-private-key"
    dialog.save()
    assert dialog.result_settings.is_deepseek
