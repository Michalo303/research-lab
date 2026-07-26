from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path

from research_lab.research.minervini_immutable_pilot_artifacts_v1 import (
    MinerviniPilotArtifactWriterV1,
)


PLAN_VERSION = "minervini_eodhd_acquisition_plan_v1"
PROVIDER_REQUEST_LIMIT = 24
RAW_START = "2010-01-01"
RAW_END = "2025-12-31"
EVALUATION_START = "2013-01-02"
_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
RawHttpGet = Callable[
    [str], tuple[bytes, Mapping[str, object]]
]


def build_minervini_eodhd_acquisition_plan_v1(
    *,
    active_rows: Sequence[Mapping[str, object]],
    delisted_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Normalize the universe and construct the remaining 22 requests."""
    active, active_duplicates = _normalize_universe(active_rows, "ACTIVE")
    delisted, delisted_duplicates = _normalize_universe(
        delisted_rows, "DELISTED"
    )
    active_codes = {row["code"] for row in active}
    delisted_codes = {row["code"] for row in delisted}
    collisions = sorted(active_codes & delisted_codes)
    blockers = (
        ["ACTIVE_DELISTED_IDENTITY_COLLISION"] if collisions else []
    )
    sample_symbols = _sample_symbols(active, delisted)
    request_specs = [
        {
            "name": "symbol_change_history",
            "artifact_name": "symbol-change-history.json",
            "endpoint_identity": _endpoint(
                "/symbol-change-history",
                {
                    "from": RAW_START,
                    "to": RAW_END,
                    "ex": "US",
                    "fmt": "json",
                },
            ),
        },
        {
            "name": "split_calendar",
            "artifact_name": "split-calendar-sample.json",
            "endpoint_identity": _endpoint(
                "/calendar/splits",
                {"from": RAW_START, "to": RAW_END, "fmt": "json"},
            ),
        },
        *[
            {
                "name": f"eod_{symbol}",
                "artifact_name": f"eod-{_artifact_symbol(symbol)}.json",
                "endpoint_identity": _endpoint(
                    f"/eod/{symbol}",
                    {
                        "from": RAW_START,
                        "to": RAW_END,
                        "period": "d",
                        "fmt": "json",
                    },
                ),
            }
            for symbol in sample_symbols
        ],
    ]
    if len(request_specs) + 2 != PROVIDER_REQUEST_LIMIT:
        raise RuntimeError("frozen provider request count changed.")
    result: dict[str, object] = {
        "version": PLAN_VERSION,
        "provider_request_limit": PROVIDER_REQUEST_LIMIT,
        "raw_start": RAW_START,
        "raw_end": RAW_END,
        "evaluation_start": EVALUATION_START,
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


def validate_minervini_eod_sample_v1(
    payload: object,
) -> dict[str, object]:
    if not isinstance(payload, list) or not payload:
        raise ValueError("EOD payload must be a non-empty array.")
    required = {
        "date",
        "open",
        "high",
        "low",
        "close",
        "adjusted_close",
        "volume",
    }
    previous: date | None = None
    gap_count = 0
    for row in payload:
        if not isinstance(row, Mapping) or not required.issubset(row):
            raise ValueError("EOD row is missing required fields.")
        current = _date(row["date"], "EOD date")
        if previous is not None:
            if current <= previous:
                raise ValueError("EOD dates must be strictly ordered and unique.")
            if (current - previous).days > 1:
                gap_count += 1
        values = {
            name: _finite_number(row[name], name)
            for name in ("open", "high", "low", "close", "adjusted_close")
        }
        volume = _finite_number(row["volume"], "volume")
        if (
            min(values.values()) <= 0
            or volume < 0
            or values["high"] < max(values["open"], values["close"])
            or values["low"] > min(values["open"], values["close"])
            or values["high"] < values["low"]
        ):
            raise ValueError("EOD OHLC or volume values are invalid.")
        previous = current
    return {
        "status": "VALID",
        "first_date": str(payload[0]["date"]),
        "last_date": str(payload[-1]["date"]),
        "row_count": len(payload),
        "gap_count": gap_count,
    }


def analyze_minervini_symbol_changes_v1(
    payload: object,
) -> dict[str, object]:
    if not isinstance(payload, list):
        raise ValueError("symbol-change payload must be an array.")
    normalized: list[dict[str, str]] = []
    targets: dict[str, set[str]] = {}
    graph: dict[str, set[str]] = {}
    for row in payload:
        if not isinstance(row, Mapping):
            raise ValueError("symbol-change rows must be objects.")
        old = _provider_code(row.get("old_symbol"), "old_symbol")
        new = _provider_code(row.get("new_symbol"), "new_symbol")
        effective = _date(row.get("effective"), "effective")
        if not date.fromisoformat(RAW_START) <= effective <= date.fromisoformat(
            RAW_END
        ):
            raise ValueError("symbol-change date is outside the frozen interval.")
        exchange = _text(row.get("exchange"), "exchange").upper()
        targets.setdefault(old, set()).add(new)
        graph.setdefault(old, set()).add(new)
        normalized.append(
            {
                "old_symbol": old,
                "new_symbol": new,
                "effective": effective.isoformat(),
                "exchange": exchange,
            }
        )
    blockers: list[str] = []
    if any(len(values) > 1 for values in targets.values()):
        blockers.append("AMBIGUOUS_SYMBOL_CHANGE_CHAIN")
    if _has_cycle(graph):
        blockers.append("SYMBOL_CHANGE_CYCLE")
    normalized.sort(
        key=lambda row: (
            row["effective"],
            row["old_symbol"],
            row["new_symbol"],
            row["exchange"],
        )
    )
    return {
        "record_count": len(normalized),
        "normalized_sha256": _hash(normalized),
        "blockers": sorted(blockers),
    }


def analyze_minervini_split_coverage_v1(
    payload: object,
) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError("split payload must be an object.")
    if isinstance(payload.get("splits"), list):
        rows = payload["splits"]
        if payload.get("from") != RAW_START or payload.get("to") != RAW_END:
            raise ValueError("split response does not cover the frozen interval.")
        page_count: int | None = None
        coverage_complete = True
    elif isinstance(payload.get("data"), list) and isinstance(
        payload.get("meta"), Mapping
    ):
        rows = payload["data"]
        meta = payload["meta"]
        total = _non_negative_int(meta.get("total"), "split total")
        limit = _positive_int(meta.get("limit"), "split limit")
        offset = _non_negative_int(meta.get("offset"), "split offset")
        page_count = math.ceil(total / limit) if total else 0
        coverage_complete = offset == 0 and len(rows) >= total
    else:
        raise ValueError("split payload schema is unsupported.")
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("split rows must be objects.")
        code = row.get("code", row.get("symbol"))
        split_date = row.get("split_date", row.get("date"))
        _provider_symbol(code, "split code")
        _date(split_date, "split date")
    return {
        "record_count": len(rows),
        "coverage_complete": coverage_complete,
        "page_count": page_count,
        "normalized_sha256": _hash(rows),
    }


def estimate_minervini_wide_acquisition_v1(
    *,
    deduplicated_symbol_count: int,
    sample_summaries: Sequence[Mapping[str, object]],
    split_metadata: Mapping[str, object],
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
    for summary in sample_summaries:
        if not isinstance(summary, Mapping):
            raise ValueError("sample summaries must be objects.")
        rows = _positive_int(summary.get("row_count"), "sample row_count")
        response_bytes = _positive_int(
            summary.get("response_bytes"), "sample response_bytes"
        )
        row_counts.append(rows)
        bytes_per_row.append(response_bytes / rows)
    coverage_complete = split_metadata.get("coverage_complete") is True
    page_count = split_metadata.get("page_count")
    if coverage_complete:
        split_lower = split_upper = max(
            1, int(page_count) if isinstance(page_count, int) else 1
        )
        exact = True
    elif isinstance(page_count, int) and page_count >= 1:
        split_lower = split_upper = page_count
        exact = True
    else:
        split_lower = 1
        split_upper = symbols
        exact = False
    fixed_requests = 3
    total_lower = symbols + split_lower + fixed_requests
    total_upper = symbols + split_upper + fixed_requests
    median_rows = sorted(row_counts)[len(row_counts) // 2]
    estimated_rows = symbols * median_rows
    storage_lower = math.floor(min(bytes_per_row) * estimated_rows)
    storage_upper = math.ceil(max(bytes_per_row) * estimated_rows)
    return {
        "full_history_eod_requests": symbols,
        "split_request_lower_bound": split_lower,
        "split_request_upper_bound": split_upper,
        "symbol_change_requests": 1,
        "total_http_requests_lower_bound": total_lower,
        "total_http_requests_upper_bound": total_upper,
        "total_call_units_lower_bound": total_lower,
        "total_call_units_upper_bound": total_upper,
        "total_call_units_exact": exact,
        "minimum_acquisition_days_at_100000_units": math.ceil(
            total_upper / 100_000
        ),
        "minimum_runtime_seconds_at_1000_per_minute": math.ceil(
            total_upper * 60 / 1_000
        ),
        "conservative_runtime_seconds_at_5_per_second": math.ceil(
            total_upper / 5
        ),
        "raw_storage_bytes_lower_bound": storage_lower,
        "raw_storage_bytes_upper_bound": storage_upper,
        "storage_estimate_status": "ESTIMATED_FROM_SAMPLE",
    }


def run_minervini_eodhd_acquisition_pilot_v1(
    *,
    output_dir: Path,
    expected_provider_requests: int,
    api_key: str | None = None,
    env: Mapping[str, str] | None = None,
    http_get: RawHttpGet | None = None,
    now_utc: Callable[[], str] | None = None,
) -> dict[str, object]:
    """Execute at most 24 read-only requests and persist immutable evidence."""
    if expected_provider_requests != PROVIDER_REQUEST_LIMIT:
        raise ValueError("expected_provider_requests must be exactly 24.")
    environment = os.environ if env is None else env
    key = (
        api_key if api_key is not None else environment.get("EODHD_API_KEY", "")
    ).strip()
    if not key:
        return _safety_result(
            {
                "version": "minervini_eodhd_acquisition_pilot_result_v1",
                "status": "MISSING_API_KEY",
                "provider_requests_used": 0,
                "stopping_ordinal": None,
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
                    blocker=f"PROVIDER_HTTP_{http_status}",
                ) from exc
            raise _PilotFailure("provider payload validation failed.", ordinal) from exc
        row_count = _payload_row_count(payload)
        record = writer.write_response(
            ordinal=ordinal,
            artifact_name=artifact_name,
            endpoint_identity=endpoint_identity,
            http_status=http_status,
            raw_bytes=raw,
            retrieved_at_utc=clock(),
            parsed_row_count=row_count,
            schema_status=schema_status,
        )
        if http_status != 200:
            if http_status == 403:
                raise _PilotFailure(
                    "provider capability unavailable.",
                    ordinal,
                    status="BLOCKED_PROVIDER_CAPABILITY",
                    blocker=f"PROVIDER_HTTP_{http_status}",
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
        plan = build_minervini_eodhd_acquisition_plan_v1(
            active_rows=active,
            delisted_rows=delisted,
        )
        if plan["blockers"]:
            raise _PilotFailure("universe identity is ambiguous.", request_count)
        symbol_payload, symbol_record = perform(
            endpoint_identity=plan["request_specs"][0]["endpoint_identity"],
            artifact_name=plan["request_specs"][0]["artifact_name"],
            validator=analyze_minervini_symbol_changes_v1,
        )
        symbol_analysis = analyze_minervini_symbol_changes_v1(symbol_payload)
        split_payload, split_record = perform(
            endpoint_identity=plan["request_specs"][1]["endpoint_identity"],
            artifact_name=plan["request_specs"][1]["artifact_name"],
            validator=analyze_minervini_split_coverage_v1,
        )
        split_analysis = analyze_minervini_split_coverage_v1(split_payload)
        sample_summaries: list[dict[str, object]] = []
        for symbol, spec in zip(
            plan["sample_symbols"],
            plan["request_specs"][2:],
            strict=True,
        ):
            _, record = perform(
                endpoint_identity=spec["endpoint_identity"],
                artifact_name=spec["artifact_name"],
                validator=validate_minervini_eod_sample_v1,
            )
            summary = dict(record["validation"])
            summary.update(
                {
                    "symbol": symbol,
                    "response_bytes": record["response_bytes"],
                    "response_sha256": record["response_sha256"],
                }
            )
            sample_summaries.append(summary)
        blockers = [
            *plan["blockers"],
            *symbol_analysis["blockers"],
        ]
        if split_analysis["record_count"] < 1:
            blockers.append("SPLIT_EVIDENCE_EMPTY")
        estimate = estimate_minervini_wide_acquisition_v1(
            deduplicated_symbol_count=plan["universe"][
                "deduplicated_code_count"
            ],
            sample_summaries=sample_summaries,
            split_metadata=split_analysis,
        )
        if request_count != PROVIDER_REQUEST_LIMIT:
            raise _PilotFailure("provider request count was not exactly 24.")
        status = (
            "READY_FOR_WIDE_ACQUISITION_APPROVAL"
            if not blockers
            else "BLOCKED_IDENTITY_AMBIGUITY"
        )
        result = _safety_result(
            {
                "version": "minervini_eodhd_acquisition_pilot_result_v1",
                "status": status,
                "provider_requests_used": request_count,
                "stopping_ordinal": None,
                "plan_sha256": plan["output_payload_sha256"],
                "universe": plan["universe"],
                "symbol_change_analysis": symbol_analysis,
                "split_analysis": split_analysis,
                "sample_summaries": sample_summaries,
                "estimate": estimate,
                "blockers": sorted(set(blockers)),
                "metadata_response_sha256": {
                    "symbol_changes": symbol_record["response_sha256"],
                    "splits": split_record["response_sha256"],
                },
            }
        )
    except (_PilotFailure, ValueError) as exc:
        stopping = (
            exc.ordinal
            if isinstance(exc, _PilotFailure) and exc.ordinal is not None
            else request_count
        )
        failure_status = (
            exc.status
            if isinstance(exc, _PilotFailure)
            else "FAILED_VALIDATION"
        )
        failure_blocker = (
            exc.blocker
            if isinstance(exc, _PilotFailure)
            else "PILOT_EXECUTION_FAILED"
        )
        result = _safety_result(
            {
                "version": "minervini_eodhd_acquisition_pilot_result_v1",
                "status": failure_status,
                "provider_requests_used": request_count,
                "stopping_ordinal": stopping,
                "blockers": [failure_blocker],
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


def _normalize_universe(
    rows: Sequence[Mapping[str, object]],
    status: str,
) -> tuple[list[dict[str, str | None]], int]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or not rows:
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
        if isin_value is None or isin_value == "":
            isin = None
        else:
            isin = _text(isin_value, "Isin").upper()
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
    active_ranked = _ranked(active)
    delisted_ranked = _ranked(delisted)
    active_codes = {str(row["code"]) for row in active}
    delisted_codes = {str(row["code"]) for row in delisted}
    if "AAPL" not in active_codes:
        raise ValueError("active universe does not contain AAPL.")
    if "ATVI" not in delisted_codes:
        raise ValueError("delisted universe does not contain ATVI.")
    chosen = ["SPY.US", "AAPL.US"]
    chosen.extend(
        f"{row['code']}.US"
        for row in active_ranked
        if row["code"] != "AAPL"
    )
    chosen_active = _unique(chosen)[:10]
    if len(chosen_active) != 10:
        raise ValueError("active universe cannot fill the frozen sample.")
    chosen = [*chosen_active, "ATVI.US"]
    chosen.extend(
        f"{row['code']}.US"
        for row in delisted_ranked
        if row["code"] != "ATVI"
    )
    result = _unique(chosen)[:20]
    if len(result) != 20:
        raise ValueError("delisted universe cannot fill the frozen sample.")
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


def _provider_code(value: object, name: str) -> str:
    code = _text(value, name).upper()
    if not _CODE_RE.fullmatch(code):
        raise ValueError(f"{name} is malformed.")
    return code


def _provider_symbol(value: object, name: str) -> str:
    symbol = _text(value, name).upper()
    parts = symbol.rsplit(".", 1)
    if len(parts) != 2 or not all(_CODE_RE.fullmatch(part) for part in parts):
        raise ValueError(f"{name} is malformed.")
    return symbol


def _date(value: object, name: str) -> date:
    text = _text(value, name)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date.") from exc


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    return number


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def _non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer.")
    return value


def _has_cycle(graph: Mapping[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(target) for target in graph.get(node, set())):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def _validate_non_empty_array(payload: object) -> dict[str, object]:
    if not isinstance(payload, list) or not payload:
        raise ValueError("provider universe payload must be a non-empty array.")
    return {"row_count": len(payload)}


def _payload_row_count(payload: object) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, Mapping):
        for key in ("splits", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
    return 0


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


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
