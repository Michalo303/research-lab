import json
import sys
import threading
import time
from contextlib import contextmanager

import pytest

from research_lab.registry import append_jsonl, append_jsonl_batch_atomic


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


def test_atomic_batch_replace_failure_preserves_entire_existing_queue(tmp_path, monkeypatch):
    path = tmp_path / "registry" / "hypothesis_queue.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"hypothesis_id":"existing"}\n', encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(
        "research_lab.registry.os.replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        append_jsonl_batch_atomic(
            path,
            [{"hypothesis_id": "new-1"}, {"hypothesis_id": "new-2"}],
        )

    assert path.read_bytes() == before
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))
