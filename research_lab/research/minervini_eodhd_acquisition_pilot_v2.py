from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path

from research_lab.research.minervini_eodhd_acquisition_pilot_v1 import (
    validate_minervini_eod_sample_v1,
)
from research_lab.research.minervini_immutable_pilot_artifacts_v1 import (
    MinerviniPilotArtifactWriterV1,
)


PLAN_VERSION = "minervini_eodhd_acquisition_plan_v2"
PROVIDER_REQUEST_LIMIT = 24
RAW_START = "2010-01-01"
RAW_END = "2025-12-31"
EVALUATION_START = "2013-01-02"
SPLIT_LINEAGE_CLASSIFICATION = (
    "PROVIDER_REPORTED_EVENTS_NOT_COMPLETENESS_PROOF"
)
_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
RawHttpGet = Callable[[str], tuple[bytes, Mapping[str, object]]]


def build_minervini_eodhd_acquisition_plan_v2(
    *,
    active_rows: Sequence[Mapping[str, object]],
    delisted_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Build eleven paired EOD/split requests for atomic provider tickers."""
    active, active_duplicates = _normalize_universe(active_rows, "ACTIVE")
    delisted, delisted_duplicates = _normalize_universe(
        delisted_rows, "DELISTED"
    )
    active_codes = {str(row["code"]) for row in active}
    delisted_codes = {str(row["code"]) for row in delisted}
    collisions = sorted(active_codes & delisted_codes)
    blockers = (
        ["ACTIVE_DELISTED_IDENTITY_COLLISION"] if collisions else []
    )
    sample_symbols = _sample_symbols(active, delisted)
    request_specs: list[dict[str, str]] = []
    for symbol in sample_symbols:
        artifact_symbol = _artifact_symbol(symbol)
        request_specs.extend(
            [
                {
                    "kind": "eod",
                    "symbol": symbol,
                    "artifact_name": f"eod-{artifact_symbol}.json",
                    "endpoint_identity": _endpoint(
                        f"/eod/{symbol}",
                        {
                            "from": RAW_START,
                            "to": RAW_END,
                            "period": "d",
                            "fmt": "json",
                        },
                    ),
                },
                {
                    "kind": "splits",
                    "symbol": symbol,
                    "artifact_name": f"splits-{artifact_symbol}.json",
                    "endpoint_identity": _endpoint(
                        f"/splits/{symbol}",
                        {
                            "from": RAW_START,
                            "to": RAW_END,
                            "fmt": "json",
                        },
                    ),
                },
            ]
        )
    if len(request_specs) + 2 != PROVIDER_REQUEST_LIMIT:
        raise RuntimeError("frozen V2 provider request count changed.")
    result: dict[str, object] = {
        "version": PLAN_VERSION,
        "provider_request_limit": PROVIDER_REQUEST_LIMIT,
        "raw_start": RAW_START,
        "raw_end": RAW_END,
        "evaluation_start": EVALUATION_START,
        "identity_continuity_mode": "ATOMIC_PROVIDER_TICKER",
        "rename_continuity_supported": False,
        "wide_acquisition_scope": "ATOMIC_PROVIDER_TICKER_HISTORIES",
        "universe": {
            "active_count": len(active),
            "delisted_count": len(delisted),
            "active_duplicate_count": active_duplicates,
            "delisted_duplicate_count": delisted_duplicates,
            "active_delisted_collision_count": len(collisions),
            "active_delisted_collision_codes": collisions,
            "deduplicated_code_count": len(active_codes | delisted_codes),
            "active_identity_sha256": _hash(active),
            "delisted_identity_sha256": _hash(delisted),
        },
        "sample_symbols": sample_symbols,
        "request_specs": request_specs,
        "blockers": blockers,
    }
    result["output_payload_sha256"] = _hash(result)
    return result


def validate_minervini_symbol_splits_v2(
    payload: object,
) -> dict[str, object]:
    if not isinstance(payload, list):
        raise ValueError("split payload must be an array.")
    previous: date | None = None
    for row in payload:
        if not isinstance(row, Mapping):
            raise ValueError("split rows must be objects.")
        current = _date(row.get("date"), "split date")
        if not date.fromisoformat(RAW_START) <= current <= date.fromisoformat(
            RAW_END
        ):
            raise ValueError("split date is outside the frozen interval.")
        if previous is not None and current <= previous:
            raise ValueError("split dates must be strictly ordered and unique.")
        ratio = row.get("split")
        if not isinstance(ratio, str) or ratio.count("/") != 1:
            raise ValueError("split ratio must use new/old form.")
        numerator_text, denominator_text = ratio.split("/", 1)
        try:
            numerator = float(numerator_text)
            denominator = float(denominator_text)
        except ValueError as exc:
            raise ValueError("split ratio must be numeric.") from exc
        if (
            not math.isfinite(numerator)
            or not math.isfinite(denominator)
            or numerator <= 0
            or denominator <= 0
        ):
            raise ValueError("split ratio values must be positive and finite.")
        previous = current
    return {
        "status": "VALID",
        "record_count": len(payload),
        "first_date": str(payload[0]["date"]) if payload else None,
        "last_date": str(payload[-1]["date"]) if payload else None,
        "lineage_classification": SPLIT_LINEAGE_CLASSIFICATION,
    }


def estimate_minervini_atomic_acquisition_v2(
    *,
    deduplicated_symbol_count: int,
    sample_summaries: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    symbols = _positive_int(
        deduplicated_symbol_count, "deduplicated_symbol_count"
    )
    if (
        isinstance(sample_summaries, (str, bytes))
        or not isinstance(sample_summaries, Sequence)
        or not sample_summaries
    ):
        raise ValueError("sample_summaries must be a non-empty sequence.")
    bytes_per_row: list[float] = []
    row_counts: list[int] = []
    split_bytes: list[int] = []
    for summary in sample_summaries:
        if not isinstance(summary, Mapping):
            raise ValueError("sample summaries must be objects.")
        rows = _positive_int(
            summary.get("eod_row_count"), "sample eod_row_count"
        )
        eod_bytes = _positive_int(
            summary.get("eod_response_bytes"), "sample eod_response_bytes"
        )
        per_symbol_split_bytes = _non_negative_int(
            summary.get("split_response_bytes"),
            "sample split_response_bytes",
        )
        row_counts.append(rows)
        bytes_per_row.append(eod_bytes / rows)
        split_bytes.append(per_symbol_split_bytes)
    median_rows = sorted(row_counts)[len(row_counts) // 2]
    estimated_rows = symbols * median_rows
    storage_lower = math.floor(
        min(bytes_per_row) * estimated_rows + min(split_bytes) * symbols
    )
    storage_upper = math.ceil(
        max(bytes_per_row) * estimated_rows + max(split_bytes) * symbols
    )
    total_requests = 2 + symbols * 2
    return {
        "full_history_eod_requests": symbols,
        "per_symbol_split_requests": symbols,
        "universe_requests": 2,
        "total_http_requests": total_requests,
        "total_call_units": total_requests,
        "total_call_units_exact": True,
        "minimum_acquisition_days_at_100000_units": math.ceil(
            total_requests / 100_000
        ),
        "minimum_runtime_seconds_at_1000_per_minute": math.ceil(
            total_requests * 60 / 1_000
        ),
        "conservative_runtime_seconds_at_5_per_second": math.ceil(
            total_requests / 5
        ),
        "raw_storage_bytes_lower_bound": storage_lower,
        "raw_storage_bytes_upper_bound": storage_upper,
        "storage_estimate_status": "ESTIMATED_FROM_SAMPLE",
    }


def run_minervini_eodhd_acquisition_pilot_v2(
    *,
    output_dir: Path,
    expected_provider_requests: int,
    api_key: str | None = None,
    env: Mapping[str, str] | None = None,
    http_get: RawHttpGet | None = None,
    now_utc: Callable[[], str] | None = None,
) -> dict[str, object]:
    """Execute exactly 24 EOD-plan-compatible read-only requests."""
    if expected_provider_requests != PROVIDER_REQUEST_LIMIT:
        raise ValueError("expected_provider_requests must be exactly 24.")
    environment = os.environ if env is None else env
    key = (
        api_key if api_key is not None else environment.get("EODHD_API_KEY", "")
    ).strip()
    if not key:
        return _safety_result(
            {
                "version": "minervini_eodhd_acquisition_pilot_result_v2",
                "status": "MISSING_API_KEY",
                "provider_requests_used": 0,
                "stopping_ordinal": None,
                "blockers": ["EODHD_API_KEY_UNAVAILABLE"],
            }
        )
    writer = MinerviniPilotArtifactWriterV1.create(Path(output_dir))
    getter = http_get or _download_raw
    clock = now_utc or _now_utc
    request_count = 0

    def perform(
        *,
        endpoint_identity: str,
        artifact_name: str,
        validator: Callable[[object], dict[str, object] | None],
    ) -> tuple[object, dict[str, object]]:
        nonlocal request_count
        if request_count >= PROVIDER_REQUEST_LIMIT:
            raise _PilotFailure("provider request cap reached.")
        request_count += 1
        ordinal = request_count
        try:
            raw, metadata = getter(_authorized_url(endpoint_identity, key))
        except Exception as exc:
            raise _PilotFailure("provider request failed.", ordinal) from exc
        if not isinstance(raw, bytes):
            raise _PilotFailure("provider response was not bytes.", ordinal)
        secret_encodings = {
            key.encode("utf-8"),
            urllib.parse.quote(key, safe="").encode("ascii"),
            urllib.parse.quote_plus(key, safe="").encode("ascii"),
        }
        if any(secret and secret in raw for secret in secret_encodings):
            raise _PilotFailure(
                "provider response contained credential material.",
                ordinal,
                blocker="PROVIDER_RESPONSE_CONTAINED_SECRET",
            )
        http_status = metadata.get("http_status")
        if isinstance(http_status, bool) or not isinstance(http_status, int):
            http_status = 0
        try:
            payload = json.loads(raw.decode("utf-8"))
            validation = validator(payload)
            schema_status = "VALID"
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            writer.write_response(
                ordinal=ordinal,
                artifact_name=artifact_name,
                endpoint_identity=endpoint_identity,
                http_status=http_status,
                raw_bytes=raw,
                retrieved_at_utc=clock(),
                parsed_row_count=0,
                schema_status="INVALID",
            )
            if http_status == 403:
                raise _PilotFailure(
                    "provider capability unavailable.",
                    ordinal,
                    status="BLOCKED_PROVIDER_CAPABILITY",
                    blocker="PROVIDER_HTTP_403",
                ) from exc
            raise _PilotFailure("provider payload validation failed.", ordinal) from exc
        record = writer.write_response(
            ordinal=ordinal,
            artifact_name=artifact_name,
            endpoint_identity=endpoint_identity,
            http_status=http_status,
            raw_bytes=raw,
            retrieved_at_utc=clock(),
            parsed_row_count=_payload_row_count(payload),
            schema_status=schema_status,
        )
        if http_status != 200:
            if http_status == 403:
                raise _PilotFailure(
                    "provider capability unavailable.",
                    ordinal,
                    status="BLOCKED_PROVIDER_CAPABILITY",
                    blocker="PROVIDER_HTTP_403",
                )
            raise _PilotFailure("provider HTTP status was not 200.", ordinal)
        return payload, {**record, "validation": validation or {}}

    active_endpoint = _endpoint(
        "/exchange-symbol-list/US",
        {"type": "common_stock", "fmt": "json"},
    )
    delisted_endpoint = _endpoint(
        "/exchange-symbol-list/US",
        {"delisted": "1", "type": "common_stock", "fmt": "json"},
    )
    try:
        active, _ = perform(
            endpoint_identity=active_endpoint,
            artifact_name="active-common-stocks.json",
            validator=_validate_non_empty_array,
        )
        delisted, _ = perform(
            endpoint_identity=delisted_endpoint,
            artifact_name="delisted-common-stocks.json",
            validator=_validate_non_empty_array,
        )
        plan = build_minervini_eodhd_acquisition_plan_v2(
            active_rows=active,
            delisted_rows=delisted,
        )
        if plan["blockers"]:
            raise _PilotFailure(
                "universe identity is ambiguous.",
                request_count,
                status="BLOCKED_UNIVERSE_IDENTITY",
                blocker="ACTIVE_DELISTED_IDENTITY_COLLISION",
            )
        sample_summaries: list[dict[str, object]] = []
        for index, symbol in enumerate(plan["sample_symbols"]):
            eod_spec = plan["request_specs"][index * 2]
            split_spec = plan["request_specs"][index * 2 + 1]
            _, eod_record = perform(
                endpoint_identity=eod_spec["endpoint_identity"],
                artifact_name=eod_spec["artifact_name"],
                validator=validate_minervini_eod_sample_v1,
            )
            _, split_record = perform(
                endpoint_identity=split_spec["endpoint_identity"],
                artifact_name=split_spec["artifact_name"],
                validator=validate_minervini_symbol_splits_v2,
            )
            eod_validation = dict(eod_record["validation"])
            split_validation = dict(split_record["validation"])
            sample_summaries.append(
                {
                    "symbol": symbol,
                    "eod_first_date": eod_validation["first_date"],
                    "eod_last_date": eod_validation["last_date"],
                    "eod_row_count": eod_validation["row_count"],
                    "eod_gap_count": eod_validation["gap_count"],
                    "eod_response_bytes": eod_record["response_bytes"],
                    "eod_response_sha256": eod_record["response_sha256"],
                    "split_record_count": split_validation["record_count"],
                    "split_first_date": split_validation["first_date"],
                    "split_last_date": split_validation["last_date"],
                    "split_lineage_classification": split_validation[
                        "lineage_classification"
                    ],
                    "split_response_bytes": split_record["response_bytes"],
                    "split_response_sha256": split_record["response_sha256"],
                }
            )
        if request_count != PROVIDER_REQUEST_LIMIT:
            raise _PilotFailure("provider request count was not exactly 24.")
        estimate = estimate_minervini_atomic_acquisition_v2(
            deduplicated_symbol_count=plan["universe"][
                "deduplicated_code_count"
            ],
            sample_summaries=sample_summaries,
        )
        result = _safety_result(
            {
                "version": "minervini_eodhd_acquisition_pilot_result_v2",
                "status": "READY_FOR_ATOMIC_TICKER_ACQUISITION_APPROVAL",
                "provider_requests_used": request_count,
                "stopping_ordinal": None,
                "blockers": [],
                "plan_sha256": plan["output_payload_sha256"],
                "identity_continuity_mode": "ATOMIC_PROVIDER_TICKER",
                "rename_continuity_supported": False,
                "wide_acquisition_scope": (
                    "ATOMIC_PROVIDER_TICKER_HISTORIES"
                ),
                "universe": plan["universe"],
                "sample_summaries": sample_summaries,
                "estimate": estimate,
            }
        )
    except (_PilotFailure, ValueError) as exc:
        stopping = (
            exc.ordinal
            if isinstance(exc, _PilotFailure) and exc.ordinal is not None
            else request_count
        )
        status = (
            exc.status
            if isinstance(exc, _PilotFailure)
            else "FAILED_VALIDATION"
        )
        blocker = (
            exc.blocker
            if isinstance(exc, _PilotFailure)
            else "PILOT_EXECUTION_FAILED"
        )
        result = _safety_result(
            {
                "version": "minervini_eodhd_acquisition_pilot_result_v2",
                "status": status,
                "provider_requests_used": request_count,
                "stopping_ordinal": stopping,
                "blockers": [blocker],
                "identity_continuity_mode": "ATOMIC_PROVIDER_TICKER",
                "rename_continuity_supported": False,
            }
        )
    manifest_path = writer.finalize(result)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        **result,
        "result_manifest_sha256": manifest["result_manifest_sha256"],
        "manifest_path": str(manifest_path),
        "output_dir": str(writer.root),
    }


def _normalize_universe(
    rows: Sequence[Mapping[str, object]],
    status: str,
) -> tuple[list[dict[str, str | None]], int]:
    if (
        isinstance(rows, (str, bytes))
        or not isinstance(rows, Sequence)
        or not rows
    ):
        raise ValueError(f"{status.lower()} rows must be a non-empty sequence.")
    normalized: list[dict[str, str | None]] = []
    seen: set[tuple[str, str, str, str | None]] = set()
    duplicate_count = 0
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ValueError("universe rows must be objects.")
        instrument_type = _text(raw.get("Type"), "Type")
        if instrument_type.casefold() != "common stock":
            raise ValueError("universe rows must be Common Stock.")
        code = _text(raw.get("Code"), "Code").upper()
        if not _CODE_RE.fullmatch(code):
            raise ValueError("provider Code is malformed.")
        exchange = _text(raw.get("Exchange"), "Exchange").upper()
        currency = _text(raw.get("Currency"), "Currency").upper()
        isin_value = raw.get("Isin")
        isin = (
            None
            if isin_value is None or isin_value == ""
            else _text(isin_value, "Isin").upper()
        )
        identity = (code, exchange, currency, isin)
        if identity in seen:
            duplicate_count += 1
            continue
        seen.add(identity)
        normalized.append(
            {
                "status": status,
                "code": code,
                "exchange": exchange,
                "currency": currency,
                "isin": isin,
            }
        )
    normalized.sort(
        key=lambda row: (
            str(row["code"]),
            str(row["exchange"]),
            str(row["currency"]),
            str(row["isin"] or ""),
        )
    )
    return normalized, duplicate_count


def _sample_symbols(
    active: list[dict[str, str | None]],
    delisted: list[dict[str, str | None]],
) -> list[str]:
    active_codes = {str(row["code"]) for row in active}
    delisted_codes = {str(row["code"]) for row in delisted}
    if "AAPL" not in active_codes:
        raise ValueError("active universe does not contain AAPL.")
    if "ATVI" not in delisted_codes:
        raise ValueError("delisted universe does not contain ATVI.")
    active_candidates = [
        "SPY.US",
        "AAPL.US",
        *(
            f"{row['code']}.US"
            for row in _ranked(active)
            if row["code"] != "AAPL"
        ),
    ]
    active_sample = _unique(active_candidates)[:6]
    if len(active_sample) != 6:
        raise ValueError("active universe cannot fill the frozen V2 sample.")
    candidates = [
        *active_sample,
        "ATVI.US",
        *(
            f"{row['code']}.US"
            for row in _ranked(delisted)
            if row["code"] != "ATVI"
        ),
    ]
    result = _unique(candidates)[:11]
    if len(result) != 11:
        raise ValueError("delisted universe cannot fill the frozen V2 sample.")
    return result


def _ranked(
    rows: list[dict[str, str | None]],
) -> list[dict[str, str | None]]:
    return sorted(
        rows,
        key=lambda row: hashlib.sha256(
            (
                f"{row['status']}:{row['code']}:{row['exchange']}:"
                f"{row['currency']}:{row['isin'] or ''}"
            ).encode("utf-8")
        ).hexdigest(),
    )


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _endpoint(path: str, query: Mapping[str, str]) -> str:
    return f"https://eodhd.com/api{path}?{urllib.parse.urlencode(query)}"


def _artifact_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", symbol)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be normalized non-empty text.")
    return value


def _date(value: object, name: str) -> date:
    text = _text(value, name)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date.") from exc


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def _non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer.")
    return value


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _validate_non_empty_array(payload: object) -> dict[str, object]:
    if not isinstance(payload, list) or not payload:
        raise ValueError("provider universe payload must be a non-empty array.")
    return {"row_count": len(payload)}


def _payload_row_count(payload: object) -> int:
    return len(payload) if isinstance(payload, list) else 0


def _authorized_url(endpoint_identity: str, api_key: str) -> str:
    parsed = urllib.parse.urlparse(endpoint_identity)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if any(name.casefold() == "api_token" for name, _ in query):
        raise ValueError("endpoint identity unexpectedly contains a token.")
    query.append(("api_token", api_key))
    return urllib.parse.urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            urllib.parse.urlencode(query),
            "",
        )
    )


def _download_raw(url: str) -> tuple[bytes, Mapping[str, object]]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "research-lab/0.1 research-only"},
    )
    opener = urllib.request.build_opener(_SameHostRedirectHandler())
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read(100 * 1024 * 1024 + 1)
            if len(raw) > 100 * 1024 * 1024:
                raise ValueError("provider response exceeded 100 MiB.")
            return raw, {
                "http_status": int(getattr(response, "status", 200)),
                "content_type": str(response.headers.get("Content-Type", "")),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read(100 * 1024 * 1024 + 1)
        if len(raw) > 100 * 1024 * 1024:
            raw = b""
        return raw, {"http_status": int(exc.code)}


class _SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).hostname != "eodhd.com":
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "off-domain redirect rejected",
                headers,
                fp,
            )
        return super().redirect_request(
            req, fp, code, msg, headers, newurl
        )


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _safety_result(result: dict[str, object]) -> dict[str, object]:
    result.update(
        {
            "network_used": bool(result["provider_requests_used"]),
            "broker_actions_used": 0,
            "registry_write_performed": False,
            "promotion_performed": False,
            "deployment_performed": False,
            "production_runtime_supported": False,
            "wide_acquisition_authorized": False,
        }
    )
    result["output_payload_sha256"] = _hash(result)
    return result


class _PilotFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        ordinal: int | None = None,
        *,
        status: str = "FAILED_VALIDATION",
        blocker: str = "PILOT_EXECUTION_FAILED",
    ) -> None:
        super().__init__(message)
        self.ordinal = ordinal
        self.status = status
        self.blocker = blocker
