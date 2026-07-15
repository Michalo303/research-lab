"""Bounded, deterministic, review-only Fio long-term inventory validation."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from research_lab.execution.instrument_identity_execution_routing_v1 import (
    build_instrument_identity_execution_routing,
)


REQUEST_VERSION = "fio_manual_long_term_inventory_request_v1"
SOURCE_VERSION = "fio_manual_long_term_inventory_source_v1"
RESULT_VERSION = "fio_manual_long_term_inventory_result_v1"
CONTRACT_VERSION = "fio_manual_long_term_inventory_v1"

_REQUEST_FIELDS = {"version", "inventory_id", "account_id_redacted", "as_of_timestamp", "base_currency", "source_file_path", "expected_source_sha256", "maximum_bytes", "maximum_positions", "provenance"}
_SOURCE_FIELDS = {"version", "inventory_id", "account_id_redacted", "as_of_timestamp", "base_currency", "provenance", "positions"}
_POSITION_FIELDS = {"position_id", "identity_routing_result", "quantity", "currency", "average_cost", "reference_price", "reference_price_timestamp", "market_value", "acquisition_or_earliest_lot_date", "expected_holding_horizon", "provenance"}
_SHA_RE = re.compile(r"[0-9a-f]{64}")


def build_fio_manual_long_term_inventory(request: dict[str, object]) -> dict[str, object]:
    """Read one immutable local JSON inventory; never access a provider, broker, or Fio."""
    value = _validate_request(request)
    path = _safe_file_path(value["source_file_path"])
    before = _read_source(path, value["maximum_bytes"])
    source_sha256 = _sha_bytes(before)
    if source_sha256 != value["expected_source_sha256"]:
        raise ValueError("source hash mismatch")
    try:
        source = json.loads(before.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed source JSON") from exc
    positions = _validate_source(source, value)
    # A second bounded read makes source mutation fail closed before returning any result.
    if _read_source(path, value["maximum_bytes"]) != before:
        raise ValueError("source file changed during processing")
    valued = [item for item in positions if item["market_value"] is not None]
    unvalued = [item for item in positions if item["market_value"] is None]
    status = "PASS" if not unvalued else "REVIEW_REQUIRED"
    result: dict[str, Any] = {
        "version": RESULT_VERSION, "contract_version": CONTRACT_VERSION,
        "status": status, "validation_status": status, "inventory_id": value["inventory_id"],
        "account_id_redacted": value["account_id_redacted"], "as_of_timestamp": value["as_of_timestamp"],
        "base_currency": value["base_currency"], "validated_positions": positions,
        "valued_position_count": len(valued), "unvalued_position_count": len(unvalued),
        "exposures_by_currency": {value["base_currency"]: _decimal_text(sum((Decimal(item["market_value"]) for item in valued), Decimal("0")))},
        "totals": {"market_value": _decimal_text(sum((Decimal(item["market_value"]) for item in valued), Decimal("0"))), "locked_manual_market_value": _decimal_text(sum((Decimal(item["market_value"]) for item in valued), Decimal("0")))},
        "locked_manual_exposure": {"currency": value["base_currency"], "market_value": _decimal_text(sum((Decimal(item["market_value"]) for item in valued), Decimal("0"))), "position_count": len(positions)},
        "source_bytes": len(before), "source_sha256": source_sha256,
        "recomputed_child_hashes": {item["position_id"]: item["identity_routing_result"]["output_payload_sha256"] for item in positions},
        "input_sha256": value["input_sha256"],
        "findings": (["UNVALUED_POSITIONS_REQUIRE_REVIEW"] if unvalued else []),
        "provenance": value["provenance"], "filesystem_reads_performed": True,
        "filesystem_read_count": 2,
        "safety_flags": {"filesystem_writes_performed": False, "network_used": False, "provider_calls_used": 0, "provider_credentials_accessed": False, "broker_calls_used": 0, "broker_credentials_accessed": False, "Fio_actions_performed": False, "automatic_orders_generated": False, "automatic_liquidation_allowed": False, "production_runtime_supported": False},
    }
    result["output_payload_sha256"] = _sha(result)
    return copy.deepcopy(result)


def _validate_request(raw: dict[str, object]) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - _REQUEST_FIELDS:
        raise ValueError("unknown request field")
    if set(raw) != _REQUEST_FIELDS or raw.get("version") != REQUEST_VERSION:
        raise ValueError("invalid request fields or version")
    value: dict[str, Any] = copy.deepcopy(raw)
    for field in ("inventory_id", "account_id_redacted", "as_of_timestamp", "base_currency", "source_file_path"):
        value[field] = _text(value[field], field)
    _timestamp(value["as_of_timestamp"], "as_of_timestamp")
    value["expected_source_sha256"] = _sha_text(value["expected_source_sha256"], "expected_source_sha256")
    for field in ("maximum_bytes", "maximum_positions"):
        if isinstance(value[field], bool) or not isinstance(value[field], int) or value[field] <= 0:
            raise ValueError(f"{field} must be a positive integer")
    if not isinstance(value["provenance"], dict):
        raise ValueError("provenance must be an object")
    value["input_sha256"] = _sha(value)
    return value


def _safe_file_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError("unsafe path traversal")
    if any(candidate.is_symlink() for candidate in (path, *path.parents)) or not path.exists() or not path.is_file():
        raise ValueError("source path must be an existing non-symlink regular file")
    return path


def _read_source(path: Path, maximum_bytes: int) -> bytes:
    size = path.stat().st_size
    if size > maximum_bytes:
        raise ValueError("source exceeds maximum_bytes")
    data = path.read_bytes()
    if len(data) != size or len(data) > maximum_bytes:
        raise ValueError("source file changed during processing or exceeds maximum_bytes")
    return data


def _validate_source(source: Any, request: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(source, dict) or set(source) != _SOURCE_FIELDS or source.get("version") != SOURCE_VERSION:
        raise ValueError("malformed source fields or version")
    for field in ("inventory_id", "account_id_redacted", "as_of_timestamp", "base_currency"):
        if source.get(field) != request[field]:
            raise ValueError(f"source {field} mismatch")
    _timestamp(source["as_of_timestamp"], "source as_of_timestamp")
    if not isinstance(source["provenance"], dict) or not isinstance(source["positions"], list):
        raise ValueError("malformed source provenance or positions")
    if len(source["positions"]) > request["maximum_positions"]:
        raise ValueError("source exceeds maximum_positions")
    seen_ids: set[str] = set()
    seen_listings: set[str] = set()
    positions: list[dict[str, Any]] = []
    for raw in source["positions"]:
        item = _validate_position(raw, request["base_currency"])
        if item["position_id"] in seen_ids:
            raise ValueError("duplicate position ID")
        listing = item["identity_routing_result"]["identity_key"]
        if listing in seen_listings:
            raise ValueError("duplicate exact instrument listing")
        seen_ids.add(item["position_id"]); seen_listings.add(listing); positions.append(item)
    return sorted(positions, key=lambda item: item["position_id"])


def _validate_position(raw: Any, base_currency: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != _POSITION_FIELDS:
        raise ValueError("position contains unknown or missing field")
    item = copy.deepcopy(raw)
    for field in ("position_id", "currency", "expected_holding_horizon"):
        item[field] = _text(item[field], field)
    if item["currency"] != base_currency:
        raise ValueError("position currency is inconsistent with base currency")
    item["quantity"] = _decimal_text(_positive_decimal(item["quantity"], "quantity"))
    for field in ("average_cost", "reference_price", "market_value"):
        item[field] = None if item[field] is None else _decimal_text(_nonnegative_decimal(item[field], field))
    if item["reference_price_timestamp"] is not None:
        item["reference_price_timestamp"] = _timestamp(_text(item["reference_price_timestamp"], "reference_price_timestamp"), "reference_price_timestamp")
    if (item["reference_price"] is None) != (item["reference_price_timestamp"] is None):
        raise ValueError("reference price and timestamp must be supplied together")
    if item["acquisition_or_earliest_lot_date"] is not None:
        _date_text(_text(item["acquisition_or_earliest_lot_date"], "acquisition_or_earliest_lot_date"))
    if not isinstance(item["provenance"], dict):
        raise ValueError("position provenance must be an object")
    item["identity_routing_result"] = _verify_child(item["identity_routing_result"])
    item.update({"execution_route": "FIO_MANUAL_LONG_TERM", "manual_action_only": True,
                 "risk_inclusion_required": True, "automation_allowed": False,
                 "automatic_liquidation_allowed": False,
                 "automatic_order_generation_allowed": False})
    return item


def _verify_child(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("malformed M31A child result")
    child = copy.deepcopy(raw); declared = child.pop("output_payload_sha256", None)
    if _sha_text(declared, "child hash") != _sha(child):
        raise ValueError("mismatched M31A child hash")
    if child.get("validation_status") != "PASS" or child.get("contract_version") != "instrument_identity_execution_routing_v1":
        raise ValueError("malformed M31A child result")
    route = child.get("execution_route")
    required = {"route": "FIO_MANUAL_LONG_TERM", "manual_only": True, "risk_inclusion_required": True, "automation_allowed": False, "automatic_liquidation_allowed": False, "automatic_order_generation_allowed": False}
    if not isinstance(route, dict) or any(route.get(key) != expected for key, expected in required.items()):
        raise ValueError("invalid M31A child route")
    if not isinstance(child.get("identity_key"), str) or not child["identity_key"]:
        raise ValueError("malformed M31A child identity")
    try:
        rebuilt = build_instrument_identity_execution_routing(
            {"version": "instrument_identity_execution_routing_request_v1", "instrument": child.get("instrument"), "execution_route": route, "provenance": child.get("provenance")}
        )
    except ValueError as exc:
        raise ValueError("malformed M31A child result") from exc
    child["output_payload_sha256"] = declared
    if child != rebuilt:
        raise ValueError("malformed M31A child result")
    return child


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip(): raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def _sha_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value): raise ValueError(f"{name} must be a sha256")
    return value


def _timestamp(value: str, name: str) -> str:
    try: parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc: raise ValueError(f"invalid {name}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"invalid {name}")
    return value


def _date_text(value: str) -> None:
    try: datetime.fromisoformat(value + "T00:00:00")
    except ValueError as exc: raise ValueError("invalid acquisition_or_earliest_lot_date") from exc


def _nonnegative_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)): raise ValueError(f"{name} must be a finite Decimal")
    try: number = Decimal(str(value))
    except InvalidOperation as exc: raise ValueError(f"{name} must be a finite Decimal") from exc
    if not number.is_finite() or number < 0: raise ValueError(f"{name} must be non-negative")
    return number


def _positive_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(f"{name} must be positive; zero quantities are rejected")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be positive; zero quantities are rejected") from exc
    if not number.is_finite():
        raise ValueError(f"{name} must be positive; zero quantities are rejected")
    if number <= 0: raise ValueError(f"{name} must be positive; zero quantities are rejected")
    return number


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _sha(value: object) -> str:
    return _sha_bytes(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8"))


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
