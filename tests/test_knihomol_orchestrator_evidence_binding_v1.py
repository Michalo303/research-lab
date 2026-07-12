from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest

from research_lab.execution.knihomol_orchestrator_evidence_binding_v1 import (
    build_knihomol_orchestrator_evidence_binding,
)
from research_lab.execution.robustness_decision_gate_v1 import (
    build_robustness_decision_gate,
)
from research_lab.execution.strategy_robustness_review_contract_v1 import (
    build_strategy_robustness_review_contract,
)


MODULE_PATH = Path("research_lab/execution/knihomol_orchestrator_evidence_binding_v1.py")


def _adapter_note(note_id: str, *, blocker: str, implementation_hint: str, source_sha256: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "note_id": note_id,
        "blocker": blocker,
        "book_id": f"book-{source_sha256[:12]}",
        "source_title": "Risk Control Book",
        "source_sha256": source_sha256,
        "source_passage_id": "passage-1111111111111111",
        "source_location": "page:10",
        "testable_rules": ["Check drawdown control under stress."],
        "compatible_builders": ["risk_overlay"],
        "implementation_hint": implementation_hint,
        "priority_score": 80.0,
    }
    payload.update(overrides)
    return payload


def _adapter_result(notes: list[dict[str, object]]) -> dict[str, object]:
    normalized_notes = []
    for note in copy.deepcopy(notes):
        note["implementation_hint"] = str(note["implementation_hint"]).strip()
        normalized_notes.append(note)
    return {
        "version": "knihomol_readonly_evidence_adapter_result_v1",
        "adapter_version": "knihomol_readonly_evidence_adapter_v1",
        "status": "SUCCESS",
        "evidence_purpose": "robustness_review",
        "requested_note_ids": [str(note["note_id"]) for note in normalized_notes],
        "notes": normalized_notes,
        "content_sha256": _sha256(normalized_notes),
        "source_hashes": {
            "index/book_index.json": "a" * 64,
            "extracted_notes/drawdown_fail.jsonl": "b" * 64,
        },
        "corpus_files_unchanged": True,
        "writes_performed": False,
        "promotion_performed": False,
        "provider_calls_used": 0,
        "network_used": False,
        "production_runtime_supported": False,
        "provenance": {"source": "unit_test"},
    }


def _request(adapter_result: dict[str, object], expected_note_ids: list[str] | None = None) -> dict[str, object]:
    note_ids = expected_note_ids or list(adapter_result["requested_note_ids"])  # type: ignore[index]
    return {
        "version": "knihomol_orchestrator_evidence_binding_request_v1",
        "adapter_result": adapter_result,
        "expected_note_ids": note_ids,
        "expected_adapter_content_sha256": adapter_result["content_sha256"],
        "mapping_policy_version": "knihomol_orchestrator_evidence_mapping_policy_v1",
        "provenance": {"source": "unit_test"},
    }


def _sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _run(request: dict[str, object]) -> dict[str, object]:
    return build_knihomol_orchestrator_evidence_binding(copy.deepcopy(request))


def _robustness_request() -> dict[str, object]:
    from tests.test_strategy_robustness_review_contract_v1 import _request as source_request

    return source_request()


def _decision_gate_request() -> dict[str, object]:
    from tests.test_robustness_decision_gate_v1 import _request as source_request

    return source_request()


def test_drawdown_walk_forward_mixed_notes_and_deterministic_ordering():
    adapter_result = _adapter_result(
        [
            _adapter_note(
                "note-2222222222222222",
                blocker="walk_forward_fail",
                implementation_hint="Use rolling OOS windows even when overfit appears in text.",
                source_sha256="b" * 64,
                source_passage_id="passage-2222222222222222",
                source_location="page:22",
                testable_rules=["selection_bias should not change supports"],
                compatible_builders=["walk_forward_review"],
            ),
            _adapter_note(
                "note-1111111111111111",
                blocker="drawdown_fail",
                implementation_hint="Lower exposure when selection_bias and overfit appear in free text.",
                source_sha256="a" * 64,
            ),
        ]
    )
    result = _run(_request(adapter_result, expected_note_ids=sorted(adapter_result["requested_note_ids"])))  # type: ignore[arg-type]
    second = _run(_request(adapter_result, expected_note_ids=sorted(adapter_result["requested_note_ids"])))  # type: ignore[arg-type]

    assert result == second
    assert result["mapping_policy_version"] == "knihomol_orchestrator_evidence_mapping_policy_v1"
    assert [note["note_id"] for note in result["validated_knihomol_evidence"]["notes"]] == [
        "note-1111111111111111",
        "note-2222222222222222",
    ]
    assert result["validated_knihomol_evidence"]["notes"][0] == {
        "note_id": "note-1111111111111111",
        "status": "validated",
        "topic": "drawdown_fail",
        "summary": "Lower exposure when selection_bias and overfit appear in free text.",
        "supports": ["drawdown"],
    }
    assert result["validated_knihomol_evidence"]["notes"][1] == {
        "note_id": "note-2222222222222222",
        "status": "validated",
        "topic": "walk_forward_fail",
        "summary": "Use rolling OOS windows even when overfit appears in text.",
        "supports": ["walk_forward"],
    }
    assert result["source_note_ids"] == ["note-1111111111111111", "note-2222222222222222"]
    assert result["provider_calls_used"] == 0
    assert result["network_used"] is False
    assert result["promotion_performed"] is False
    assert result["hermes_state_touched"] is False
    assert result["production_runtime_supported"] is False


def test_direct_acceptance_by_strategy_robustness_contract_and_decision_gate():
    adapter_result = _adapter_result(
        [_adapter_note("note-1111111111111111", blocker="drawdown_fail", implementation_hint="Add a defensive overlay.", source_sha256="a" * 64)]
    )
    bridge_result = _run(_request(adapter_result))

    robustness_request = _robustness_request()
    robustness_request["validated_knihomol_evidence"] = bridge_result["validated_knihomol_evidence"]
    robustness_result = build_strategy_robustness_review_contract(copy.deepcopy(robustness_request))
    assert robustness_result["knowledge_note_ids_used"] == ["note-1111111111111111"]

    decision_request = _decision_gate_request()
    decision_request["validated_knihomol_evidence"] = bridge_result["validated_knihomol_evidence"]
    decision_request["robustness_review_result"]["knowledge_note_ids_used"] = ["note-1111111111111111"]  # type: ignore[index]
    decision_result = build_robustness_decision_gate(copy.deepcopy(decision_request))
    assert decision_result["knowledge_note_ids_used"] == ["note-1111111111111111"]


def test_exact_note_id_binding_and_content_hash_binding():
    adapter_result = _adapter_result(
        [_adapter_note("note-1111111111111111", blocker="drawdown_fail", implementation_hint="Add a defensive overlay.", source_sha256="a" * 64)]
    )

    with pytest.raises(ValueError, match="expected_note_ids"):
        _run(_request(adapter_result, expected_note_ids=["note-9999999999999999"]))

    bad_request = _request(adapter_result)
    bad_request["expected_adapter_content_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="expected_adapter_content_sha256"):
        _run(bad_request)


def test_rejects_wrong_versions_and_non_success_adapter_status():
    adapter_result = _adapter_result(
        [_adapter_note("note-1111111111111111", blocker="drawdown_fail", implementation_hint="Add a defensive overlay.", source_sha256="a" * 64)]
    )
    bad_version = _request({**adapter_result, "version": "wrong"})
    with pytest.raises(ValueError, match="adapter_result.version"):
        _run(bad_version)

    bad_adapter_version = _request({**adapter_result, "adapter_version": "wrong"})
    with pytest.raises(ValueError, match="adapter_result.adapter_version"):
        _run(bad_adapter_version)

    bad_status = _request({**adapter_result, "status": "FAILED"})
    with pytest.raises(ValueError, match="adapter_result.status"):
        _run(bad_status)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("corpus_files_unchanged", False, "corpus_files_unchanged"),
        ("writes_performed", True, "writes_performed"),
        ("promotion_performed", True, "promotion_performed"),
        ("provider_calls_used", 1, "provider_calls_used"),
        ("network_used", True, "network_used"),
        ("production_runtime_supported", True, "production_runtime_supported"),
    ],
)
def test_rejects_unsafe_adapter_flags(field, value, message):
    adapter_result = _adapter_result(
        [_adapter_note("note-1111111111111111", blocker="drawdown_fail", implementation_hint="Add a defensive overlay.", source_sha256="a" * 64)]
    )
    adapter_result[field] = value
    with pytest.raises(ValueError, match=message):
        _run(_request(adapter_result))


def test_rejects_duplicate_notes_unsupported_blocker_missing_or_empty_implementation_hint_and_malformed_sha():
    duplicate_adapter_result = _adapter_result(
        [
            _adapter_note("note-1111111111111111", blocker="drawdown_fail", implementation_hint="One", source_sha256="a" * 64),
            _adapter_note("note-1111111111111111", blocker="drawdown_fail", implementation_hint="Two", source_sha256="a" * 64, source_passage_id="passage-2222222222222222"),
        ]
    )
    with pytest.raises(ValueError, match="must be unique"):
        _run(_request(duplicate_adapter_result))

    unsupported_blocker = _adapter_result(
        [_adapter_note("note-1111111111111111", blocker="overfit_risk", implementation_hint="Text", source_sha256="a" * 64)]
    )
    with pytest.raises(ValueError, match="unsupported in V1"):
        _run(_request(unsupported_blocker))

    missing_hint = _adapter_result(
        [_adapter_note("note-1111111111111111", blocker="drawdown_fail", implementation_hint="Text", source_sha256="a" * 64)]
    )
    missing_hint["notes"][0].pop("implementation_hint")  # type: ignore[index]
    with pytest.raises(ValueError, match="implementation_hint"):
        _run(_request(missing_hint))

    empty_hint = _adapter_result(
        [_adapter_note("note-1111111111111111", blocker="drawdown_fail", implementation_hint="   ", source_sha256="a" * 64)]
    )
    with pytest.raises(ValueError, match="implementation_hint"):
        _run(_request(empty_hint))

    malformed_sha = _adapter_result(
        [_adapter_note("note-1111111111111111", blocker="drawdown_fail", implementation_hint="Text", source_sha256="bad-sha")]
    )
    malformed_sha["content_sha256"] = "bad-sha"
    with pytest.raises(ValueError, match="adapter_result.content_sha256"):
        _run(_request(malformed_sha))


def test_source_adapter_result_remains_deeply_unchanged_and_no_files_are_written(tmp_path):
    adapter_result = _adapter_result(
        [_adapter_note("note-1111111111111111", blocker="drawdown_fail", implementation_hint="Add a defensive overlay.", source_sha256="a" * 64)]
    )
    request = _request(adapter_result)
    before = copy.deepcopy(adapter_result)
    files_before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    result = _run(request)
    files_after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    assert adapter_result == before
    assert files_before == files_after
    assert result["validated_knihomol_evidence"]["notes"][0]["supports"] == ["drawdown"]


def test_non_escalation_words_in_implementation_hint_and_testable_rules_do_not_change_supports():
    adapter_result = _adapter_result(
        [
            _adapter_note(
                "note-1111111111111111",
                blocker="drawdown_fail",
                implementation_hint="Add drawdown controls even if overfit and selection_bias are mentioned.",
                source_sha256="a" * 64,
                testable_rules=[
                    "The words overfit and selection_bias appear here but must not change supports."
                ],
            ),
            _adapter_note(
                "note-2222222222222222",
                blocker="walk_forward_fail",
                implementation_hint="Use walk-forward validation; selection_bias text must stay inert.",
                source_sha256="b" * 64,
                source_passage_id="passage-2222222222222222",
                source_location="page:22",
                compatible_builders=["walk_forward_review"],
                testable_rules=["Mention overfit here without escalation."],
            ),
        ]
    )
    result = _run(_request(adapter_result, expected_note_ids=sorted(adapter_result["requested_note_ids"])))  # type: ignore[arg-type]

    assert result["validated_knihomol_evidence"]["notes"][0]["supports"] == ["drawdown"]
    assert result["validated_knihomol_evidence"]["notes"][1]["supports"] == ["walk_forward"]
    supports_union = {item for note in result["validated_knihomol_evidence"]["notes"] for item in note["supports"]}
    assert "overfit" not in supports_union
    assert "selection_bias" not in supports_union


def test_module_does_not_import_provider_or_broker_modules():
    forbidden_roots = (
        "research_lab.runner",
        "research_lab.backtest",
        "research_lab.deployment_gate",
        "research_lab.registry",
        "research_lab.hermes",
        "requests",
        "urllib.request",
        "http",
        "socket",
        "ibapi",
        "ib_insync",
    )
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    for import_name in imports:
        assert not any(
            import_name == forbidden_root or import_name.startswith(forbidden_root + ".")
            for forbidden_root in forbidden_roots
        ), f"unexpected forbidden import: {import_name}"
