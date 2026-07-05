import json

from research_lab.jsonl import iter_jsonl, tail_jsonl


def test_tail_jsonl_is_bounded_skips_malformed_and_handles_partial_final_line(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join([json.dumps({"row": index}) for index in range(20)] + ["malformed", json.dumps({"row": 21})]), encoding="utf-8")
    parsed = []
    real_loads = json.loads

    def tracking_loads(value):
        parsed.append(value)
        return real_loads(value)

    monkeypatch.setattr("research_lab.jsonl.json.loads", tracking_loads)
    assert tail_jsonl(path, 3) == [{"row": 19}, {"row": 21}]
    assert len(parsed) == 3


def test_tail_jsonl_non_positive_limit_does_not_open_file(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    opened = False

    def forbidden_open(*args, **kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("file must not be opened")

    monkeypatch.setattr(type(path), "open", forbidden_open)
    assert tail_jsonl(path, 0) == []
    assert tail_jsonl(path, -1) == []
    assert opened is False


def test_iter_jsonl_streams_all_valid_objects_and_counts_invalid_rows(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"row": 1}\nmalformed\n[]\n{"row": 2}', encoding="utf-8")
    counts = {"invalid": 0}
    assert list(iter_jsonl(path, malformed_count=counts, malformed_key="invalid")) == [{"row": 1}, {"row": 2}]
    assert counts == {"invalid": 2}
    assert list(iter_jsonl(tmp_path / "missing.jsonl")) == []
