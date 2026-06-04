from __future__ import annotations

from collections import Counter
from typing import Any

from research_lab.queue_dedupe import candidate_fingerprint


def summarize_hypothesis_diagnostics(hypotheses: list[dict[str, Any]], top_n: int = 5) -> dict[str, Any]:
    fingerprints = []
    family_counts: Counter[str] = Counter()
    asset_counts: Counter[str] = Counter()
    timeframe_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()

    for item in hypotheses:
        _count_if_present(family_counts, item.get("family"))
        _count_if_present(asset_counts, _asset_value(item))
        _count_if_present(timeframe_counts, item.get("timeframe"))
        _count_if_present(source_counts, _source_value(item))
        _count_if_present(reason_counts, _reason_value(item))
        try:
            fingerprints.append(candidate_fingerprint(item))
        except ValueError:
            continue

    fingerprint_counts = Counter(fingerprints)
    duplicate_groups = [(fingerprint, count) for fingerprint, count in fingerprint_counts.items() if count > 1]
    duplicate_groups.sort(key=lambda item: (-item[1], item[0]))
    unique_fingerprints = len(fingerprint_counts)
    duplicate_fingerprints = len(duplicate_groups)

    return {
        "total_hypotheses_seen": len(hypotheses),
        "unique_fingerprints": unique_fingerprints,
        "duplicate_fingerprints": duplicate_fingerprints,
        "duplicate_rate": round(duplicate_fingerprints / unique_fingerprints, 6) if unique_fingerprints else 0.0,
        "family_counts": dict(family_counts),
        "asset_counts": dict(asset_counts),
        "timeframe_counts": dict(timeframe_counts),
        "source_counts": dict(source_counts),
        "skipped_or_deduped_reason_counts": dict(reason_counts),
        "top_duplicate_fingerprints": [
            {"fingerprint": fingerprint, "count": count, "duplicate_count": count - 1}
            for fingerprint, count in duplicate_groups[: max(top_n, 0)]
        ],
    }


def _count_if_present(counter: Counter[str], value: Any) -> None:
    text = str(value or "").strip()
    if text:
        counter[text] += 1


def _asset_value(item: dict[str, Any]) -> Any:
    for key in ("asset_class", "asset", "asset_type", "ticker", "symbol"):
        if item.get(key):
            return item[key]
    parameters = item.get("parameters")
    if isinstance(parameters, dict):
        for key in ("asset_class", "asset", "ticker", "symbol"):
            if parameters.get(key):
                return parameters[key]
    return None


def _source_value(item: dict[str, Any]) -> Any:
    source_key = str(item.get("source_key") or "").strip()
    if source_key:
        return source_key.split(":", 1)[0]
    return item.get("source_title") or item.get("source") or item.get("source_url")


def _reason_value(item: dict[str, Any]) -> Any:
    for key in ("skip_reason", "dedupe_reason", "skipped_reason", "deduped_reason", "reason"):
        if item.get(key):
            return item[key]
    status = str(item.get("status") or "").strip().lower()
    if status in {"skipped", "deduped"}:
        return status
    return None
