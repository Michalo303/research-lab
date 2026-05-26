import json
import sys
import threading
import time
from contextlib import contextmanager

from research_lab.registry import append_jsonl


@contextmanager
def _held_lock(path):
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def test_append_jsonl_waits_for_registry_lock(tmp_path):
    path = tmp_path / "registry" / "experiments.jsonl"
    finished = threading.Event()

    def append_row():
        append_jsonl(path, {"strategy_id": "S1"})
        finished.set()

    with _held_lock(path):
        thread = threading.Thread(target=append_row)
        thread.start()
        time.sleep(0.2)
        assert not finished.is_set()

    thread.join(timeout=2)

    assert finished.is_set()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["strategy_id"] == "S1"
