from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from hermes_knowledge.request_runner import run_book_extraction_request


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_book_extraction_request.py"
MODULE_PATH = ROOT / "hermes_knowledge" / "request_runner.py"


def _book(books_root: Path, marker: str, title: str, text: str) -> dict[str, object]:
    sha256 = marker * 64
    book_id = f"book-{sha256[:12]}"
    raw_path = books_root / "raw" / f"{book_id}.pdf"
    text_path = books_root / "text" / f"{book_id}.txt"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"%PDF-1.7\n")
    text_path.write_text(text, encoding="utf-8")
    return {
        "name": title,
        "path": str(raw_path),
        "extension": ".pdf",
        "size_bytes": len(text.encode("utf-8")),
        "sha256": sha256,
    }


def _write_index(books_root: Path, books: list[dict[str, object]]) -> Path:
    path = books_root / "index" / "book_index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "books": books}), encoding="utf-8")
    return path


def _write_request(tmp_path: Path, **overrides: object) -> Path:
    payload = {
        "version": "book_extraction_request_v1",
        "created_at": "2026-06-14T12:00:00Z",
        "source_decision_version": "orchestration_decision_v1",
        "source_selected_blocker": "drawdown_fail",
        "requested_worker": "hermes_book_extraction",
        "request_type": "extract_book_notes_for_blocker",
        "blocker": "drawdown_fail",
        "priority": "high",
        "query_hints": [
            "drawdown control",
            "risk management",
            "volatility targeting",
            "defensive allocation",
            "circuit breaker",
        ],
        "constraints": {
            "must_use_extracted_passages_only": True,
            "must_include_source_provenance": True,
            "must_not_invent_claims": True,
            "must_not_generate_strategy_code": True,
            "must_not_promote_notes": True,
            "must_not_modify_runtime": True,
            "must_not_run_backtests": True,
            "must_not_call_broker": True,
        },
        "allowed_outputs": [
            "proposed_book_notes_jsonl",
            "book_extraction_audit_json",
        ],
        "safety": {
            "worker_execution_allowed": False,
            "llm_calls_allowed_in_this_step": False,
            "pdf_parsing_allowed_in_this_step": False,
            "registry_write_allowed": False,
            "promotion_allowed": False,
            "requires_manual_review": True,
        },
        "evidence": {
            "source_decision_reason": "Selected blocker drawdown_fail routes to book extraction.",
            "source_decision_evidence": {"selected_reason": "drawdown_fail"},
        },
        "no_request_reason": None,
    }
    payload.update(overrides)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _provider_payload(index: int) -> str:
    return json.dumps(_provider_note(index))


def _provider_note(index: int, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "extracted_claim": f"Risk overlay claim {index}",
        "trading_hypothesis": f"Trading hypothesis {index}",
        "why_relevant_to_blocker": "This passage addresses drawdown containment.",
        "implementation_hint": "Add a volatility target and cash filter.",
        "risk_controls": ["volatility target", "cash filter"],
        "validation_hint": "Check unseen max drawdown and crisis periods.",
        "confidence": "medium",
    }
    payload.update(overrides)
    return payload


def _fake_ok_provider(calls: list[tuple[str, str]] | None = None):
    def fake_provider(provider: str, prompt: str, env):
        if calls is not None:
            calls.append((provider, prompt))

        class Result:
            status = "ok"
            output = _provider_payload(len(calls or [None]))

        return Result()

    return fake_provider


def _run_provider_output(tmp_path: Path, output: str | None, *, status: str = "ok"):
    books_root = tmp_path / "hermes_books"
    index_path = _write_index(
        books_root,
        [
            _book(
                books_root,
                "a",
                "Risk Management for Trend Following.pdf",
                "Drawdown control with volatility targeting and risk management.",
            )
        ],
    )
    request_path = _write_request(tmp_path)

    def fake_provider(provider: str, prompt: str, env):
        class Result:
            pass

        result = Result()
        result.status = status
        result.output = output
        result.message = "OpenAI-compatible provider returned empty content" if output is None else ""
        return result

    output_jsonl = tmp_path / "notes.jsonl"
    notes_written, audit = run_book_extraction_request(
        request_path=request_path,
        book_index_path=index_path,
        books_root=books_root,
        output_jsonl=output_jsonl,
        audit_json=tmp_path / "audit.json",
        provider_invoker=fake_provider,
        env={"HERMES_PROVIDER": "command"},
    )
    rows = [
        json.loads(line)
        for line in output_jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return notes_written, rows, audit


def test_valid_drawdown_request_produces_notes_and_audit(tmp_path):
    books_root = tmp_path / "hermes_books"
    index_path = _write_index(
        books_root,
        [
            _book(
                books_root,
                "a",
                "Risk Management for Trend Following.pdf",
                (
                    "Risk management and drawdown control matter. "
                    "Use volatility targeting, defensive allocation, and crisis protection."
                ),
            )
        ],
    )
    request_path = _write_request(tmp_path)
    output_jsonl = tmp_path / "out" / "drawdown_fail_notes.jsonl"
    audit_json = tmp_path / "out" / "drawdown_fail_audit.json"

    calls: list[tuple[str, str]] = []

    notes_written, audit = run_book_extraction_request(
        request_path=request_path,
        book_index_path=index_path,
        books_root=books_root,
        output_jsonl=output_jsonl,
        audit_json=audit_json,
        provider_invoker=_fake_ok_provider(calls),
        env={"HERMES_PROVIDER": "command"},
    )

    assert notes_written == 1
    assert len(calls) == 1
    rows = [json.loads(line) for line in output_jsonl.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["version"] == "extracted_book_note_v1"
    assert rows[0]["blocker"] == "drawdown_fail"
    assert rows[0]["created_by"] == "hermes_book_extraction"
    assert rows[0]["promotion_status"] == "not_promoted"
    assert rows[0]["source_excerpt"]
    assert audit_json.exists()
    assert audit["version"] == "book_extraction_audit_v1"
    assert audit["provider_used"] == "command"
    assert audit["pdf_parser_used"][rows[0]["book_id"]] == "sidecar_text"
    assert audit["notes_written"] == 1
    assert audit["selected_books"][0]["reasons"]
    assert audit["safety"] == {
        "strategy_modification_allowed": False,
        "backtest_allowed": False,
        "promotion_allowed": False,
        "registry_write_allowed": False,
        "service_restart_allowed": False,
        "broker_calls_allowed": False,
    }


def test_provider_prompt_requires_strict_notes_json(tmp_path):
    calls: list[tuple[str, str]] = []
    books_root = tmp_path / "hermes_books"
    index_path = _write_index(
        books_root,
        [
            _book(
                books_root,
                "a",
                "Risk Management for Trend Following.pdf",
                "Drawdown control with volatility targeting and risk management.",
            )
        ],
    )

    run_book_extraction_request(
        request_path=_write_request(tmp_path),
        book_index_path=index_path,
        books_root=books_root,
        output_jsonl=tmp_path / "notes.jsonl",
        audit_json=tmp_path / "audit.json",
        provider_invoker=_fake_ok_provider(calls),
        env={"HERMES_PROVIDER": "command"},
    )

    prompt = calls[0][1]
    assert '"notes": [' in prompt
    assert '"confidence": "low"' in prompt
    assert '"risk_controls": ["..."]' in prompt
    assert "Return only valid JSON" in prompt
    assert "no markdown" in prompt.lower()
    assert "no code fences" in prompt.lower()
    assert '{"notes": []}' in prompt


@pytest.mark.parametrize(
    "provider_output",
    [
        lambda: json.dumps({"notes": [_provider_note(1)]}),
        lambda: json.dumps([_provider_note(1)]),
        lambda: json.dumps(_provider_note(1)),
        lambda: "```json\n" + json.dumps({"notes": [_provider_note(1)]}) + "\n```",
        lambda: "Here is the requested result:\n" + json.dumps({"notes": [_provider_note(1)]}) + "\nEnd.",
    ],
)
def test_provider_output_forms_are_accepted(tmp_path, provider_output):
    notes_written, rows, audit = _run_provider_output(tmp_path, provider_output())

    assert notes_written == 1
    assert rows[0]["promotion_status"] == "not_promoted"
    assert audit["errors"] == []


def test_non_json_provider_output_is_rejected_safely(tmp_path):
    notes_written, rows, audit = _run_provider_output(tmp_path, "not JSON at all")

    assert notes_written == 0
    assert rows == []
    assert any(error["code"] == "invalid_json" for error in audit["errors"])


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        ("moderate", "medium"),
        ("med", "medium"),
        ("mid", "medium"),
        ("average", "medium"),
        ("strong", "high"),
        ("weak", "low"),
        (0.8, "high"),
    ],
)
def test_confidence_variations_are_normalized(tmp_path, confidence, expected):
    output = json.dumps({"notes": [_provider_note(1, confidence=confidence)]})
    notes_written, rows, audit = _run_provider_output(tmp_path, output)

    assert notes_written == 1
    assert rows[0]["confidence"] == expected
    assert any(
        error["code"] == "note_normalized"
        and error["field"] == "confidence"
        and error["normalized_value"] == expected
        for error in audit["errors"]
    )


def test_unknown_confidence_defaults_to_low_with_warning(tmp_path):
    output = json.dumps({"notes": [_provider_note(1, confidence="uncertain-ish")]})
    notes_written, rows, audit = _run_provider_output(tmp_path, output)

    assert notes_written == 1
    assert rows[0]["confidence"] == "low"
    assert any(
        error["code"] == "note_normalized"
        and error["field"] == "confidence"
        and error["reason"] == "confidence_normalized_to_low"
        for error in audit["errors"]
    )


def test_string_risk_controls_becomes_one_item_list(tmp_path):
    output = json.dumps({"notes": [_provider_note(1, risk_controls="Use a hard exposure cap.")]})
    notes_written, rows, audit = _run_provider_output(tmp_path, output)

    assert notes_written == 1
    assert rows[0]["risk_controls"] == ["Use a hard exposure cap."]
    assert any(
        error["code"] == "note_normalized"
        and error["field"] == "risk_controls"
        for error in audit["errors"]
    )


def test_missing_risk_controls_defaults_with_warning(tmp_path):
    note = _provider_note(1)
    note.pop("risk_controls")
    notes_written, rows, audit = _run_provider_output(tmp_path, json.dumps({"notes": [note]}))

    assert notes_written == 1
    assert rows[0]["risk_controls"] == ["manual review required before any implementation"]
    assert any(
        error["code"] == "note_normalized"
        and error["field"] == "risk_controls"
        and error["reason"] == "risk_controls_defaulted"
        for error in audit["errors"]
    )


def test_real_failure_shape_can_produce_a_safe_note(tmp_path):
    provider_output = (
        "```json\n"
        + json.dumps(
            {
                "notes": [
                    _provider_note(
                        1,
                        confidence="moderate",
                        risk_controls="Require manual drawdown review.",
                    )
                ]
            }
        )
        + "\n```"
    )
    notes_written, rows, audit = _run_provider_output(tmp_path, provider_output)

    assert notes_written == 1
    assert rows[0]["confidence"] == "medium"
    assert rows[0]["risk_controls"] == ["Require manual drawdown review."]
    assert rows[0]["promotion_status"] == "not_promoted"
    assert sum(error["code"] == "note_normalized" for error in audit["errors"]) == 2


def test_empty_provider_content_is_audited_safely(tmp_path):
    notes_written, rows, audit = _run_provider_output(tmp_path, None, status="provider_error")

    assert notes_written == 0
    assert rows == []
    assert any(error["code"] == "provider_empty_content" for error in audit["errors"])


def test_no_request_refuses_and_writes_nothing(tmp_path):
    books_root = tmp_path / "hermes_books"
    index_path = _write_index(books_root, [])
    request_path = _write_request(tmp_path, request_type="no_request", no_request_reason="decision_validation_failed")
    output_jsonl = tmp_path / "out" / "notes.jsonl"
    audit_json = tmp_path / "out" / "audit.json"

    with pytest.raises(ValueError, match="no_request"):
        run_book_extraction_request(
            request_path=request_path,
            book_index_path=index_path,
            books_root=books_root,
            output_jsonl=output_jsonl,
            audit_json=audit_json,
            env={"HERMES_PROVIDER": "command"},
        )

    assert not output_jsonl.exists()
    assert not audit_json.exists()


def test_wrong_blocker_refuses(tmp_path):
    books_root = tmp_path / "hermes_books"
    index_path = _write_index(books_root, [])
    request_path = _write_request(tmp_path, blocker="walk_forward_fail", source_selected_blocker="walk_forward_fail")

    with pytest.raises(ValueError, match="drawdown_fail"):
        run_book_extraction_request(
            request_path=request_path,
            book_index_path=index_path,
            books_root=books_root,
            output_jsonl=tmp_path / "notes.jsonl",
            audit_json=tmp_path / "audit.json",
            env={"HERMES_PROVIDER": "command"},
        )


def test_missing_safety_block_is_refused(tmp_path):
    books_root = tmp_path / "hermes_books"
    index_path = _write_index(books_root, [])
    request_path = _write_request(tmp_path, safety=None)

    with pytest.raises(ValueError, match="safety"):
        run_book_extraction_request(
            request_path=request_path,
            book_index_path=index_path,
            books_root=books_root,
            output_jsonl=tmp_path / "notes.jsonl",
            audit_json=tmp_path / "audit.json",
            env={"HERMES_PROVIDER": "command"},
        )


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"worker_execution_allowed": True}, "worker_execution_allowed"),
        ({"promotion_allowed": True}, "promotion_allowed"),
        ({"registry_write_allowed": True}, "registry_write_allowed"),
        ({"requires_manual_review": False}, "requires_manual_review"),
        ({"llm_calls_allowed_in_this_step": True}, "llm_calls_allowed_in_this_step"),
        ({"pdf_parsing_allowed_in_this_step": True}, "pdf_parsing_allowed_in_this_step"),
        ({"backtest_allowed": True}, "backtest_allowed"),
        ({"strategy_modification_allowed": True}, "strategy_modification_allowed"),
        ({"service_restart_allowed": True}, "service_restart_allowed"),
        ({"broker_calls_allowed": True}, "broker_calls_allowed"),
    ],
)
def test_invalid_safety_flags_are_refused(tmp_path, override, expected):
    books_root = tmp_path / "hermes_books"
    index_path = _write_index(books_root, [])
    request_path = _write_request(
        tmp_path,
        safety={
            **json.loads(_write_request(tmp_path).read_text(encoding="utf-8"))["safety"],
            **override,
        },
    )

    with pytest.raises(ValueError, match=expected):
        run_book_extraction_request(
            request_path=request_path,
            book_index_path=index_path,
            books_root=books_root,
            output_jsonl=tmp_path / "notes.jsonl",
            audit_json=tmp_path / "audit.json",
            env={"HERMES_PROVIDER": "command"},
        )


def test_max_books_limit_enforced(tmp_path):
    books_root = tmp_path / "hermes_books"
    books = [
        _book(
            books_root,
            marker,
            title,
            "Drawdown control and risk management with volatility targeting.",
        )
        for marker, title in (
            ("a", "Drawdown Control Handbook.pdf"),
            ("b", "Risk Management Playbook.pdf"),
            ("c", "Volatility Targeting Reference.pdf"),
            ("d", "Defensive Allocation Manual.pdf"),
        )
    ]
    index_path = _write_index(books_root, books)
    request_path = _write_request(tmp_path)

    _, audit = run_book_extraction_request(
        request_path=request_path,
        book_index_path=index_path,
        books_root=books_root,
        output_jsonl=tmp_path / "notes.jsonl",
        audit_json=tmp_path / "audit.json",
        max_books=2,
        provider_invoker=_fake_ok_provider(),
        env={"HERMES_PROVIDER": "command"},
    )

    assert len(audit["selected_books"]) == 2


def test_max_notes_limit_enforced(tmp_path):
    books_root = tmp_path / "hermes_books"
    books = [
        _book(
            books_root,
            marker,
            title,
            (
                "Drawdown control uses volatility targeting. "
                "Risk management improves crisis protection. "
                "Defensive allocation reduces portfolio risk."
            ),
        )
        for marker, title in (
            ("a", "Drawdown Control Handbook.pdf"),
            ("b", "Risk Management Playbook.pdf"),
        )
    ]
    index_path = _write_index(books_root, books)
    request_path = _write_request(tmp_path)
    output_jsonl = tmp_path / "notes.jsonl"

    call_count = {"count": 0}

    def fake_provider(provider: str, prompt: str, env):
        call_count["count"] += 1
        class Result:
            status = "ok"
            output = _provider_payload(call_count["count"])
        return Result()

    notes_written, audit = run_book_extraction_request(
        request_path=request_path,
        book_index_path=index_path,
        books_root=books_root,
        output_jsonl=output_jsonl,
        audit_json=tmp_path / "audit.json",
        max_notes=2,
        provider_invoker=fake_provider,
        env={"HERMES_PROVIDER": "command"},
    )

    assert notes_written == 2
    assert audit["notes_written"] == 2
    assert len(output_jsonl.read_text(encoding="utf-8").splitlines()) == 2


def test_source_path_outside_books_root_is_skipped_with_audit_error(tmp_path):
    books_root = tmp_path / "hermes_books"
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    outside_pdf = outside_root / "outside-risk-book.pdf"
    outside_pdf.write_bytes(b"%PDF-1.7\n")
    index_path = _write_index(
        books_root,
        [
            {
                "name": "Drawdown Control Outside.pdf",
                "path": str(outside_pdf),
                "extension": ".pdf",
                "size_bytes": 10,
                "sha256": "e" * 64,
            }
        ],
    )
    request_path = _write_request(tmp_path)
    calls: list[tuple[str, str]] = []

    notes_written, audit = run_book_extraction_request(
        request_path=request_path,
        book_index_path=index_path,
        books_root=books_root,
        output_jsonl=tmp_path / "notes.jsonl",
        audit_json=tmp_path / "audit.json",
        provider_invoker=_fake_ok_provider(calls),
        env={"HERMES_PROVIDER": "command"},
    )

    assert notes_written == 0
    assert calls == []
    assert audit["selected_books"] == []
    assert any(error["code"] == "source_path_outside_books_root" for error in audit["errors"])


def test_sidecar_text_is_bounded_before_provider_call(tmp_path):
    books_root = tmp_path / "hermes_books"
    bounded_marker = "BOUNDARY_MARKER"
    unbounded_marker = "UNBOUNDED_SUFFIX"
    text = ("x" * 3850) + f" drawdown control {bounded_marker} " + ("y" * 250) + unbounded_marker
    index_path = _write_index(
        books_root,
        [_book(books_root, "a", "Risk Management for Trend Following.pdf", text)],
    )
    request_path = _write_request(tmp_path)
    calls: list[tuple[str, str]] = []

    notes_written, audit = run_book_extraction_request(
        request_path=request_path,
        book_index_path=index_path,
        books_root=books_root,
        output_jsonl=tmp_path / "notes.jsonl",
        audit_json=tmp_path / "audit.json",
        max_pages_per_book=1,
        provider_invoker=_fake_ok_provider(calls),
        env={"HERMES_PROVIDER": "command"},
    )

    assert notes_written == 1
    assert len(calls) == 1
    assert bounded_marker in calls[0][1]
    assert unbounded_marker not in calls[0][1]
    assert audit["pdf_parser_used"][json.loads((tmp_path / "notes.jsonl").read_text(encoding="utf-8").splitlines()[0])["book_id"]] == "sidecar_text"


def test_sidecar_terms_beyond_bound_do_not_trigger_provider(tmp_path):
    books_root = tmp_path / "hermes_books"
    text = ("x" * 4500) + " drawdown control risk management"
    index_path = _write_index(
        books_root,
        [_book(books_root, "a", "Risk Management for Trend Following.pdf", text)],
    )
    request_path = _write_request(tmp_path)
    calls: list[tuple[str, str]] = []

    notes_written, audit = run_book_extraction_request(
        request_path=request_path,
        book_index_path=index_path,
        books_root=books_root,
        output_jsonl=tmp_path / "notes.jsonl",
        audit_json=tmp_path / "audit.json",
        max_pages_per_book=1,
        provider_invoker=_fake_ok_provider(calls),
        env={"HERMES_PROVIDER": "command"},
    )

    assert notes_written == 0
    assert calls == []
    assert any(error["code"] == "no_match" for error in audit["errors"])


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("trading_hypothesis", "```python\nprint('run')\n```", "unsafe_note_content"),
        ("implementation_hint", "Run a backtest() and promote the winner.", "unsafe_note_content"),
    ],
)
def test_provider_notes_with_code_or_automation_are_rejected(tmp_path, field, value, expected_code):
    books_root = tmp_path / "hermes_books"
    index_path = _write_index(
        books_root,
        [
            _book(
                books_root,
                "a",
                "Risk Management for Trend Following.pdf",
                "Drawdown control with volatility targeting and risk management.",
            )
        ],
    )
    request_path = _write_request(tmp_path)

    def fake_provider(provider: str, prompt: str, env):
        payload = json.loads(_provider_payload(1))
        payload[field] = value

        class Result:
            status = "ok"
            output = json.dumps(payload)

        return Result()

    notes_written, audit = run_book_extraction_request(
        request_path=request_path,
        book_index_path=index_path,
        books_root=books_root,
        output_jsonl=tmp_path / "notes.jsonl",
        audit_json=tmp_path / "audit.json",
        provider_invoker=fake_provider,
        env={"HERMES_PROVIDER": "command"},
    )

    assert notes_written == 0
    assert (tmp_path / "notes.jsonl").read_text(encoding="utf-8") == ""
    assert any(error["code"] == expected_code for error in audit["errors"])


def test_pdf_audit_uses_book_id_when_filename_stem_differs(tmp_path, monkeypatch):
    books_root = tmp_path / "hermes_books"
    sha256 = "f" * 64
    raw_path = books_root / "raw" / "drawdown-manual.pdf"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"%PDF-1.7\n")
    index_path = _write_index(
        books_root,
        [
            {
                "name": "Drawdown Control Manual.pdf",
                "path": str(raw_path),
                "extension": ".pdf",
                "size_bytes": 10,
                "sha256": sha256,
            }
        ],
    )
    request_path = _write_request(tmp_path)
    book_id = f"book-{sha256[:12]}"

    class FakePage:
        def __init__(self, text: str):
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class FakeReader:
        def __init__(self, path: str):
            self.pages = [
                FakePage("Drawdown control and volatility targeting."),
                FakePage("Risk management and defensive allocation."),
            ]

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=FakeReader))

    notes_written, audit = run_book_extraction_request(
        request_path=request_path,
        book_index_path=index_path,
        books_root=books_root,
        output_jsonl=tmp_path / "notes.jsonl",
        audit_json=tmp_path / "audit.json",
        provider_invoker=_fake_ok_provider(),
        env={"HERMES_PROVIDER": "command"},
    )

    assert notes_written == 1
    assert audit["pages_scanned_by_book"][book_id] == 2
    assert audit["pdf_parser_used"][book_id] == "pypdf"


def test_cli_writes_only_requested_output_files(tmp_path):
    books_root = tmp_path / "hermes_books"
    index_path = _write_index(
        books_root,
        [
            _book(
                books_root,
                "a",
                "Risk Management for Trend Following.pdf",
                "Drawdown control with volatility targeting and risk management.",
            )
        ],
    )
    request_path = _write_request(tmp_path)
    output_jsonl = tmp_path / "out" / "notes.jsonl"
    audit_json = tmp_path / "out" / "audit.json"
    provider_script = tmp_path / "provider_stub.py"
    provider_script.write_text(
        "import json\n"
        f"print(json.dumps({json.dumps(json.loads(_provider_payload(1)))}))\n",
        encoding="utf-8",
    )

    env = {
        **os.environ,
        "HERMES_PROVIDER": "command",
        "HERMES_COMMAND": f"{sys.executable} {provider_script}",
    }
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--request",
            str(request_path),
            "--book-index",
            str(index_path),
            "--books-root",
            str(books_root),
            "--output-jsonl",
            str(output_jsonl),
            "--audit-json",
            str(audit_json),
            "--max-books",
            "1",
            "--max-pages-per-book",
            "40",
            "--max-notes",
            "1",
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )

    rows = output_jsonl.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    files = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file())
    assert files == [
        "hermes_books/index/book_index.json",
        "hermes_books/raw/book-aaaaaaaaaaaa.pdf",
        "hermes_books/text/book-aaaaaaaaaaaa.txt",
        "out/audit.json",
        "out/notes.jsonl",
        "provider_stub.py",
        "request.json",
    ]


def test_cli_avoids_strategy_backtest_and_registry_imports():
    forbidden_import_roots = (
        "research_lab.runner",
        "research_lab.deployment_gate",
        "research_lab.backtest",
        "research_lab.walk_forward",
        "research_lab.strategies",
        "research_lab.reports",
        "research_lab.registry",
    )

    for path in (MODULE_PATH, SCRIPT_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        for import_name in imports:
            for forbidden_root in forbidden_import_roots:
                assert not (
                    import_name == forbidden_root or import_name.startswith(forbidden_root + ".")
                )
