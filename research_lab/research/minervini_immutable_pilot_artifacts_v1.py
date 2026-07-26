from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping


MANIFEST_VERSION = "minervini_immutable_pilot_manifest_v1"
_ARTIFACT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass
class MinerviniPilotArtifactWriterV1:
    root: Path
    journal_path: Path
    used_ordinals: set[int] = field(default_factory=set)
    used_names: set[str] = field(default_factory=set)
    records: list[dict[str, object]] = field(default_factory=list)
    finalized: bool = False

    @classmethod
    def create(cls, output_dir: Path) -> "MinerviniPilotArtifactWriterV1":
        raw = Path(output_dir)
        if not raw.is_absolute():
            raise ValueError("output directory must be absolute.")
        if raw.exists() and raw.is_symlink():
            raise ValueError("output directory must not be a symlink.")
        root = raw.resolve()
        if root.exists():
            if not root.is_dir():
                raise ValueError("output directory must be a directory.")
            if any(root.iterdir()):
                raise ValueError("output directory must be empty.")
        else:
            root.mkdir(parents=True, exist_ok=False)
        journal_path = root / "request-journal.jsonl"
        journal_path.touch(exist_ok=False)
        return cls(root=root, journal_path=journal_path)

    def write_response(
        self,
        *,
        ordinal: int,
        artifact_name: str,
        endpoint_identity: str,
        http_status: int,
        raw_bytes: bytes,
        retrieved_at_utc: str,
        parsed_row_count: int,
        schema_status: str,
    ) -> dict[str, object]:
        if self.finalized:
            raise ValueError("writer is already finalized.")
        if (
            isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or not 1 <= ordinal <= 24
            or ordinal in self.used_ordinals
        ):
            raise ValueError("ordinal must be unique and between 1 and 24.")
        if (
            not isinstance(artifact_name, str)
            or not _ARTIFACT_RE.fullmatch(artifact_name)
            or artifact_name in self.used_names
        ):
            raise ValueError("artifact name must be a unique direct child.")
        _validate_endpoint(endpoint_identity)
        if isinstance(http_status, bool) or not isinstance(http_status, int):
            raise ValueError("http_status must be an integer.")
        if not isinstance(raw_bytes, bytes):
            raise ValueError("raw_bytes must be bytes.")
        if (
            not isinstance(parsed_row_count, int)
            or isinstance(parsed_row_count, bool)
            or parsed_row_count < 0
        ):
            raise ValueError("parsed_row_count must be non-negative.")
        for value, name in (
            (retrieved_at_utc, "retrieved_at_utc"),
            (schema_status, "schema_status"),
        ):
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name} must be normalized text.")
        target = self.root / artifact_name
        with target.open("xb") as handle:
            handle.write(raw_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        record: dict[str, object] = {
            "ordinal": ordinal,
            "artifact_name": artifact_name,
            "endpoint_identity": endpoint_identity,
            "http_status": http_status,
            "retrieved_at_utc": retrieved_at_utc,
            "response_bytes": len(raw_bytes),
            "response_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "parsed_row_count": parsed_row_count,
            "schema_status": schema_status,
        }
        with self.journal_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical_json(record))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.used_ordinals.add(ordinal)
        self.used_names.add(artifact_name)
        self.records.append(record)
        return dict(record)

    def finalize(self, result: Mapping[str, object]) -> Path:
        if self.finalized:
            raise ValueError("writer is already finalized.")
        if not isinstance(result, Mapping):
            raise ValueError("result must be a mapping.")
        journal_bytes = self.journal_path.read_bytes()
        manifest: dict[str, object] = {
            "version": MANIFEST_VERSION,
            "result": dict(result),
            "artifacts": list(self.records),
            "journal_bytes": len(journal_bytes),
            "journal_sha256": hashlib.sha256(journal_bytes).hexdigest(),
        }
        manifest["result_manifest_sha256"] = _hash(manifest)
        target = self.root / "pilot-result-manifest.json"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.root,
                prefix=".pilot-result-manifest.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(_canonical_json(manifest))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary_path = Path(handle.name)
            os.replace(temporary_path, target)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        self.finalized = True
        return target


def replay_minervini_pilot_artifacts_v1(
    output_dir: Path,
) -> dict[str, object]:
    root = Path(output_dir)
    if not root.is_absolute() or not root.exists() or not root.is_dir():
        raise ValueError("output directory must be an absolute directory.")
    manifest_path = root / "pilot-result-manifest.json"
    if not manifest_path.is_file():
        return {"status": "FAILED_MANIFEST_UNAVAILABLE"}
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("version") != MANIFEST_VERSION
        or not isinstance(payload.get("artifacts"), list)
    ):
        return {"status": "FAILED_MANIFEST_SCHEMA"}
    expected_manifest_hash = payload.get("result_manifest_sha256")
    without_hash = dict(payload)
    without_hash.pop("result_manifest_sha256", None)
    if expected_manifest_hash != _hash(without_hash):
        return {"status": "FAILED_MANIFEST_HASH_MISMATCH"}
    journal_path = root / "request-journal.jsonl"
    if not journal_path.is_file():
        return {"status": "FAILED_JOURNAL_UNAVAILABLE"}
    journal_bytes = journal_path.read_bytes()
    if (
        len(journal_bytes) != payload.get("journal_bytes")
        or hashlib.sha256(journal_bytes).hexdigest()
        != payload.get("journal_sha256")
    ):
        return {"status": "FAILED_JOURNAL_HASH_MISMATCH"}
    for record in payload["artifacts"]:
        if not isinstance(record, dict):
            return {"status": "FAILED_MANIFEST_SCHEMA"}
        name = record.get("artifact_name")
        if not isinstance(name, str) or not _ARTIFACT_RE.fullmatch(name):
            return {"status": "FAILED_MANIFEST_SCHEMA"}
        target = root / name
        if not target.is_file():
            return {"status": "FAILED_RAW_ARTIFACT_UNAVAILABLE"}
        if hashlib.sha256(target.read_bytes()).hexdigest() != record.get(
            "response_sha256"
        ):
            return {"status": "FAILED_RAW_HASH_MISMATCH"}
    return {
        "status": "VERIFIED",
        "result_manifest_sha256": expected_manifest_hash,
        "artifact_count": len(payload["artifacts"]),
    }


def _validate_endpoint(value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("endpoint identity must be normalized text.")
    parsed = urllib.parse.urlparse(value)
    query_names = {
        name.casefold()
        for name, _ in urllib.parse.parse_qsl(
            parsed.query, keep_blank_values=True
        )
    }
    if (
        parsed.scheme != "https"
        or parsed.netloc.casefold() != "eodhd.com"
        or "api_token" in query_names
    ):
        raise ValueError("endpoint identity is unsafe or contains a secret.")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
