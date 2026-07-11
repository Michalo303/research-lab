from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

import research_lab.execution as execution


MODULE_PATH = Path("research_lab/execution/ultracode_shim_v1.py")


def _request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "version": "ultracode_shim_request_v1",
        "proposed_changes": [
            {
                "path": "research_lab/execution/example_safe_module.py",
                "change_summary": "Add review-only helper logic.",
            }
        ],
        "allowed_paths": ["research_lab/execution/", "tests/"],
        "denied_paths": ["secrets/", ".env", "deploy/", "research_lab/hermes/", "research_lab/registry/"],
        "provenance": {"source": "unit_test"},
    }
    request.update(overrides)
    return request


def _run(request: dict[str, object]) -> dict[str, object]:
    return execution.build_ultracode_shim_artifact(copy.deepcopy(request))


def test_deterministic_artifact_for_same_proposal():
    first = _run(_request())
    second = _run(_request())

    assert first == second


def test_allowed_path_proposal_yields_review_required_not_auto_apply():
    result = _run(_request())

    assert result["review_status"] == "REVIEW_REQUIRED"
    assert result["rejection_reasons"] == []
    assert result["production_runtime_supported"] is False


def test_denied_path_proposal_yields_rejected():
    result = _run(
        _request(
            proposed_changes=[
                {
                    "path": "deploy/prod_release.sh",
                    "change_summary": "Trigger deployment update.",
                }
            ]
        )
    )

    assert result["review_status"] == "REJECTED"
    assert any("denied_paths" in reason for reason in result["rejection_reasons"])


def test_secret_like_text_yields_rejected():
    result = _run(
        _request(
            proposed_changes=[
                {
                    "path": "research_lab/execution/example_safe_module.py",
                    "change_summary": "Set OPENAI_API_KEY=super-secret-value for local run.",
                }
            ]
        )
    )

    assert result["review_status"] == "REJECTED"
    assert any("secret" in reason.lower() for reason in result["rejection_reasons"])


def test_provider_broker_deployment_registry_hermes_and_hetzner_actions_yield_rejected():
    result = _run(
        _request(
            proposed_changes=[
                {
                    "path": "research_lab/execution/example_safe_module.py",
                    "change_summary": "Call provider API, trigger broker order, update registry, deploy service, sync Hetzner, and write Hermes state.",
                }
            ]
        )
    )

    assert result["review_status"] == "REJECTED"
    joined = " ".join(result["rejection_reasons"]).lower()
    for token in ("provider", "broker", "registry", "deploy", "hermes", "hetzner"):
        assert token in joined


def test_stable_schema_on_pass_and_fail():
    passed = _run(_request())
    failed = _run(
        _request(
            proposed_changes=[
                {
                    "path": ".env",
                    "change_summary": "Enable production runtime and set token=secret.",
                }
            ]
        )
    )

    for result in (passed, failed):
        assert "ultracode_shim_version" in result
        assert "proposal_hash" in result
        assert "review_status" in result
        assert "rejection_reasons" in result
        assert "allowed_paths" in result
        assert "denied_paths" in result
        assert "changed_paths" in result


def test_no_writes_no_external_calls_no_promotion_no_production_runtime():
    result = _run(_request())

    assert result["provider_calls_used"] == 0
    assert result["registry_write_performed"] is False
    assert result["broker_actions_used"] == 0
    assert result["deployment_gate_run"] is False
    assert result["hermes_state_touched"] is False
    assert result["hetzner_state_touched"] is False
    assert result["promotion_performed"] is False
    assert result["production_runtime_supported"] is False


def test_module_does_not_import_forbidden_modules():
    forbidden_roots = (
        "research_lab.provider",
        "research_lab.providers",
        "research_lab.broker",
        "research_lab.hermes",
        "research_lab.registry",
        "research_lab.deployment",
        "research_lab.orchestration.daily",
        "research_lab.backtest",
        "socket",
        "subprocess",
        "requests",
        "aiohttp",
        "urllib",
        "http",
    )
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    for forbidden in forbidden_roots:
        assert forbidden not in imports
