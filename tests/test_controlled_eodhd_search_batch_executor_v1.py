import copy

import pytest

from research_lab.execution.eodhd_exact_identity_capability_v2 import build_eodhd_exact_identity_capability_v2
from research_lab.execution.eodhd_exact_symbol_resolution_readiness_v3 import build_eodhd_exact_symbol_resolution_readiness_v3
from research_lab.execution.official_instrument_identity_manifest_v2 import build_official_instrument_identity_manifest


def _upstream():
    identity = build_official_instrument_identity_manifest()
    capability = build_eodhd_exact_identity_capability_v2(identity)
    readiness = build_eodhd_exact_symbol_resolution_readiness_v3({
        "version": "eodhd_exact_symbol_resolution_readiness_request_v3",
        "readiness_request_id": "M31P:V3",
        "m31i_manifest": identity,
        "expected_m31i_canonical_manifest_sha256": identity["canonical_manifest_sha256"],
        "m31n_capability_manifest": capability,
        "expected_m31n_canonical_capability_sha256": capability["canonical_capability_manifest_sha256"],
        "m31o_adapter_contract_version": "eodhd_approval_bound_search_metadata_adapter_v2",
        "provider_call_policy": "BOUNDED_EODHD_SEARCH_ONLY",
        "destination_policy": "PENDING_EXACT_SYMBOL_RESOLUTION_V3",
        "approval_policy": "EXTERNAL_HUMAN_APPROVAL_REQUIRED",
        "provenance": "M31I_M31N_M31O_ONLY",
    })
    return identity, capability, readiness


def _request():
    identity, capability, readiness = _upstream()
    return {
        "version": "controlled_eodhd_search_batch_execution_request_v1",
        "execution_request_id": "M31Q:DRY_RUN:FIXED",
        "mode": "DRY_RUN",
        "m31i_manifest": identity,
        "expected_m31i_canonical_manifest_sha256": identity["canonical_manifest_sha256"],
        "m31n_capability_manifest": capability,
        "expected_m31n_canonical_capability_manifest_sha256": capability["canonical_capability_manifest_sha256"],
        "m31p_readiness_result": readiness,
        "m31p_approval_manifest": readiness["approval_manifest"],
        "external_approved_approval_manifest_sha256": readiness["approval_manifest_sha256"],
        "external_approved_acquisition_plan_sha256": readiness["acquisition_plan_sha256"],
        "approved_budget_policy": copy.deepcopy(readiness["call_budgets"]),
        "m31o_adapter_contract_version": "eodhd_approval_bound_search_metadata_adapter_v2",
        "allow_provider_calls": False,
        "journal": object(),
        "result_store": object(),
        "provenance": "M31I_M31N_M31O_M31P_ONLY",
    }


def test_dry_run_validates_exact_approval_chain_without_side_effects():
    from research_lab.execution.controlled_eodhd_search_batch_executor_v1 import (
        run_controlled_eodhd_search_batch_v1,
    )

    request = _request()
    before = copy.deepcopy({key: value for key, value in request.items() if key not in {"journal", "result_store"}})
    journal, result_store = request["journal"], request["result_store"]
    output = run_controlled_eodhd_search_batch_v1(request)

    assert {key: value for key, value in request.items() if key not in {"journal", "result_store"}} == before
    assert request["journal"] is journal and request["result_store"] is result_store
    assert output["status"] == "DRY_RUN_VALIDATED"
    assert [record["sequence"] for record in output["schedule"]] == list(range(1, 16))
    assert output["safety_fields"]["provider_calls_used"] == 0
    assert output["safety_fields"]["provider_credentials_accessed"] is False
    assert output["safety_fields"]["journal_writes"] == 0
    assert output["safety_fields"]["result_store_writes"] == 0


def test_executor_is_available_from_execution_package():
    from research_lab.execution import run_controlled_eodhd_search_batch_v1

    assert callable(run_controlled_eodhd_search_batch_v1)


def test_approved_execution_creates_intent_markers_and_completes_exact_15_call_batch():
    from research_lab.execution.controlled_eodhd_search_batch_executor_v1 import (
        InMemoryExecutionJournal,
        InMemoryResultStore,
        run_controlled_eodhd_search_batch_v1,
    )

    request = _request()
    journal, store, calls = InMemoryExecutionJournal(), InMemoryResultStore(), []

    def client(path, parameters, credential):
        sequence = len(calls)
        assert "intent" in journal.states()
        assert f"started-{sequence + 1}" in journal.states()
        calls.append((path, parameters, credential))
        record = request["m31p_readiness_result"]["complete_plan"][sequence]
        return [{"Code": record["exchange_ticker"], "Exchange": record["eodhd_exchange_code"], "Name": record["legal_name"], "Type": record["accepted_response_types"][0], "Country": "x", "Currency": record["currency"], "ISIN": record["isin"]}]

    request.update({"mode": "APPROVED_EXECUTION", "allow_provider_calls": True, "journal": journal, "result_store": store, "provider_client": client, "credential": "credential-must-not-persist"})
    output = run_controlled_eodhd_search_batch_v1(request)

    assert output["status"] == "EXECUTION_COMPLETED"
    assert output["completed_sequences"] == list(range(1, 16))
    assert len(calls) == 15
    assert output["safety_fields"]["provider_calls_used"] == 15
    assert "credential-must-not-persist" not in str(journal.states())
    assert "credential-must-not-persist" not in str(store.items)


def test_started_without_matching_completed_refuses_automatic_replay_even_after_prior_completion():
    from research_lab.execution.controlled_eodhd_search_batch_executor_v1 import (
        InMemoryExecutionJournal,
        InMemoryResultStore,
        run_controlled_eodhd_search_batch_v1,
    )

    request = _request()
    journal = InMemoryExecutionJournal()
    journal.create_intent({"safe": True})
    journal.create_started(1, {"sequence": 1})
    journal.create_completed(1, {"sequence": 1})
    journal.create_started(2, {"sequence": 2})
    request.update({"mode": "APPROVED_EXECUTION", "allow_provider_calls": True, "journal": journal, "result_store": InMemoryResultStore(), "provider_client": lambda *_: pytest.fail("must not replay"), "credential": "secret"})

    output = run_controlled_eodhd_search_batch_v1(request)

    assert output["status"] == "MANUAL_REVIEW_REQUIRED_POSSIBLE_CALL_ALREADY_CONSUMED"


@pytest.mark.parametrize("field", ["external_approved_approval_manifest_sha256", "external_approved_acquisition_plan_sha256", "m31o_adapter_contract_version"])
def test_dry_run_rejects_authorization_mutation_before_any_injected_side_effect(field):
    from research_lab.execution.controlled_eodhd_search_batch_executor_v1 import ControlledExecutionError, run_controlled_eodhd_search_batch_v1

    request = _request()
    request[field] = "wrong"

    with pytest.raises(ControlledExecutionError):
        run_controlled_eodhd_search_batch_v1(request)


def test_filesystem_journal_refuses_overwrite_without_replacing_existing_evidence(tmp_path):
    from research_lab.execution.controlled_eodhd_search_batch_executor_v1 import ControlledExecutionError, FilesystemExecutionJournal

    journal = FilesystemExecutionJournal(tmp_path / "run")
    journal.create_intent({"evidence": "first"})
    with pytest.raises(ControlledExecutionError):
        journal.create_intent({"evidence": "replacement"})
    assert journal.states()["execution-intent.json"] == {"evidence": "first"}
