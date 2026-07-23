from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


EDGE_COLUMNS = [
    "item_id",
    "source",
    "family",
    "title",
    "edge_bucket",
    "edge_strength",
    "failure_mode",
    "validation_requirement",
]
MAX_EDGE_AUDIT_INPUT_ROWS = 1000
MAX_EDGE_AUDIT_INPUT_LINES = 2000
MAX_EDGE_AUDIT_LINE_BYTES = 8192


EDGE_RULES = [
    {
        "bucket": "smart_money_flow",
        "keywords": ["13f", "dataroma", "apify", "whale", "insider", "congress", "holder", "holding", "smart_money"],
        "strength": "plausible_filter",
        "failure": "Filings are delayed or stale; holdings may not represent current intent.",
        "validation": "Use as universe filter only, then require price/volatility entry rules and out-of-sample tests.",
    },
    {
        "bucket": "behavioral_momentum",
        "keywords": ["momentum", "relative strength", "trend", "rotation", "rank"],
        "strength": "plausible",
        "failure": "Momentum crashes and crowded lookbacks can reverse abruptly.",
        "validation": "Require positive validation/unseen results, nearby lookback stability, and drawdown controls.",
    },
    {
        "bucket": "behavioral_mean_reversion",
        "keywords": ["mean_reversion", "mean reversion", "pullback", "oversold", "rsi", "reversal"],
        "strength": "plausible",
        "failure": "The pullback may be the start of a new downtrend.",
        "validation": "Require trend filter, stop/time exit, enough trades, and cost stress.",
    },
    {
        "bucket": "volatility_risk_control",
        "keywords": ["volatility", "vol target", "vol-target", "risk", "drawdown", "defensive", "skew"],
        "strength": "risk_control",
        "failure": "Risk control can reduce return without creating alpha.",
        "validation": "Measure drawdown reduction versus return drag; do not treat as standalone alpha.",
    },
    {
        "bucket": "event_sentiment",
        "keywords": ["earnings", "news", "sentiment", "disclosure", "policy", "filing", "event"],
        "strength": "weak_until_tested",
        "failure": "Narratives are noisy and can be priced before the lab sees them.",
        "validation": "Use timestamped event studies and strict no-lookahead windows.",
    },
    {
        "bucket": "execution_microstructure",
        "keywords": ["intraday", "vwap", "microstructure", "spread", "liquidity", "tick"],
        "strength": "data_limited",
        "failure": "Costs, latency, and slippage can dominate gross edge.",
        "validation": "Do not promote without real intraday data and conservative fill assumptions.",
    },
]


def classify_edge(item: dict[str, Any]) -> dict[str, str]:
    text = _item_text(item)
    matches = []
    for rule in EDGE_RULES:
        score = sum(1 for keyword in rule["keywords"] if keyword in text)
        if score:
            matches.append((score, rule))
    if not matches:
        return {
            "edge_bucket": "unclear",
            "edge_strength": "missing",
            "failure_mode": "The hypothesis does not name a repeatable market inefficiency or compensated risk.",
            "validation_requirement": "Clarify edge before spending more data or compute on this idea.",
        }
    matches.sort(key=lambda item: item[0], reverse=True)
    rule = matches[0][1]
    return {
        "edge_bucket": rule["bucket"],
        "edge_strength": rule["strength"],
        "failure_mode": rule["failure"],
        "validation_requirement": rule["validation"],
    }


def run_edge_audit(root: Path) -> dict[str, Any]:
    rows = []
    for source, path in (
        ("hypothesis", root / "registry" / "hypothesis_queue.jsonl"),
        ("creative", root / "registry" / "creative_ideas.jsonl"),
    ):
        for item in _read_jsonl(path):
            edge = classify_edge(item)
            rows.append(
                {
                    "item_id": item.get("hypothesis_id") or item.get("idea_id") or item.get("source_key") or "",
                    "source": source,
                    "family": item.get("family", ""),
                    "title": item.get("title", ""),
                    **edge,
                }
            )

    registry_path = root / "registry" / "edge_audit.csv"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(registry_path, rows)
    report_path = _write_report(root, rows)
    return {"rows": rows, "csv_path": registry_path, "report_path": report_path}


def summarize_edge_audit(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["- edge audit: no hypotheses or creative ideas found"]
    buckets = Counter(row["edge_bucket"] for row in rows)
    weak = [row for row in rows if row["edge_bucket"] == "unclear" or row["edge_strength"] in {"missing", "weak_until_tested", "data_limited"}]
    lines = [
        f"- edge-audited ideas: {len(rows)}",
        f"- unclear or weak/data-limited ideas: {len(weak)}",
        "- edge buckets: " + ", ".join(f"{bucket}={count}" for bucket, count in buckets.most_common()),
    ]
    return lines


def _item_text(item: dict[str, Any]) -> str:
    parts = [
        str(item.get("title", "")),
        str(item.get("rationale", "")),
        str(item.get("hypothesis", "")),
        str(item.get("rules", "")),
        str(item.get("short_name", "")),
        str(item.get("family", "")),
        str(item.get("source_title", "")),
        " ".join(str(tag) for tag in item.get("tags", [])),
    ]
    return " ".join(parts).lower()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items = []
    with path.open("rb") as handle:
        for _ in range(MAX_EDGE_AUDIT_INPUT_LINES):
            line = handle.readline(MAX_EDGE_AUDIT_LINE_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_EDGE_AUDIT_LINE_BYTES:
                raise ValueError("edge-audit JSONL line exceeds bounded input size")
            line = line.decode("utf-8")
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                items.append(item)
            if len(items) >= MAX_EDGE_AUDIT_INPUT_ROWS:
                break
    return items


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=EDGE_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in EDGE_COLUMNS})


def _write_report(root: Path, rows: list[dict[str, Any]]) -> Path:
    report = root / "reports" / "self_improvement" / f"{date.today().isoformat()}-edge-audit.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Edge Audit - {date.today().isoformat()}",
        "",
        *summarize_edge_audit(rows),
        "",
        "## Weak Or Unclear Ideas",
        "",
    ]
    weak = [row for row in rows if row["edge_bucket"] == "unclear" or row["edge_strength"] in {"missing", "weak_until_tested", "data_limited"}]
    for row in weak[:25]:
        lines.extend(
            [
                f"### {row['item_id']} - {row['title']}",
                "",
                f"- bucket: {row['edge_bucket']}",
                f"- strength: {row['edge_strength']}",
                f"- failure mode: {row['failure_mode']}",
                f"- validation: {row['validation_requirement']}",
                "",
            ]
        )
    report.write_text("\n".join(lines), encoding="utf-8")
    return report
