from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from research_lab.research.minervini_immutable_pilot_artifacts_v1 import (
    MinerviniPilotArtifactWriterV1,
    replay_minervini_pilot_artifacts_v1,
)


ENDPOINT = (
    "https://eodhd.com/api/exchange-symbol-list/US"
    "?type=common_stock&fmt=json"
)


def _write_one(writer: MinerviniPilotArtifactWriterV1):
    raw = b'[{"Code":"AAPL","Type":"Common Stock"}]'
    record = writer.write_response(
        ordinal=1,
        artifact_name="active-common-stocks.json",
        endpoint_identity=ENDPOINT,
        http_status=200,
        raw_bytes=raw,
        retrieved_at_utc="2026-07-25T20:00:00Z",
        parsed_row_count=1,
        schema_status="VALID",
    )
    return raw, record


def test_writer_persists_exact_bytes_hashes_and_append_only_journal(tmp_path):
    writer = MinerviniPilotArtifactWriterV1.create(tmp_path / "run")

    raw, record = _write_one(writer)

    assert (tmp_path / "run" / "active-common-stocks.json").read_bytes() == raw
    assert record["response_sha256"] == hashlib.sha256(raw).hexdigest()
    journal = (tmp_path / "run" / "request-journal.jsonl").read_text(
        encoding="utf-8"
    )
    assert json.loads(journal)["ordinal"] == 1
    assert "api_token" not in journal


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("ordinal", 1, "ordinal"),
        ("artifact_name", "active-common-stocks.json", "artifact"),
        ("artifact_name", "../escape.json", "artifact"),
        (
            "endpoint_identity",
            ENDPOINT + "&api_token=secret-value",
            "secret",
        ),
    ],
)
def test_writer_rejects_duplicate_or_unsafe_evidence(tmp_path, field, value, match):
    writer = MinerviniPilotArtifactWriterV1.create(tmp_path / "run")
    _write_one(writer)
    request = {
        "ordinal": 2,
        "artifact_name": "second.json",
        "endpoint_identity": ENDPOINT,
        "http_status": 200,
        "raw_bytes": b"[]",
        "retrieved_at_utc": "2026-07-25T20:00:01Z",
        "parsed_row_count": 0,
        "schema_status": "VALID",
    }
    request[field] = value

    with pytest.raises(ValueError, match=match):
        writer.write_response(**request)


def test_writer_requires_absolute_empty_local_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="absolute"):
        MinerviniPilotArtifactWriterV1.create(Path("relative"))
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "existing.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        MinerviniPilotArtifactWriterV1.create(occupied)


def test_finalize_and_replay_verify_manifest_and_raw_hash(tmp_path):
    root = tmp_path / "run"
    writer = MinerviniPilotArtifactWriterV1.create(root)
    _write_one(writer)
    manifest_path = writer.finalize(
        {
            "status": "READY_FOR_WIDE_ACQUISITION_APPROVAL",
            "provider_requests_used": 1,
        }
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    replay = replay_minervini_pilot_artifacts_v1(root)

    assert replay["status"] == "VERIFIED"
    assert (
        replay["result_manifest_sha256"]
        == manifest["result_manifest_sha256"]
    )

    (root / "active-common-stocks.json").write_bytes(b"tampered")
    failed = replay_minervini_pilot_artifacts_v1(root)
    assert failed["status"] == "FAILED_RAW_HASH_MISMATCH"


def test_replay_detects_journal_mutation(tmp_path):
    root = tmp_path / "run"
    writer = MinerviniPilotArtifactWriterV1.create(root)
    _write_one(writer)
    writer.finalize({"status": "READY", "provider_requests_used": 1})
    journal = root / "request-journal.jsonl"
    journal.write_text(
        journal.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    replay = replay_minervini_pilot_artifacts_v1(root)

    assert replay["status"] == "FAILED_JOURNAL_HASH_MISMATCH"
