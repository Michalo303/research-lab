from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from research_lab.registry import append_jsonl
from research_lab.risk_management import apply_risk_guidance


def import_smartmoney_candidates(
    root: Path,
    smartmoney_path: Path,
    limit: int = 10,
    min_final_score: float = 70.0,
) -> list[dict]:
    candidates_path = smartmoney_path / "reports" / "all_candidates.csv"
    if not candidates_path.exists():
        raise FileNotFoundError(f"Smartmoney candidates not found: {candidates_path}")
    rows = _read_candidates(candidates_path)
    selected = [
        row
        for row in rows
        if _as_bool(row.get("eligible_for_top10"))
        or float(row.get("final_score") or 0) >= min_final_score
    ]
    selected = sorted(selected, key=lambda row: float(row.get("final_score") or 0), reverse=True)[:limit]
    imported = []
    existing_keys = _existing_source_keys(root / "registry" / "hypothesis_queue.jsonl")
    for row in selected:
        ticker = row["ticker"].strip().upper()
        source_key = f"smartmoney:{ticker}:{row.get('final_score', '')}:{row.get('smart_money_score', '')}"
        if source_key in existing_keys:
            continue
        payload = apply_risk_guidance(
            {
                "hypothesis_id": f"HYP_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_SMARTMONEY_{len(imported) + 1:03d}",
                "title": "Smart-money accumulation pullback",
                "family": "SWING",
                "ticker": ticker,
                "rationale": (
                    f"{ticker} passed the smartmoney screen with final_score={row.get('final_score')} "
                    f"and smart_money_score={row.get('smart_money_score')}. Test only price-based swing entries; "
                    "13F activity is a universe filter, not an entry signal."
                ),
                "source_title": f"smartmoney:{ticker}",
                "source_url": str(candidates_path),
                "source_key": source_key,
                "tags": ["smart_money", "13f", "swing", "pullback"],
                "status": "queued",
                "research_only": True,
                "smartmoney": {
                    "company_name": row.get("company_name", ""),
                    "final_score": row.get("final_score", ""),
                    "smart_money_score": row.get("smart_money_score", ""),
                    "number_of_buyers": row.get("number_of_buyers", ""),
                    "strict_quality_buyer_count": row.get("strict_quality_buyer_count", ""),
                    "too_late_assessment": row.get("too_late_assessment", ""),
                    "analyst_category": row.get("analyst_category", ""),
                    "sector": row.get("sector", ""),
                },
            }
        )
        append_jsonl(root / "registry" / "hypothesis_queue.jsonl", payload)
        imported.append(payload)
        existing_keys.add(source_key)
    _write_report(root, smartmoney_path, imported)
    return imported


def _read_candidates(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _as_bool(value: str | None) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _existing_source_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if '"source_key"' not in line:
            continue
        try:
            import json

            item = json.loads(line)
        except Exception:
            continue
        if item.get("source_key"):
            keys.add(str(item["source_key"]))
    return keys


def _write_report(root: Path, smartmoney_path: Path, imported: list[dict]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    path = root / "reports" / "source_scans" / f"{stamp}-smartmoney-import.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Smartmoney Import - {stamp} UTC",
        "",
        f"- source repo: {smartmoney_path}",
        f"- hypotheses imported: {len(imported)}",
        "",
    ]
    for item in imported:
        meta = item["smartmoney"]
        lines.extend(
            [
                f"## {item['ticker']}",
                "",
                f"- company: {meta['company_name']}",
                f"- final_score: {meta['final_score']}",
                f"- smart_money_score: {meta['smart_money_score']}",
                f"- buyers: {meta['number_of_buyers']}",
                f"- category: {meta['analyst_category']}",
                "",
                item["rationale"],
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
