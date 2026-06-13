"""Rank private-library metadata for a bounded, curated hypothesis seed set."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import re
from typing import Iterable, Mapping


@dataclass(frozen=True)
class BookRecord:
    book_id: str
    title: str
    source_path: str
    source_sha256: str
    size_bytes: int
    relevance_score: float = 0.0
    relevance_reasons: tuple[str, ...] = ()


CATEGORY_PHRASES: dict[str, dict[str, float]] = {
    "etf_1d": {
        "advanced technical analysis of etfs": 24,
        "trading with intermarket analysis": 14,
        "exchange traded funds": 18,
        "etf": 16,
        "market timing": 7,
        "technical analysis": 5,
        "intermarket": 5,
    },
    "trend_following": {
        "trend following": 24,
        "identifying market trends": 18,
        "all about market timing": 10,
        "market trends": 12,
        "trend": 10,
        "moving averages": 9,
        "technical trading": 5,
        "technical analysis": 4,
    },
    "volatility_targeting": {
        "volatility trading": 22,
        "stock market volatility": 12,
        "volatility": 14,
        "volatile markets": 10,
        "risk management": 5,
        "risk control": 5,
    },
    "drawdown_control": {
        "buy and hedge": 16,
        "risk analysis techniques for traders": 14,
        "money management": 15,
        "risk control": 15,
        "managing risk": 14,
        "risk management": 12,
        "protecting": 8,
        "hedge": 6,
        "bear market": 6,
    },
    "market_regimes": {
        "regime": 24,
        "little book of sideways markets": 14,
        "sideways markets": 15,
        "intermarket": 10,
        "market timing": 9,
        "bear market": 8,
        "volatility": 5,
    },
    "risk_management": {
        "risk analysis techniques for traders": 16,
        "for traders": 6,
        "risk management": 20,
        "risk analysis": 16,
        "managing risk": 16,
        "risk control": 15,
        "money management": 12,
        "portfolio": 5,
    },
    "portfolio_construction": {
        "four pillars of investing": 20,
        "buy and hedge": 14,
        "market neutral strategies": 12,
        "portfolio": 18,
        "asset allocation": 18,
        "investments": 9,
        "exchange traded funds": 8,
        "etf": 7,
        "intermarket": 6,
        "hedge": 4,
    },
    "walk_forward_robustness": {
        "building winning algorithmic trading systems": 26,
        "monte carlo": 22,
        "mechanical trading systems": 22,
        "trading systems and methods": 22,
        "trading systems": 14,
        "quantitative": 9,
        "statistics": 7,
        "methods": 4,
    },
}

PENALTY_PHRASES: dict[str, float] = {
    "day trading": 18,
    "intraday": 16,
    "forex": 12,
    "binary options": 16,
    "options": 20,
    "bonds": 12,
    "foreign exchange": 10,
    "credit risk": 35,
    "credit": 20,
    "interest rate risk": 12,
    "firmwide": 15,
    "futures market": 6,
    "for a living": 10,
    "candlestick": 8,
    "for dummies": 5,
    "psychology": 5,
}


def _normalize_title(value: str) -> str:
    stem = Path(value).stem
    return re.sub(r"\s+", " ", stem.replace("_", " ")).strip()


def _book_from_mapping(raw: Mapping[str, object]) -> BookRecord:
    sha256 = str(raw["sha256"]).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("book sha256 must contain 64 hexadecimal characters")
    return BookRecord(
        book_id=f"book-{sha256[:12]}",
        title=_normalize_title(str(raw["name"])),
        source_path=str(raw["path"]),
        source_sha256=sha256,
        size_bytes=int(raw.get("size_bytes", 0)),
    )


def load_book_index(path: str | Path) -> list[BookRecord]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    raw_books = payload.get("books") if isinstance(payload, dict) else None
    if not isinstance(raw_books, list):
        raise ValueError("book index must contain a books array")
    return [_book_from_mapping(raw) for raw in raw_books if isinstance(raw, dict)]


def score_book(book: BookRecord) -> BookRecord:
    title = book.title.casefold()
    category_scores: dict[str, float] = {}
    for category, phrases in CATEGORY_PHRASES.items():
        category_scores[category] = sum(
            weight for phrase, weight in phrases.items() if phrase in title
        )
    coverage = sum(score > 0 for score in category_scores.values())
    penalty = sum(
        weight for phrase, weight in PENALTY_PHRASES.items() if phrase in title
    )
    score = sum(category_scores.values()) + coverage * 4 - penalty
    reasons = tuple(
        f"{category}:{category_score:g}"
        for category, category_score in sorted(category_scores.items())
        if category_score > 0
    )
    return replace(
        book,
        relevance_score=round(max(0.0, score), 2),
        relevance_reasons=reasons,
    )


def select_top_books(
    books: Iterable[BookRecord | Mapping[str, object]], limit: int = 20
) -> list[BookRecord]:
    if limit < 1:
        raise ValueError("limit must be positive")
    normalized = [
        book if isinstance(book, BookRecord) else _book_from_mapping(book)
        for book in books
    ]
    unique_by_title: dict[str, BookRecord] = {}
    for book in sorted(
        normalized, key=lambda item: (item.title.casefold(), item.source_sha256)
    ):
        unique_by_title.setdefault(book.title.casefold(), book)
    scored = [score_book(book) for book in unique_by_title.values()]
    relevant = [book for book in scored if book.relevance_score > 0]
    return sorted(
        relevant,
        key=lambda book: (
            -book.relevance_score,
            book.title.casefold(),
            book.source_sha256,
        ),
    )[:limit]
