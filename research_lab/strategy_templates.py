from __future__ import annotations

import copy
import hashlib
import itertools
import json
import re
from typing import Any

from research_lab.queue_dedupe import candidate_fingerprint
from research_lab.risk_management import apply_risk_guidance


REQUIRED_TEMPLATE_FIELDS = ("template_id", "family", "asset_class", "builder", "timeframe", "title")


def generate_strategy_candidates(
    templates: list[dict[str, Any]],
    limit: int | None = None,
    return_diagnostics: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostics = {
        "template_count": len(templates),
        "malformed_template_count": 0,
        "skipped_template_ids": [],
        "warnings": [],
        "generated_count": 0,
        "retained_count": 0,
        "duplicate_count": 0,
    }
    generated: list[dict[str, Any]] = []

    for template in templates:
        clean_template = copy.deepcopy(template)
        missing = [field for field in REQUIRED_TEMPLATE_FIELDS if not clean_template.get(field)]
        if missing:
            diagnostics["malformed_template_count"] += 1
            template_id = str(clean_template.get("template_id") or "unknown")
            diagnostics["skipped_template_ids"].append(template_id)
            diagnostics["warnings"].append(f"template {template_id} missing required fields: {','.join(missing)}")
            continue
        for index, parameters in enumerate(_parameter_variants(clean_template.get("parameter_grid") or clean_template.get("parameters") or {}), start=1):
            candidate = _candidate_from_template(clean_template, parameters, index)
            generated.append(candidate)

    diagnostics["generated_count"] = len(generated)
    retained = _dedupe_candidates(generated)
    if limit is not None:
        retained = retained[: max(int(limit), 0)]
    diagnostics["retained_count"] = len(retained)
    diagnostics["duplicate_count"] = diagnostics["generated_count"] - len(_dedupe_candidates(generated))

    if return_diagnostics:
        return retained, diagnostics
    return retained


def _parameter_variants(grid: dict[str, Any]) -> list[dict[str, Any]]:
    if not grid:
        return [{}]
    keys = [str(key) for key in grid]
    values = [_variant_values(grid[key]) for key in keys]
    variants = []
    for combination in itertools.product(*values):
        variants.append({key: copy.deepcopy(value) for key, value in zip(keys, combination)})
    return variants


def _variant_values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value or [None]
    return [value]


def _candidate_from_template(template: dict[str, Any], parameters: dict[str, Any], sequence: int) -> dict[str, Any]:
    template_id = str(template["template_id"])
    candidate = {
        "hypothesis_id": f"TPL_{_slug(template_id).upper()}_{sequence:03d}_{_stable_suffix(template, parameters)}",
        "title": str(template["title"]),
        "family": str(template["family"]),
        "asset_class": str(template["asset_class"]),
        "timeframe": str(template["timeframe"]),
        "template": template_id,
        "hypothesis": str(template.get("hypothesis") or template.get("rationale") or template["title"]),
        "parameters": copy.deepcopy(parameters),
        "filters": copy.deepcopy(template.get("filters", {})),
        "risk_controls": copy.deepcopy(template.get("risk_controls", {})),
        "rules": str(template.get("rules", "")),
        "builder": str(template["builder"]),
        "source_key": f"template:{template_id}:{_stable_suffix(template, parameters)}",
        "source_title": str(template.get("source") or template_id),
        "status": "queued",
        "research_only": True,
    }
    return apply_risk_guidance(candidate)


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    retained = []
    for candidate in candidates:
        fingerprint = candidate_fingerprint(candidate)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        retained.append(candidate)
    return retained


def _stable_suffix(template: dict[str, Any], parameters: dict[str, Any]) -> str:
    payload = {
        "template_id": template.get("template_id"),
        "family": template.get("family"),
        "asset_class": template.get("asset_class"),
        "timeframe": template.get("timeframe"),
        "builder": template.get("builder"),
        "parameters": parameters,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10].upper()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_") or "template"
