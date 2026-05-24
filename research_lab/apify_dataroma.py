from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from research_lab.registry import append_jsonl


DEFAULT_SUPERINVESTORS = ["BRK", "HC", "BAUPOST", "PI", "AM"]


def run_dataroma_actor(
    root: Path,
    superinvestors: list[str] | None = None,
    max_results: int = 200,
    actor_id: str | None = None,
) -> list[dict]:
    token = os.getenv("APIFY_TOKEN", "").strip()
    if not token:
        raise RuntimeError("APIFY_TOKEN is required.")
    actor = actor_id or os.getenv("APIFY_DATAROMA_ACTOR", "parsebird/dataroma-superinvestor-scraper")
    run_input = {
        "superinvestors": superinvestors or DEFAULT_SUPERINVESTORS,
        "maxResults": max_results,
    }
    items = _run_actor_sync(actor, token, run_input)
    _persist_items(root, items, run_input)
    hypotheses = _items_to_hypotheses(root, items)
    _write_report(root, items, hypotheses, run_input)
    return items


def _run_actor_sync(actor_id: str, token: str, run_input: dict) -> list[dict]:
    encoded_actor = actor_id.replace("/", "~")
    query = urllib.parse.urlencode({"token": token, "timeout": "180", "memory": "512"})
    url = f"https://api.apify.com/v2/acts/{encoded_actor}/run-sync-get-dataset-items?{query}"
    request = urllib.request.Request(
        url,
        data=json.dumps(run_input).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "research-lab/0.1"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        return json.loads(response.read().decode("utf-8"))


def _persist_items(root: Path, items: list[dict], run_input: dict) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = root / "data" / "processed" / "apify_dataroma"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_input": run_input,
        "item_count": len(items),
        "items": items,
    }
    (out_dir / f"holdings_{stamp}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for item in items:
        append_jsonl(root / "registry" / "apify_dataroma_holdings.jsonl", item)


def _items_to_hypotheses(root: Path, items: list[dict]) -> list[dict]:
    existing = _existing_source_keys(root / "registry" / "hypothesis_queue.jsonl")
    hypotheses = []
    for item in items:
        ticker = str(item.get("symbol") or item.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        investor = str(item.get("superinvestorName") or item.get("superinvestorId") or "").strip()
        activity = str(item.get("recentActivity") or "").strip()
        weight = item.get("percentOfPortfolio")
        source_key = f"apify-dataroma:{investor}:{ticker}:{activity}:{weight}"
        if source_key in existing:
            continue
        payload = {
            "hypothesis_id": f"HYP_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_APIFY_{len(hypotheses) + 1:03d}",
            "title": "Dataroma full-holdings smart-money pullback",
            "family": "SWING",
            "ticker": ticker,
            "rationale": (
                f"{investor} holds {ticker} at {weight}% of portfolio with recentActivity={activity}. "
                "Use this only as a universe/conviction filter; test price-based pullback entries."
            ),
            "source_title": f"apify_dataroma:{investor}",
            "source_url": str(item.get("superinvestorUrl") or ""),
            "source_key": source_key,
            "tags": ["dataroma", "apify", "13f", "smart_money", "swing"],
            "status": "queued",
            "research_only": True,
            "apify_dataroma": {
                "superinvestor_name": investor,
                "superinvestor_id": item.get("superinvestorId"),
                "percent_of_portfolio": weight,
                "recent_activity": activity,
                "reported_price": item.get("reportedPrice"),
                "current_price": item.get("currentPrice"),
                "change_from_reported_price": item.get("changeFromReportedPrice"),
                "portfolio_date": item.get("portfolioDate"),
                "period": item.get("period"),
            },
        }
        append_jsonl(root / "registry" / "hypothesis_queue.jsonl", payload)
        hypotheses.append(payload)
        existing.add(source_key)
    return hypotheses


def _write_report(root: Path, items: list[dict], hypotheses: list[dict], run_input: dict) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    path = root / "reports" / "source_scans" / f"{stamp}-apify-dataroma.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Apify Dataroma Import - {stamp} UTC",
        "",
        f"- superinvestors: {', '.join(run_input.get('superinvestors', []))}",
        f"- maxResults: {run_input.get('maxResults')}",
        f"- holdings imported: {len(items)}",
        f"- hypotheses queued: {len(hypotheses)}",
        "",
    ]
    for item in items[:25]:
        lines.append(
            "- {investor}: {symbol} weight={weight} activity={activity}".format(
                investor=item.get("superinvestorName") or item.get("superinvestorId"),
                symbol=item.get("symbol") or item.get("ticker"),
                weight=item.get("percentOfPortfolio"),
                activity=item.get("recentActivity"),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _existing_source_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = item.get("source_key")
        if key:
            keys.add(str(key))
    return keys

