from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUEST_VERSION = "multi_asset_immutable_ohlcv_snapshot_contract_request_v1"
RESULT_VERSION = "multi_asset_immutable_ohlcv_snapshot_contract_result_v1"
CONTRACT_VERSION = "multi_asset_immutable_ohlcv_snapshot_contract_v1"
_ALIGNMENT_POLICIES = {"INDEPENDENT_SERIES", "UNION_SESSIONS", "INTERSECTION_SESSIONS"}
_OUTPUT_FILES = (
    "snapshot_request.json",
    "universe.json",
    "asset_manifest.json",
    "multi_asset_metadata.json",
    "checksums.json",
)


def build_multi_asset_immutable_ohlcv_snapshot_contract(request: dict[str, object]) -> dict[str, object]:
    validated = _validate_request(request)
    asset_series = sorted(validated["asset_series"], key=lambda item: item["instrument_id"])
    union_sessions = sorted({timestamp for item in asset_series for timestamp in item["timestamps"]})
    intersection_sessions = sorted(
        set(asset_series[0]["timestamps"]).intersection(*(set(item["timestamps"]) for item in asset_series[1:]))
    )
    aligned_session_count = 0
    if validated["alignment_policy"] == "UNION_SESSIONS":
        aligned_session_count = len(union_sessions)
    elif validated["alignment_policy"] == "INTERSECTION_SESSIONS":
        aligned_session_count = len(intersection_sessions)

    missing_session_summary = {
        item["instrument_id"]: [timestamp for timestamp in union_sessions if timestamp not in item["timestamps"]]
        for item in asset_series
    }
    universe_identity = {
        "universe_id": validated["universe_result"]["validated_universe"]["universe_id"],
        "universe_version": validated["universe_result"]["validated_universe"]["universe_version"],
        "as_of_timestamp": validated["universe_result"]["validated_universe"]["as_of_timestamp"],
        "universe_output_sha256": validated["universe_result"]["output_payload_sha256"],
    }
    validated_asset_series = [
        {
            "instrument_id": item["instrument_id"],
            "provider_symbol": item["provider_symbol"],
            "dataset_id": item["dataset_id"],
            "source_artifact_sha256": item["source_artifact_sha256"],
            "normalized_bars_sha256": item["normalized_bars_sha256"],
            "adapter_result_sha256": item["adapter_result_sha256"],
            "row_count": item["row_count"],
            "first_timestamp": item["first_timestamp"],
            "last_timestamp": item["last_timestamp"],
            "adjustment_status": item["adjustment_status"],
        }
        for item in asset_series
    ]
    immutable_manifest = {
        "snapshot_id": validated["snapshot_id"],
        "created_at": validated["created_at"],
        "alignment_policy": validated["alignment_policy"],
        "missing_session_policy": validated["missing_session_policy"],
        "universe_identity": universe_identity,
        "assets": validated_asset_series,
    }
    result: dict[str, Any] = {
        "version": RESULT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": validated["snapshot_id"],
        "created_at": validated["created_at"],
        "validated_asset_series": validated_asset_series,
        "universe_identity": universe_identity,
        "per_asset_identities": [
            {
                "instrument_id": item["instrument_id"],
                "provider_symbol": item["provider_symbol"],
                "dataset_id": item["dataset_id"],
            }
            for item in validated_asset_series
        ],
        "per_asset_source_hashes": {item["instrument_id"]: item["source_artifact_sha256"] for item in validated_asset_series},
        "per_asset_normalized_hashes": {
            item["instrument_id"]: item["normalized_bars_sha256"] for item in validated_asset_series
        },
        "row_counts": {item["instrument_id"]: item["row_count"] for item in validated_asset_series},
        "time_ranges": {
            item["instrument_id"]: {
                "first_timestamp": item["first_timestamp"],
                "last_timestamp": item["last_timestamp"],
            }
            for item in validated_asset_series
        },
        "alignment_summary": {
            "alignment_policy": validated["alignment_policy"],
            "aligned_session_count": aligned_session_count,
            "union_session_count": len(union_sessions),
            "intersection_session_count": len(intersection_sessions),
        },
        "missing_session_summary": missing_session_summary,
        "immutable_manifest": immutable_manifest,
        "network_used": False,
        "production_runtime_supported": False,
        "provenance": validated["provenance"],
        "input_sha256": _canonical_sha256(validated["hashable_request"]),
    }
    if validated["output_dir"] is not None:
        result["persisted_artifacts"] = _write_persisted_artifacts(
            output_dir=validated["output_dir"],
            snapshot_request=validated["hashable_request"],
            universe_result=validated["universe_result"],
            asset_manifest=validated_asset_series,
            metadata={
                "snapshot_id": validated["snapshot_id"],
                "created_at": validated["created_at"],
                "alignment_summary": result["alignment_summary"],
                "missing_session_summary": missing_session_summary,
                "immutable_manifest_sha256": _canonical_sha256(immutable_manifest),
            },
        )
    result["output_payload_sha256"] = _canonical_sha256(result)
    return result


def _validate_request(request: dict[str, object]) -> dict[str, Any]:
    payload = _required_mapping(request, name="request")
    _reject_unknown_fields(
        payload,
        allowed={
            "version",
            "snapshot_id",
            "universe_result",
            "asset_inputs",
            "expected_asset_identities",
            "expected_source_hashes",
            "expected_normalized_row_hashes",
            "alignment_policy",
            "missing_session_policy",
            "created_at",
            "output_dir",
            "provenance",
        },
        name="request",
    )
    version = _required_text(payload, "version")
    if version != REQUEST_VERSION:
        raise ValueError(f"version must be {REQUEST_VERSION}.")
    universe_result = _validate_universe_result(payload.get("universe_result"))
    serializable_universe_result = {
        key: value for key, value in universe_result.items() if not key.startswith("_")
    }
    asset_inputs = _required_list(payload.get("asset_inputs"), name="asset_inputs")
    expected_asset_identities = _validate_expected_identities(payload.get("expected_asset_identities"))
    expected_source_hashes = _validate_expected_hashes(payload.get("expected_source_hashes"), name="expected_source_hashes")
    expected_normalized_hashes = _validate_expected_hashes(
        payload.get("expected_normalized_row_hashes"),
        name="expected_normalized_row_hashes",
    )
    asset_series = _validate_asset_inputs(
        asset_inputs,
        universe_result=universe_result,
        expected_asset_identities=expected_asset_identities,
        expected_source_hashes=expected_source_hashes,
        expected_normalized_hashes=expected_normalized_hashes,
    )
    alignment_policy = _required_text(payload, "alignment_policy")
    if alignment_policy not in _ALIGNMENT_POLICIES:
        raise ValueError("alignment_policy is not supported.")
    output_dir = payload.get("output_dir")
    validated_output_dir = None if output_dir is None else _validated_output_dir(output_dir)
    created_at = _required_utc_timestamp(payload.get("created_at"), name="created_at")
    hashable_request = {
        "version": version,
        "snapshot_id": _required_text(payload, "snapshot_id"),
        "universe_result": serializable_universe_result,
        "asset_inputs": asset_inputs,
        "expected_asset_identities": expected_asset_identities,
        "expected_source_hashes": expected_source_hashes,
        "expected_normalized_row_hashes": expected_normalized_hashes,
        "alignment_policy": alignment_policy,
        "missing_session_policy": _required_text(payload, "missing_session_policy"),
        "created_at": created_at,
        "output_dir": None if validated_output_dir is None else str(validated_output_dir),
        "provenance": _validate_provenance(payload.get("provenance")),
    }
    return {
        "snapshot_id": hashable_request["snapshot_id"],
        "universe_result": serializable_universe_result,
        "asset_series": asset_series,
        "alignment_policy": alignment_policy,
        "missing_session_policy": hashable_request["missing_session_policy"],
        "created_at": created_at,
        "output_dir": validated_output_dir,
        "provenance": hashable_request["provenance"],
        "hashable_request": hashable_request,
    }


def _validate_universe_result(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="universe_result")
    if payload.get("version") != "point_in_time_universe_contract_result_v1":
        raise ValueError("universe_result.version must be point_in_time_universe_contract_result_v1.")
    if payload.get("contract_version") != "point_in_time_universe_contract_v1":
        raise ValueError("universe_result.contract_version must be point_in_time_universe_contract_v1.")
    if payload.get("production_runtime_supported") is not False:
        raise ValueError("universe_result.production_runtime_supported must be false.")
    _required_sha256(payload, "output_payload_sha256")
    validated_universe = _required_mapping(payload.get("validated_universe"), name="universe_result.validated_universe")
    included_instruments = _required_list(payload.get("included_instruments"), name="universe_result.included_instruments")
    instrument_ids = {str(_required_text(item, "instrument_id")) for item in included_instruments if isinstance(item, dict)}
    return {
        **payload,
        "validated_universe": validated_universe,
        "_included_instrument_ids": instrument_ids,
    }


def _validate_expected_identities(value: Any) -> list[dict[str, str]]:
    items = _required_list(value, name="expected_asset_identities")
    normalized: list[dict[str, str]] = []
    for raw in items:
        payload = _required_mapping(raw, name="expected_asset_identity")
        _reject_unknown_fields(payload, allowed={"instrument_id", "provider_symbol", "dataset_id"}, name="expected_asset_identity")
        normalized.append(
            {
                "instrument_id": _required_text(payload, "instrument_id"),
                "provider_symbol": _required_text(payload, "provider_symbol"),
                "dataset_id": _required_text(payload, "dataset_id"),
            }
        )
    return sorted(normalized, key=lambda item: item["instrument_id"])


def _validate_expected_hashes(value: Any, *, name: str) -> dict[str, str]:
    payload = _required_mapping(value, name=name)
    return {str(key): _required_sha256({"value": raw}, "value") for key, raw in sorted(payload.items(), key=lambda item: str(item[0]))}


def _validate_asset_inputs(
    items: list[Any],
    *,
    universe_result: dict[str, Any],
    expected_asset_identities: list[dict[str, str]],
    expected_source_hashes: dict[str, str],
    expected_normalized_hashes: dict[str, str],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    expected_identity_map = {item["instrument_id"]: item for item in expected_asset_identities}
    for raw in items:
        payload = _required_mapping(raw, name="asset_input")
        _reject_unknown_fields(
            payload,
            allowed={
                "instrument_id",
                "provider_symbol",
                "dataset_id",
                "adapter_result",
                "source_artifact_sha256",
                "normalized_bars_sha256",
                "row_count",
                "first_timestamp",
                "last_timestamp",
                "adjustment_status",
                "provenance",
            },
            name="asset_input",
        )
        instrument_id = _required_text(payload, "instrument_id")
        if instrument_id in seen_ids:
            raise ValueError("duplicate instrument_id is not allowed.")
        if instrument_id not in universe_result["_included_instrument_ids"]:
            raise ValueError("asset_input.instrument_id must exist in the included universe.")
        provider_symbol = _required_text(payload, "provider_symbol")
        dataset_id = _required_text(payload, "dataset_id")
        expected_identity = expected_identity_map.get(instrument_id)
        if expected_identity is None or expected_identity != {
            "instrument_id": instrument_id,
            "provider_symbol": provider_symbol,
            "dataset_id": dataset_id,
        }:
            raise ValueError("expected asset identity mismatch.")
        adapter_result = _validate_adapter_result(payload.get("adapter_result"))
        source_artifact_sha256 = _required_sha256(payload, "source_artifact_sha256")
        normalized_bars_sha256 = _required_sha256(payload, "normalized_bars_sha256")
        if source_artifact_sha256 != str(adapter_result["source_sha256"]) or expected_source_hashes.get(instrument_id) != source_artifact_sha256:
            raise ValueError("source hash mismatch.")
        if normalized_bars_sha256 != str(adapter_result["normalized_rows_hash"]) or expected_normalized_hashes.get(instrument_id) != normalized_bars_sha256:
            raise ValueError("normalized row hash mismatch.")
        row_count = _required_positive_int(payload, "row_count")
        if row_count != int(adapter_result["row_count"]):
            raise ValueError("row_count mismatch.")
        first_timestamp = _required_utc_timestamp(payload.get("first_timestamp"), name="first_timestamp")
        last_timestamp = _required_utc_timestamp(payload.get("last_timestamp"), name="last_timestamp")
        if first_timestamp != str(adapter_result["first_timestamp"]) or last_timestamp != str(adapter_result["last_timestamp"]):
            raise ValueError("time range mismatch.")
        timestamps = [
            str(row["timestamp"])
            for row in adapter_result["downstream_adapter_result"]["synthetic_bars"]
        ]
        normalized.append(
            {
                "instrument_id": instrument_id,
                "provider_symbol": provider_symbol,
                "dataset_id": dataset_id,
                "source_artifact_sha256": source_artifact_sha256,
                "normalized_bars_sha256": normalized_bars_sha256,
                "adapter_result_sha256": _canonical_sha256(adapter_result),
                "row_count": row_count,
                "first_timestamp": first_timestamp,
                "last_timestamp": last_timestamp,
                "adjustment_status": _required_text(payload, "adjustment_status"),
                "timestamps": timestamps,
            }
        )
        seen_ids.add(instrument_id)
    if sorted(expected_identity_map) != sorted(seen_ids):
        raise ValueError("expected asset identity mismatch.")
    return normalized


def _validate_adapter_result(value: Any) -> dict[str, Any]:
    payload = _required_mapping(value, name="adapter_result")
    if payload.get("version") != "local_ohlcv_file_input_adapter_result_v1":
        raise ValueError("adapter_result.version must be local_ohlcv_file_input_adapter_result_v1.")
    if payload.get("adapter_version") != "local_ohlcv_file_input_adapter_v1":
        raise ValueError("adapter_result.adapter_version must be local_ohlcv_file_input_adapter_v1.")
    if payload.get("status") != "SUCCESS":
        raise ValueError("adapter_result.status must be SUCCESS.")
    if payload.get("production_runtime_supported") is not False:
        raise ValueError("adapter_result.production_runtime_supported must be false.")
    if payload.get("network_used") is not False:
        raise ValueError("adapter_result.network_used must be false.")
    if payload.get("provider_calls_used") != 0:
        raise ValueError("adapter_result.provider_calls_used must be zero.")
    _required_sha256(payload, "source_sha256")
    _required_sha256(payload, "normalized_rows_hash")
    _required_mapping(payload.get("downstream_adapter_result"), name="adapter_result.downstream_adapter_result")
    return payload


def _validated_output_dir(value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("output_dir must be non-empty text.")
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError("output_dir must be an absolute path.")
    if path.exists() and any(path.iterdir()):
        raise ValueError("output_dir must be empty or absent.")
    return path.resolve(strict=False)


def _write_persisted_artifacts(
    *,
    output_dir: Path,
    snapshot_request: dict[str, Any],
    universe_result: dict[str, Any],
    asset_manifest: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    staged_hashes: dict[str, str] = {}
    staged_hashes["snapshot_request.json"] = _write_verified_json(output_dir / "snapshot_request.json", snapshot_request)
    staged_hashes["universe.json"] = _write_verified_json(output_dir / "universe.json", universe_result)
    staged_hashes["asset_manifest.json"] = _write_verified_json(output_dir / "asset_manifest.json", asset_manifest)
    staged_hashes["multi_asset_metadata.json"] = _write_verified_json(output_dir / "multi_asset_metadata.json", metadata)
    checksums = {
        "version": "multi_asset_immutable_ohlcv_snapshot_checksums_v1",
        "files": dict(sorted(staged_hashes.items())),
    }
    staged_hashes["checksums.json"] = _write_verified_json(output_dir / "checksums.json", checksums)
    complete_path = output_dir / "COMPLETE"
    complete_path.write_text(json.dumps({"status": "COMPLETE"}, sort_keys=True) + "\n", encoding="utf-8")
    if json.loads(complete_path.read_text(encoding="utf-8"))["status"] != "COMPLETE":
        raise OSError("COMPLETE verification failed.")
    return {
        "written_files": list(_OUTPUT_FILES) + ["COMPLETE"],
        "checksums": {**checksums["files"], "checksums.json": staged_hashes["checksums.json"]},
    }


def _write_verified_json(path: Path, payload: Any) -> str:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    expected_sha256 = _canonical_sha256(payload)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(encoded, encoding="utf-8")
    os.replace(temp_path, path)
    observed = json.loads(path.read_text(encoding="utf-8"))
    observed_sha256 = _canonical_sha256(observed)
    if observed_sha256 != expected_sha256:
        raise OSError(f"post-write verification failed for {path.name}")
    return observed_sha256


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object.")
    return dict(value)


def _required_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list.")
    return list(value)


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text.")
    return value.strip()


def _required_positive_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _required_sha256(payload: dict[str, Any], field: str) -> str:
    value = _required_text(payload, field)
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"{field} must be a lowercase sha256 hex digest.")
    return value


def _required_utc_timestamp(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text.")
    text = value.strip()
    if not text.endswith("Z"):
        raise ValueError(f"{name} must end with Z.")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid ISO-8601 UTC timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include UTC timezone information.")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_provenance(value: Any) -> dict[str, str | int | float | bool | None]:
    if value is None:
        return {}
    payload = _required_mapping(value, name="provenance")
    normalized: dict[str, str | int | float | bool | None] = {}
    for key, raw in payload.items():
        key_name = str(key).strip()
        if not key_name:
            raise ValueError("provenance keys must be non-empty text.")
        if raw is None or isinstance(raw, (str, int, float, bool)):
            normalized[key_name] = raw
            continue
        raise ValueError(f"provenance.{key_name} must be a JSON scalar.")
    return normalized


def _reject_unknown_fields(payload: dict[str, Any], *, allowed: set[str], name: str) -> None:
    unknown = sorted(key for key in payload if key not in allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown field(s): {', '.join(unknown)}")
