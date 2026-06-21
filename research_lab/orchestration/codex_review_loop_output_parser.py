from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any


VALID_STATUSES = {"completed", "blocked", "failed"}
VALID_VALIDATION_STATUSES = {"passed", "failed", "not_run", "unknown"}
RESULT_MARKER = "CODEX_REVIEW_LOOP_RESULT:"
FENCED_JSON_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)


@dataclass
class ParsedCodexReviewLoopOutput:
    status: str
    summary: str
    changed_files: list[str]
    diff_summary: dict[str, int]
    validation: dict[str, Any]
    blocked_reason: str | None
    raw_notes: str
    parser_warning: str | None = None
    parse_error: str | None = None
    source_format: str = "fallback_text"
    exit_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_codex_review_loop_output(
    *,
    stdout: str,
    stderr: str,
    exit_code: int | None,
) -> ParsedCodexReviewLoopOutput:
    stdout = stdout or ""
    stderr = stderr or ""
    candidate_json, source_format, extraction_error = _extract_candidate_json(stdout)
    if candidate_json is not None:
        try:
            payload = json.loads(candidate_json)
        except json.JSONDecodeError as exc:
            return _fallback_result(
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                parse_error=f"Failed to decode contract JSON: {exc.msg}.",
                source_format=source_format,
            )
        if not isinstance(payload, dict):
            return _fallback_result(
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                parse_error="Contract JSON must decode to an object.",
                source_format=source_format,
            )
        result = _from_payload(payload, exit_code=exit_code, source_format=source_format)
        if extraction_error and result.parser_warning is None:
            result.parser_warning = extraction_error
        return result

    parser_warning = extraction_error or "No contract JSON found in Codex output; using text fallback."
    return _fallback_result(
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        parser_warning=parser_warning,
        source_format=source_format,
    )


def _from_payload(payload: dict[str, Any], *, exit_code: int | None, source_format: str) -> ParsedCodexReviewLoopOutput:
    status = str(payload.get("status") or "").strip().lower()
    if status not in VALID_STATUSES:
        status = "failed" if exit_code not in (None, 0) else "completed"

    summary = _normalize_summary(payload.get("summary"))
    if not summary:
        summary = _default_summary(status=status, exit_code=exit_code)

    changed_files = _normalize_changed_files(payload.get("changed_files"))
    diff_summary = _normalize_diff_summary(payload.get("diff_summary"), changed_files)
    validation = _normalize_validation(payload.get("validation"))
    blocked_reason = _normalize_optional_text(payload.get("blocked_reason"))
    raw_notes = _normalize_optional_text(payload.get("raw_notes")) or ""

    parser_warning = None
    if status == "blocked" and not blocked_reason:
        parser_warning = "Blocked status did not include blocked_reason."

    return ParsedCodexReviewLoopOutput(
        status=status,
        summary=summary,
        changed_files=changed_files,
        diff_summary=diff_summary,
        validation=validation,
        blocked_reason=blocked_reason,
        raw_notes=raw_notes,
        parser_warning=parser_warning,
        parse_error=None,
        source_format=source_format,
        exit_code=exit_code,
    )


def _fallback_result(
    *,
    stdout: str,
    stderr: str,
    exit_code: int | None,
    parser_warning: str | None = None,
    parse_error: str | None = None,
    source_format: str,
) -> ParsedCodexReviewLoopOutput:
    status = "failed" if exit_code not in (None, 0) else "completed"
    summary = _fallback_summary(stdout=stdout, stderr=stderr, exit_code=exit_code, status=status)
    return ParsedCodexReviewLoopOutput(
        status=status,
        summary=summary,
        changed_files=[],
        diff_summary={
            "files_changed": 0,
            "insertions": 0,
            "deletions": 0,
            "line_count": 0,
        },
        validation={
            "commands": [],
            "overall_status": "not_run",
        },
        blocked_reason=None,
        raw_notes="",
        parser_warning=parser_warning,
        parse_error=parse_error,
        source_format=source_format,
        exit_code=exit_code,
    )


def _extract_candidate_json(stdout: str) -> tuple[str | None, str, str | None]:
    stripped = stdout.strip()
    if not stripped:
        return None, "empty", None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return stripped, "whole_stdout_json", None

    fenced_match = FENCED_JSON_RE.search(stdout)
    if fenced_match:
        return fenced_match.group(1).strip(), "fenced_json", None

    marker_index = stdout.find(RESULT_MARKER)
    if marker_index >= 0:
        candidate = stdout[marker_index + len(RESULT_MARKER) :].strip()
        return candidate or None, "marker_json", None if candidate else "Result marker found without JSON payload."

    return None, "fallback_text", None


def _normalize_changed_files(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        path = item.strip().replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        path = path.strip("/")
        if not path or path in seen:
            continue
        seen.add(path)
        normalized.append(path)
    return normalized


def _normalize_diff_summary(value: Any, changed_files: list[str]) -> dict[str, int]:
    payload = value if isinstance(value, dict) else {}
    files_changed = _coerce_int(payload.get("files_changed"), default=len(changed_files))
    insertions = _coerce_int(payload.get("insertions"), default=0)
    deletions = _coerce_int(payload.get("deletions"), default=0)
    line_count = _coerce_int(payload.get("line_count"), default=insertions + deletions)
    return {
        "files_changed": files_changed,
        "insertions": insertions,
        "deletions": deletions,
        "line_count": line_count,
    }


def _normalize_validation(value: Any) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    commands: list[dict[str, Any]] = []
    for item in payload.get("commands", []):
        if not isinstance(item, dict):
            continue
        commands.append(
            {
                "command": _normalize_optional_text(item.get("command")) or "",
                "exit_code": _coerce_optional_int(item.get("exit_code")),
                "stdout": _normalize_optional_text(item.get("stdout")) or "",
                "stderr": _normalize_optional_text(item.get("stderr")) or "",
            }
        )
    overall_status = _normalize_optional_text(payload.get("overall_status"))
    if overall_status:
        overall_status = overall_status.lower()
    if overall_status not in VALID_VALIDATION_STATUSES:
        overall_status = "not_run"
    return {
        "commands": commands,
        "overall_status": overall_status,
    }


def _normalize_summary(value: Any) -> str:
    text = _normalize_optional_text(value) or ""
    return " ".join(text.split())


def _default_summary(*, status: str, exit_code: int | None) -> str:
    if status == "blocked":
        return "Codex output reported a blocked result."
    if status == "failed":
        if exit_code not in (None, 0):
            return f"Codex CLI exit code {exit_code}."
        return "Codex output reported a failed result."
    return "Codex output reported a completed result."


def _fallback_summary(*, stdout: str, stderr: str, exit_code: int | None, status: str) -> str:
    stdout_summary = _normalize_summary(stdout)
    stderr_summary = _normalize_summary(stderr)
    if status == "failed":
        suffix = stderr_summary or stdout_summary
        if suffix:
            return f"Codex CLI exit code {exit_code}. {suffix}".strip()
        return _default_summary(status=status, exit_code=exit_code)
    if stdout_summary:
        return stdout_summary
    if stderr_summary:
        return stderr_summary if status == "completed" else f"Codex CLI exit code {exit_code}. {stderr_summary}".strip()
    return _default_summary(status=status, exit_code=exit_code)


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_int(value: Any, *, default: int) -> int:
    coerced = _coerce_optional_int(value)
    return default if coerced is None else coerced


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
