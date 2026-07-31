from __future__ import annotations

import copy
import json
import threading
import time
import urllib.parse
from pathlib import Path

import pandas as pd
import pytest

import research_lab.research.eodhd_us_equity_universe_acquisition_v1 as acquisition_module
from research_lab.research.eodhd_us_equity_universe_acquisition_v1 import (
    RetryableProviderFailure,
    _RateLimiter,
    _download_symbol_histories,
    _last_spy_session_per_month,
    _normalize_identity_universe,
    _response_size_cap,
    _validate_bulk_response,
    _validate_response_metadata,
    _worst_case_call_units,
    build_eodhd_us_equity_acquisition_plan_v1,
    run_eodhd_us_equity_universe_acquisition_v1,
)


def _request(tmp_path: Path) -> dict[str, object]:
    return {
        "version": "eodhd_us_equity_universe_acquisition_request_v1",
        "acquisition_id": "EODHD-US-EQUITY-2006-2022-V1",
        "output_dir": str((tmp_path / "final").resolve()),
        "provider": "EODHD",
        "approved_host": "eodhd.com",
        "start_date": "2006-01-01",
        "end_date": "2022-12-31",
        "maximum_call_units": 90_000,
        "maximum_attempts_per_request": 2,
        "history_concurrency": 8,
        "timeout_seconds": 90,
        "maximum_symbol_response_bytes": 2_000_000,
        "maximum_bulk_response_bytes": 20_000_000,
        "provenance": {"source": "operator_approved_eodhd_acquisition_v1"},
    }


def test_plan_freezes_contract_and_contains_no_credential_surface(tmp_path: Path) -> None:
    result = build_eodhd_us_equity_acquisition_plan_v1(_request(tmp_path))

    assert result["version"] == "eodhd_us_equity_universe_acquisition_plan_v1"
    assert result["acquisition_id"] == "EODHD-US-EQUITY-2006-2022-V1"
    assert result["interval"] == {"start": "2006-01-01", "end": "2022-12-31"}
    assert result["maximum_call_units"] == 90_000
    assert result["maximum_attempts_per_request"] == 2
    assert result["history_concurrency"] == 8
    assert result["minimum_free_disk_bytes"] == 8_000_000_000
    assert result["supported_exchange_mics"] == {
        "AMEX": "XASE",
        "NASDAQ": "XNAS",
        "NYSE": "XNYS",
        "NYSE MKT": "XASE",
    }
    assert [item["kind"] for item in result["initial_requests"]] == [
        "ACTIVE_COMMON_STOCKS",
        "DELISTED_COMMON_STOCKS",
        "SPY_SESSION_PROXY",
    ]
    serialized = repr(result)
    assert "api_token" not in serialized
    assert "EODHD_API_KEY" not in serialized
    assert result["sealed_oos_opened"] is False
    assert len(result["request_sha256"]) == 64
    assert len(result["canonical_plan_sha256"]) == 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "OTHER"),
        ("approved_host", "example.com"),
        ("start_date", "2007-01-01"),
        ("end_date", "2023-01-01"),
        ("maximum_call_units", 89_999),
        ("maximum_attempts_per_request", 3),
        ("history_concurrency", 9),
    ],
)
def test_plan_rejects_frozen_contract_drift(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    request = _request(tmp_path)
    request[field] = value

    with pytest.raises(ValueError):
        build_eodhd_us_equity_acquisition_plan_v1(request)


def test_plan_rejects_unknown_fields_relative_and_repository_output(tmp_path: Path) -> None:
    unknown = _request(tmp_path)
    unknown["api_token"] = "must-not-be-accepted"
    with pytest.raises(ValueError, match="fields"):
        build_eodhd_us_equity_acquisition_plan_v1(unknown)

    relative = _request(tmp_path)
    relative["output_dir"] = "relative/output"
    with pytest.raises(ValueError, match="absolute"):
        build_eodhd_us_equity_acquisition_plan_v1(relative)

    repository = _request(tmp_path)
    repository["output_dir"] = str((Path(__file__).resolve().parents[1] / "private-output").resolve())
    with pytest.raises(ValueError, match="outside"):
        build_eodhd_us_equity_acquisition_plan_v1(repository)


def test_identity_normalization_filters_and_rejects_ambiguous_codes() -> None:
    active = [
        {"Code": "AAA", "Exchange": "NASDAQ", "Currency": "USD", "Type": "Common Stock", "Isin": "US1"},
        {"Code": "AAA", "Exchange": "NASDAQ", "Currency": "USD", "Type": "Common Stock", "Isin": "US1"},
        {"Code": "BBB", "Exchange": "NYSE", "Currency": "USD", "Type": "Common Stock", "Isin": "US2"},
        {"Code": "FUND", "Exchange": "NYSE", "Currency": "USD", "Type": "ETF", "Isin": "US3"},
        {"Code": "OTC", "Exchange": "PINK", "Currency": "USD", "Type": "Common Stock", "Isin": "US4"},
    ]
    delisted = [
        {"Code": "BBB", "Exchange": "NYSE", "Currency": "USD", "Type": "Common Stock", "Isin": "DIFFERENT"},
        {"Code": "CCC", "Exchange": "NYSE MKT", "Currency": "USD", "Type": "Common Stock", "Isin": None},
        {"Code": "EUR", "Exchange": "NASDAQ", "Currency": "EUR", "Type": "Common Stock", "Isin": "EU1"},
    ]

    result = _normalize_identity_universe(active, delisted)

    assert [item["code"] for item in result["identities"]] == ["AAA", "CCC"]
    assert result["identities"][0]["status"] == "ACTIVE"
    assert result["identities"][1]["exchange_mic"] == "XASE"
    assert result["exact_duplicate_count"] == 1
    assert result["ambiguous_codes"] == ["BBB"]
    assert result["filtered_row_count"] == 3


def test_spy_sessions_choose_last_observed_date_per_month_without_future_rows() -> None:
    rows = [
        {"date": "2022-01-03"},
        {"date": "2022-01-31"},
        {"date": "2022-02-01"},
        {"date": "2022-02-28"},
        {"date": "2022-12-30"},
    ]

    assert _last_spy_session_per_month(rows, start="2022-01-01", end="2022-12-31") == [
        "2022-01-31",
        "2022-02-28",
        "2022-12-30",
    ]

    future = copy.deepcopy(rows)
    future.append({"date": "2023-01-03"})
    with pytest.raises(ValueError, match="interval"):
        _last_spy_session_per_month(future, start="2022-01-01", end="2022-12-31")


def _eod_row(day: str, close: float = 10.0) -> dict[str, object]:
    return {
        "date": day,
        "open": close - 0.2,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "adjusted_close": close,
        "volume": 2_000_000,
    }


def _provider_fixture():
    active = [
        {"Code": "AAA", "Exchange": "NASDAQ", "Currency": "USD", "Type": "Common Stock", "Isin": "US1"}
    ]
    delisted = [
        {"Code": "DDD", "Exchange": "NYSE", "Currency": "USD", "Type": "Common Stock", "Isin": "US2"}
    ]
    month_ends = [stamp.date().isoformat() for stamp in pd.date_range("2006-01-31", "2022-12-31", freq="ME")]
    spy = [_eod_row(day, 100.0) for day in month_ends]
    histories = {
        "AAA.US": [_eod_row("2006-01-31"), _eod_row("2006-02-28", 11.0)],
        "DDD.US": [_eod_row("2006-01-31"), _eod_row("2006-02-28", 9.0)],
    }
    calls: list[str] = []

    def fake_download(url: str, *, timeout_seconds: int, max_response_bytes: int):
        assert timeout_seconds == 90
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        assert query["api_token"] == ["unit-test-secret"]
        calls.append(url)
        if parsed.path == "/api/exchange-symbol-list/US":
            payload = delisted if query.get("delisted") == ["1"] else active
        elif parsed.path == "/api/eod/SPY.US":
            payload = spy
        elif parsed.path == "/api/eod-bulk-last-day/US":
            day = query["date"][0]
            payload = [
                {
                    "code": "AAA",
                    "exchange_short_name": "NASDAQ",
                    **_eod_row(day),
                },
                {
                    "code": "DDD",
                    "exchange_short_name": "NYSE",
                    **_eod_row(day),
                },
            ]
        elif parsed.path.startswith("/api/eod/"):
            symbol = urllib.parse.unquote(parsed.path.removeprefix("/api/eod/"))
            payload = histories[symbol]
        else:
            raise AssertionError(parsed.path)
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        assert len(raw) <= max_response_bytes
        return raw, {"http_status": 200, "final_url": url, "response_bytes": len(raw)}

    return fake_download, calls


def test_disk_preflight_stops_before_bulk_or_history_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_download, calls = _provider_fixture()
    monkeypatch.setenv("EODHD_API_KEY", "unit-test-secret")
    monkeypatch.setattr(acquisition_module, "_download_raw", fake_download)
    monkeypatch.setattr(
        acquisition_module,
        "_available_disk_bytes",
        lambda _path: acquisition_module.MINIMUM_FREE_DISK_BYTES - 1,
    )

    result = run_eodhd_us_equity_universe_acquisition_v1(_request(tmp_path))

    assert result["status"] == "INSUFFICIENT_DISK_SPACE"
    assert result["provider_call_units_used"] == 3
    assert result["provider_http_requests_used"] == 3
    assert len(calls) == 3


def test_missing_api_key_stops_before_writes_or_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EODHD_API_KEY", raising=False)
    monkeypatch.setattr(
        acquisition_module,
        "_download_raw",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network used")),
    )

    result = run_eodhd_us_equity_universe_acquisition_v1(_request(tmp_path))

    assert result["status"] == "EODHD_API_KEY_UNAVAILABLE"
    assert result["provider_call_units_used"] == 0
    assert not Path(_request(tmp_path)["output_dir"]).exists()
    assert not list(tmp_path.glob("*.partial"))


def test_download_phases_are_resumable_hash_bound_and_secret_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_download, calls = _provider_fixture()
    monkeypatch.setenv("EODHD_API_KEY", "unit-test-secret")
    monkeypatch.setattr(acquisition_module, "_download_raw", fake_download)

    first = run_eodhd_us_equity_universe_acquisition_v1(_request(tmp_path))

    assert first["status"] == "DOWNLOAD_COMPLETE_PENDING_SELECTION"
    assert first["provider_http_requests_used"] == 209
    assert first["provider_call_units_used"] == 20_405
    assert first["identity_count"] == 2
    assert first["month_end_count"] == 204
    assert len(calls) == 209
    assert not Path(_request(tmp_path)["output_dir"]).exists()
    staging = Path(first["staging_dir"])
    assert staging.is_dir()
    assert (staging / "state.sqlite").is_file()
    assert (staging / "identity_universe.json").is_file()
    assert len(list((staging / "raw" / "bulk").glob("*.json.gz"))) == 204
    assert len(list((staging / "ohlcv-full").rglob("*.csv"))) == 2
    assert not (staging / "ohlcv").exists()
    assert "unit-test-secret" not in repr(first)
    assert "api_token" not in repr(first)

    calls.clear()
    second = run_eodhd_us_equity_universe_acquisition_v1(_request(tmp_path))
    assert second["status"] == "DOWNLOAD_COMPLETE_PENDING_SELECTION"
    assert second["provider_http_requests_used"] == 209
    assert second["provider_call_units_used"] == 20_405
    assert calls == []


def test_retryable_failure_retries_once_and_accounts_both_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_download, calls = _provider_fixture()
    failures = 0

    def flaky(url: str, *, timeout_seconds: int, max_response_bytes: int):
        nonlocal failures
        if "/exchange-symbol-list/US" in url and "delisted=" not in url and failures == 0:
            failures += 1
            raise RetryableProviderFailure("retryable")
        return fake_download(url, timeout_seconds=timeout_seconds, max_response_bytes=max_response_bytes)

    monkeypatch.setenv("EODHD_API_KEY", "unit-test-secret")
    monkeypatch.setattr(acquisition_module, "_download_raw", flaky)

    result = run_eodhd_us_equity_universe_acquisition_v1(_request(tmp_path))

    assert result["status"] == "DOWNLOAD_COMPLETE_PENDING_SELECTION"
    assert result["provider_http_requests_used"] == 210
    assert result["provider_call_units_used"] == 20_406
    assert failures == 1
    assert len(calls) == 209


def test_staged_artifact_tampering_fails_closed_without_redownload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_download, calls = _provider_fixture()
    monkeypatch.setenv("EODHD_API_KEY", "unit-test-secret")
    monkeypatch.setattr(acquisition_module, "_download_raw", fake_download)
    first = run_eodhd_us_equity_universe_acquisition_v1(_request(tmp_path))
    raw_file = next((Path(first["staging_dir"]) / "raw" / "bulk").glob("*.json.gz"))
    raw_file.write_bytes(raw_file.read_bytes() + b"tampered")
    calls.clear()

    second = run_eodhd_us_equity_universe_acquisition_v1(_request(tmp_path))

    assert second["status"] == "STAGING_HASH_MISMATCH"
    assert calls == []
    assert raw_file.read_bytes().endswith(b"tampered")


def test_staging_request_hash_mismatch_returns_fixed_failure_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(tmp_path)
    plan = build_eodhd_us_equity_acquisition_plan_v1(request)
    final = Path(request["output_dir"])
    staging = final.with_name(
        f".EODHD-US-EQUITY-2006-2022-V1-{str(plan['request_sha256'])[:12]}.partial"
    )
    staging.mkdir()
    connection = acquisition_module._open_state(staging / "state.sqlite", "f" * 64)
    connection.close()
    monkeypatch.setenv("EODHD_API_KEY", "unit-test-secret")
    monkeypatch.setattr(
        acquisition_module,
        "_download_raw",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network used")),
    )

    result = run_eodhd_us_equity_universe_acquisition_v1(request)

    assert result["status"] == "STAGING_REQUEST_MISMATCH"
    assert result["provider_call_units_used"] == 0


def test_response_containing_credential_material_is_rejected_before_raw_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "unit-test-secret"

    def leaking_download(url: str, *, timeout_seconds: int, max_response_bytes: int):
        raw = json.dumps([{"leak": secret}]).encode("utf-8")
        return raw, {"http_status": 200, "final_url": url, "response_bytes": len(raw)}

    monkeypatch.setenv("EODHD_API_KEY", secret)
    monkeypatch.setattr(acquisition_module, "_download_raw", leaking_download)

    result = run_eodhd_us_equity_universe_acquisition_v1(_request(tmp_path))

    assert result["status"] == "PROVIDER_RESPONSE_CONTAINED_SECRET"
    assert secret not in repr(result)
    staging = Path(result["staging_dir"])
    assert not list((staging / "raw").rglob("*")) if (staging / "raw").exists() else True
    assert secret.encode("utf-8") not in (staging / "state.sqlite").read_bytes()

    monkeypatch.setattr(
        acquisition_module,
        "_download_raw",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network used")),
    )
    resumed = run_eodhd_us_equity_universe_acquisition_v1(_request(tmp_path))
    assert resumed["status"] == "PROVIDER_RESPONSE_CONTAINED_SECRET"
    assert resumed["provider_http_requests_used"] == 1


def test_worst_case_call_budget_includes_one_retry_for_every_request() -> None:
    assert _worst_case_call_units(identity_count=22_765, month_end_count=204) == 86_336
    assert _worst_case_call_units(identity_count=25_000, month_end_count=204) == 90_806


def test_response_caps_distinguish_large_universe_and_bulk_payloads_from_symbol_history() -> None:
    assert _response_size_cap("ACTIVE_COMMON_STOCKS") == 20_000_000
    assert _response_size_cap("DELISTED_COMMON_STOCKS") == 20_000_000
    assert _response_size_cap("MONTH_END_BULK") == 20_000_000
    assert _response_size_cap("SPY_SESSION_PROXY") == 2_000_000
    assert _response_size_cap("SYMBOL_HISTORY") == 2_000_000
    with pytest.raises(ValueError, match="kind"):
        _response_size_cap("UNKNOWN")


def test_response_metadata_rejects_public_query_drift() -> None:
    endpoint = "https://eodhd.com/api/eod-bulk-last-day/US?date=2022-12-30&fmt=json"
    with pytest.raises(ValueError, match="drift"):
        _validate_response_metadata(
            {
                "http_status": 200,
                "final_url": (
                    "https://eodhd.com/api/eod-bulk-last-day/US"
                    "?api_token=secret&date=2022-11-30&fmt=json"
                ),
            },
            endpoint,
            100,
            1_000,
        )


def test_history_rate_limiter_allows_eight_request_burst_then_caps_start_rate() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def monotonic() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = _RateLimiter(
        rate_per_second=8.0,
        burst=8,
        monotonic=monotonic,
        sleep=sleep,
    )
    for _ in range(8):
        limiter.acquire()
    assert sleeps == []

    limiter.acquire()

    assert sleeps == [pytest.approx(0.125)]


def test_bulk_validator_validates_rows_directly_without_per_row_json_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        acquisition_module,
        "_validate_eod_response",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("per-row roundtrip used")),
    )
    payload = [
        {
            "code": f"S{index:04d}",
            "exchange_short_name": "NASDAQ",
            **_eod_row("2022-12-30", 20.0 + index / 10_000.0),
        }
        for index in range(1_000)
    ]
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    normalized, count = _validate_bulk_response(raw, expected_date="2022-12-30")

    assert count == 1_000
    assert normalized[0]["code"] == "S0000"
    assert normalized[-1]["code"] == "S0999"


def test_bulk_validator_ignores_ohlc_defects_only_on_excluded_exchanges() -> None:
    invalid_ohlc = {
        "date": "2022-12-30",
        "open": 0.0,
        "high": 0.0,
        "low": 0.0,
        "close": 0.0,
        "adjusted_close": 0.0,
        "volume": 0,
    }
    payload = [
        {"code": "OTC1", "exchange_short_name": "PINK", **invalid_ohlc},
        {"code": "AAA", "exchange_short_name": "NASDAQ", **_eod_row("2022-12-30")},
    ]

    normalized, count = _validate_bulk_response(
        json.dumps(payload).encode("utf-8"),
        expected_date="2022-12-30",
    )

    assert count == 2
    assert [row["code"] for row in normalized] == ["AAA"]

    payload[0]["exchange_short_name"] = "NASDAQ"
    with pytest.raises(ValueError, match="positive"):
        _validate_bulk_response(
            json.dumps(payload).encode("utf-8"),
            expected_date="2022-12-30",
        )


def test_symbol_histories_use_bounded_parallelism(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    maximum_active = 0
    lock = threading.Lock()
    seen: list[int] = []

    def observed_obtain(**kwargs):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
            seen.append(kwargs["ordinal"])
        return b"[]"

    monkeypatch.setattr(acquisition_module, "_obtain_response", observed_obtain)
    identities = [
        {
            "code": f"S{index:02d}",
            "symbol": f"S{index:02d}.US",
            "exchange_mic": "XNAS",
        }
        for index in range(16)
    ]
    staging = tmp_path / "stage"
    staging.mkdir()
    connection = acquisition_module._open_state(staging / "state.sqlite", "a" * 64)
    connection.close()

    result = _download_symbol_histories(
        identities=identities,
        ordinal_start=208,
        staging=staging,
        api_key="secret",
    )

    assert maximum_active == 8
    assert sorted(seen) == list(range(208, 224))
    assert result == {"requested": 16, "resolved": 16, "unresolved": 0}


def test_symbol_history_failures_are_accounted_and_bounded_instead_of_aborting_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def observed_obtain(**kwargs):
        if kwargs["ordinal"] == 208:
            raise acquisition_module._AcquisitionFailure("PROVIDER_RESPONSE_INVALID")
        return b"[]"

    monkeypatch.setattr(acquisition_module, "_obtain_response", observed_obtain)
    identities = [
        {
            "code": f"S{index:03d}",
            "symbol": f"S{index:03d}.US",
            "exchange_mic": "XNAS",
        }
        for index in range(100)
    ]
    staging = tmp_path / "stage"
    staging.mkdir()
    connection = acquisition_module._open_state(staging / "state.sqlite", "a" * 64)
    connection.close()

    result = _download_symbol_histories(
        identities=identities,
        ordinal_start=208,
        staging=staging,
        api_key="secret",
    )

    assert result == {"requested": 100, "resolved": 99, "unresolved": 1}


def test_resume_does_not_retry_a_terminal_permanent_symbol_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "stage"
    staging.mkdir()
    connection = acquisition_module._open_state(staging / "state.sqlite", "a" * 64)
    endpoint = acquisition_module._endpoint_identity(
        "/api/eod/AAA.US",
        {"fmt": "json", "from": "2006-01-01", "period": "d", "to": "2022-12-31"},
    )
    connection.execute(
        """
        INSERT INTO requests(endpoint_identity, ordinal, kind, call_units, attempts, status, subject)
        VALUES(?, 208, 'SYMBOL_HISTORY', 1, 1, 'FAILED_PROVIDER_RESPONSE_INVALID', 'I1')
        """,
        (endpoint,),
    )
    connection.commit()
    monkeypatch.setattr(
        acquisition_module,
        "_download_raw",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network used")),
    )

    with pytest.raises(acquisition_module._AcquisitionFailure) as error:
        acquisition_module._obtain_response(
            connection=connection,
            staging=staging,
            api_key="secret",
            ordinal=208,
            kind="SYMBOL_HISTORY",
            endpoint_identity=endpoint,
            call_units=1,
            relative_raw_path="raw/a.json.gz",
            max_response_bytes=2_000_000,
            validator=lambda raw: ([], 0),
            subject="I1",
        )

    assert error.value.status == "PROVIDER_RESPONSE_INVALID"
    assert connection.execute(
        "SELECT attempts FROM requests WHERE endpoint_identity=?", (endpoint,)
    ).fetchone()[0] == 1
    connection.close()


def test_fatal_symbol_failure_cancels_queued_history_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    lock = threading.Lock()

    def observed_obtain(**kwargs):
        nonlocal calls
        with lock:
            calls += 1
        if kwargs["ordinal"] == 208:
            raise acquisition_module._AcquisitionFailure("PROVIDER_RESPONSE_CONTAINED_SECRET")
        time.sleep(0.05)
        return b"[]"

    monkeypatch.setattr(acquisition_module, "_obtain_response", observed_obtain)
    identities = [
        {
            "code": f"S{index:03d}",
            "symbol": f"S{index:03d}.US",
            "exchange_mic": "XNAS",
        }
        for index in range(100)
    ]
    staging = tmp_path / "stage"
    staging.mkdir()
    connection = acquisition_module._open_state(staging / "state.sqlite", "a" * 64)
    connection.close()

    with pytest.raises(acquisition_module._AcquisitionFailure) as error:
        _download_symbol_histories(
            identities=identities,
            ordinal_start=208,
            staging=staging,
            api_key="secret",
        )

    assert error.value.status == "PROVIDER_RESPONSE_CONTAINED_SECRET"
    assert calls <= 16
