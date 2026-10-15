import argparse
import json
from pathlib import Path

from minicodex.backend.persistence.codec import event_record, turn_record
from minicodex.backend.persistence.paths import default_data_directory
from minicodex.backend.persistence.sqlite_store import SQLiteThreadStore


def trace_main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description="读取脱敏任务追踪（默认不输出对话正文）")
    parser.add_argument("action", choices=["show"])
    parser.add_argument("thread_id")
    parser.add_argument("--data-dir", type=Path, default=default_data_directory())
    args = parser.parse_args(arguments)
    store = SQLiteThreadStore(args.data_dir, readonly=True)
    try:
        thread = store.get(args.thread_id)
        for turn in thread.turns:
            state = turn_record(turn)
            state.pop("prompt", None)
            print(json.dumps({"type": "turn", **state}, ensure_ascii=False))
            for event in turn.events:
                record = event_record(event)
                record.pop("text", None)
                print(json.dumps({"type": "event", **record}, ensure_ascii=False))
    finally:
        store.close()
    return 0
