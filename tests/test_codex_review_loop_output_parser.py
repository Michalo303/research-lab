from __future__ import annotations

import json

from research_lab.orchestration.codex_review_loop_output_parser import (
    parse_codex_review_loop_output,
)


def _contract_payload(**overrides) -> str:
    payload = {
        "status": "completed",
        "summary": "Updated the review loop parser.",
        "changed_files": [
            "research_lab\\orchestration\\codex_review_loop.py",
            "./research_lab/orchestration/codex_review_loop.py",
            "tests/test_codex_review_loop.py",
        ],
        "diff_summary": {
            "files_changed": 2,
            "insertions": 10,
            "deletions": 3,
            "line_count": 42,
        },
        "validation": {
            "commands": [
                {
                    "command": "python -m pytest tests/test_codex_review_loop.py -q",
                    "exit_code": 0,
                    "stdout": "1 passed",
                    "stderr": "",
                }
            ],
            "overall_status": "passed",
        },
        "blocked_reason": None,
        "raw_notes": "optional",
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_parses_whole_stdout_json_contract():
    result = parse_codex_review_loop_output(
        stdout=_contract_payload(),
        stderr="",
        exit_code=0,
    )

    assert result.status == "completed"
    assert result.summary == "Updated the review loop parser."
    assert result.changed_files == [
        "research_lab/orchestration/codex_review_loop.py",
        "tests/test_codex_review_loop.py",
    ]
    assert result.diff_summary == {
        "files_changed": 2,
        "insertions": 10,
        "deletions": 3,
        "line_count": 42,
    }
    assert result.validation["overall_status"] == "passed"
    assert result.validation["commands"][0]["command"] == "python -m pytest tests/test_codex_review_loop.py -q"
    assert result.parser_warning is None
    assert result.parse_error is None


def test_parses_fenced_json_block():
    stdout = f"before\n```json\n{_contract_payload(summary='From fenced block.')}\n```\nafter"

    result = parse_codex_review_loop_output(stdout=stdout, stderr="", exit_code=0)

    assert result.summary == "From fenced block."
    assert result.changed_files == [
        "research_lab/orchestration/codex_review_loop.py",
        "tests/test_codex_review_loop.py",
    ]


def test_parses_marker_json_block():
    stdout = f"log line\nCODEX_REVIEW_LOOP_RESULT:\n{_contract_payload(summary='From marker block.')}"

    result = parse_codex_review_loop_output(stdout=stdout, stderr="", exit_code=0)

    assert result.summary == "From marker block."
    assert result.diff_summary["line_count"] == 42


def test_malformed_json_does_not_raise_and_returns_parse_error():
    stdout = 'CODEX_REVIEW_LOOP_RESULT:\n{"status": "completed",'

    result = parse_codex_review_loop_output(stdout=stdout, stderr="", exit_code=0)

    assert result.status == "completed"
    assert result.changed_files == []
    assert result.validation["overall_status"] == "not_run"
    assert result.parse_error is not None
    assert "json" in result.parse_error.lower()


def test_plain_text_output_falls_back_to_deterministic_summary():
    result = parse_codex_review_loop_output(
        stdout="Applied changes to the executor and updated tests.\nValidation not run.",
        stderr="",
        exit_code=0,
    )

    assert result.status == "completed"
    assert result.summary == "Applied changes to the executor and updated tests. Validation not run."
    assert result.changed_files == []
    assert result.validation["overall_status"] == "not_run"
    assert result.parser_warning == "No contract JSON found in Codex output; using text fallback."


def test_validation_commands_are_preserved_and_failed_status_is_surfaced():
    result = parse_codex_review_loop_output(
        stdout=_contract_payload(
            status="failed",
            summary="Pytest failed.",
            validation={
                "commands": [
                    {
                        "command": "python -m pytest tests/test_codex_review_loop.py -q",
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": "AssertionError",
                    }
                ],
                "overall_status": "failed",
            },
        ),
        stderr="AssertionError",
        exit_code=1,
    )

    assert result.status == "failed"
    assert result.validation["overall_status"] == "failed"
    assert result.validation["commands"][0]["exit_code"] == 1
    assert result.exit_code == 1


def test_blocked_status_preserves_blocked_reason():
    result = parse_codex_review_loop_output(
        stdout=_contract_payload(
            status="blocked",
            summary="Blocked by safety policy.",
            blocked_reason="Protected path touched.",
        ),
        stderr="",
        exit_code=0,
    )

    assert result.status == "blocked"
    assert result.blocked_reason == "Protected path touched."
    assert result.summary == "Blocked by safety policy."
