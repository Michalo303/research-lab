import json
from pathlib import Path
import subprocess

import pytest

from hermes_knowledge.books import load_book_index, select_top_books
from hermes_knowledge.prompt import build_hermes_knowledge_prompt
from hermes_knowledge.retriever import retrieve_for_blocker
from hermes_knowledge.schema import (
    KnowledgeValidationError,
    load_knowledge_jsonl,
    validate_entry,
)


def _book(name: str, sha256: str) -> dict[str, object]:
    return {
        "name": name,
        "path": f"/private/raw/{name}",
        "extension": ".pdf",
        "size_bytes": 100,
        "sha256": sha256,
    }


def _entry(
    concept: str,
    *,
    blockers: list[str],
    rules: list[str] | None = None,
    expected_edge: str = "Improve risk-adjusted returns.",
    priority_score: float = 80.0,
) -> dict[str, object]:
    return {
        "book_id": "book-aaaaaaaaaaaa",
        "source_title": "Example Trading Systems",
        "source_path": "/private/raw/example.pdf",
        "source_sha256": "a" * 64,
        "concept": concept,
        "hypothesis": "A simple rule should remain stable across market regimes.",
        "summary": "Short paraphrased research note.",
        "source_excerpt": "Short supporting phrase.",
        "testable_rules": rules or ["Use a 200-day trend filter."],
        "compatible_builders": ["etf_1d"],
        "asset_classes": ["ETF"],
        "timeframes": ["1d"],
        "expected_edge": expected_edge,
        "known_failure_modes": ["Fast reversals can cause whipsaw."],
        "addresses_blockers": blockers,
        "priority_score": priority_score,
    }


def test_load_book_index_normalizes_books_and_provenance(tmp_path):
    index_path = tmp_path / "book_index.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "books": [_book("Trend Following.pdf", "1" * 64)],
            }
        ),
        encoding="utf-8",
    )

    books = load_book_index(index_path)

    assert len(books) == 1
    assert books[0].title == "Trend Following"
    assert books[0].book_id == "book-111111111111"
    assert books[0].source_path == "/private/raw/Trend Following.pdf"
    assert books[0].source_sha256 == "1" * 64


def test_select_top_books_is_deterministic_and_limited_to_twenty():
    relevant = [
        _book(f"Trend Following and Risk Management Volume {index}.pdf", f"{index:064x}")
        for index in range(25)
    ]
    irrelevant = [
        _book(f"Negotiation Handbook {index}.pdf", f"{index + 100:064x}")
        for index in range(5)
    ]

    first = select_top_books(relevant + irrelevant, limit=20)
    second = select_top_books(list(reversed(relevant + irrelevant)), limit=20)

    assert len(first) == 20
    assert [book.book_id for book in first] == [book.book_id for book in second]
    assert all("Negotiation" not in book.title for book in first)
    assert all(book.relevance_score > 0 for book in first)
    assert all(book.relevance_reasons for book in first)


def test_select_top_books_deduplicates_normalized_titles():
    books = [
        _book("Trend Following.pdf", "1" * 64),
        _book("Trend  Following .pdf", "2" * 64),
        _book("Volatility Trading.pdf", "3" * 64),
    ]

    selected = select_top_books(books, limit=3)

    assert [book.title for book in selected] == [
        "Volatility Trading",
        "Trend Following",
    ]


def test_validate_entry_accepts_short_structured_hypothesis():
    validated = validate_entry(_entry("Volatility targeting", blockers=["drawdown"]))

    assert validated["concept"] == "Volatility targeting"
    assert validated["source_sha256"] == "a" * 64


def test_validate_entry_accepts_empty_excerpt_in_v1():
    entry = _entry("Volatility targeting", blockers=["drawdown"])
    entry["source_excerpt"] = ""

    validated = validate_entry(entry)

    assert validated["source_excerpt"] == ""


def test_validate_entry_rejects_long_excerpt():
    entry = _entry("Volatility targeting", blockers=["drawdown"])
    entry["source_excerpt"] = "x" * 281

    with pytest.raises(KnowledgeValidationError, match="source_excerpt"):
        validate_entry(entry)


def test_validate_entry_rejects_long_summary():
    entry = _entry("Volatility targeting", blockers=["drawdown"])
    entry["summary"] = "x" * 601

    with pytest.raises(KnowledgeValidationError, match="summary"):
        validate_entry(entry)


def test_validate_entry_rejects_full_text_split_across_list_items():
    entry = _entry("Volatility targeting", blockers=["drawdown"])
    entry["known_failure_modes"] = ["x" * 280 for _ in range(7)]

    with pytest.raises(KnowledgeValidationError, match="total text"):
        validate_entry(entry)


def test_validate_entry_rejects_nested_full_text_payload():
    entry = _entry("Volatility targeting", blockers=["drawdown"])
    entry["full_text"] = {"chapter": "x" * 100_000}

    with pytest.raises(KnowledgeValidationError, match="unexpected fields: full_text"):
        validate_entry(entry)


LIST_FIELDS = [
    "testable_rules",
    "compatible_builders",
    "asset_classes",
    "timeframes",
    "known_failure_modes",
    "addresses_blockers",
]


@pytest.mark.parametrize("field", LIST_FIELDS)
def test_validate_entry_rejects_list_below_schema_min_items(field):
    entry = _entry("Volatility targeting", blockers=["drawdown"])
    entry[field] = []

    with pytest.raises(KnowledgeValidationError, match=field):
        validate_entry(entry)


@pytest.mark.parametrize("field", LIST_FIELDS)
def test_validate_entry_rejects_list_exceeding_schema_max_items(field):
    entry = _entry("Volatility targeting", blockers=["drawdown"])
    entry[field] = [f"item-{index}" for index in range(13)]

    with pytest.raises(KnowledgeValidationError, match=field):
        validate_entry(entry)


@pytest.mark.parametrize(
    ("field", "maximum"),
    [
        ("testable_rules", 300),
        ("compatible_builders", 100),
        ("asset_classes", 100),
        ("timeframes", 50),
        ("known_failure_modes", 300),
        ("addresses_blockers", 100),
    ],
)
def test_validate_entry_rejects_list_item_exceeding_schema_length(field, maximum):
    entry = _entry("Volatility targeting", blockers=["drawdown"])
    entry[field] = ["x" * (maximum + 1)]

    with pytest.raises(KnowledgeValidationError, match=field):
        validate_entry(entry)


@pytest.mark.parametrize("field", LIST_FIELDS)
def test_validate_entry_rejects_nested_object_in_string_list(field):
    entry = _entry("Volatility targeting", blockers=["drawdown"])
    entry[field] = [{"full_text": "x" * 100_000}]

    with pytest.raises(KnowledgeValidationError, match=field):
        validate_entry(entry)


def test_validate_entry_rejects_many_short_list_items_over_total_text_limit():
    entry = _entry("Volatility targeting", blockers=["drawdown"])
    for field in (
        "testable_rules",
        "compatible_builders",
        "asset_classes",
        "timeframes",
        "known_failure_modes",
        "addresses_blockers",
    ):
        entry[field] = ["x" * 30 for _ in range(12)]

    with pytest.raises(KnowledgeValidationError, match="total text"):
        validate_entry(entry)


def test_validate_entry_accepts_lists_at_schema_limits():
    entry = _entry("Volatility targeting", blockers=["drawdown"])
    entry["compatible_builders"] = ["x" * 100] + [
        f"builder-{index}" for index in range(11)
    ]

    validated = validate_entry(entry)

    assert len(validated["compatible_builders"]) == 12
    assert len(validated["compatible_builders"][0]) == 100


@pytest.mark.parametrize(
    "field",
    ["book_id", "source_title", "source_path", "source_sha256"],
)
def test_validate_entry_requires_every_provenance_field(field):
    entry = _entry("Volatility targeting", blockers=["drawdown"])
    del entry[field]

    with pytest.raises(KnowledgeValidationError, match=field):
        validate_entry(entry)


def test_validate_entry_rejects_book_id_without_hash_provenance():
    entry = _entry("Volatility targeting", blockers=["drawdown"])
    entry["book_id"] = "example-book"

    with pytest.raises(KnowledgeValidationError, match="book_id"):
        validate_entry(entry)


def test_validate_entry_requires_book_id_to_match_source_hash():
    entry = _entry("Volatility targeting", blockers=["drawdown"])
    entry["book_id"] = "book-bbbbbbbbbbbb"

    with pytest.raises(KnowledgeValidationError, match="source_sha256"):
        validate_entry(entry)


def test_load_knowledge_jsonl_validates_every_line(tmp_path):
    notes_path = tmp_path / "notes.jsonl"
    notes_path.write_text(
        "\n".join(
            [
                json.dumps(_entry("Trend filter", blockers=["drawdown"])),
                json.dumps(
                    _entry(
                        "Stable parameters",
                        blockers=["walk_forward_robustness"],
                    )
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    entries = load_knowledge_jsonl(notes_path)

    assert [entry["concept"] for entry in entries] == [
        "Trend filter",
        "Stable parameters",
    ]


@pytest.mark.parametrize(
    ("blocker", "preferred_concept"),
    [
        ("drawdown", "Exposure cap and cash fallback"),
        ("walk_forward_robustness", "Parameter stability"),
        ("cost_stress", "Low-turnover trend filter"),
    ],
)
def test_retriever_prioritizes_blocker_specific_entries(blocker, preferred_concept):
    entries = [
        _entry(
            "Exposure cap and cash fallback",
            blockers=["drawdown"],
            rules=["Move to cash when the ETF closes below its 200-day average."],
        ),
        _entry(
            "Parameter stability",
            blockers=["walk_forward_robustness"],
            rules=["Reject parameters that fail adjacent-window stability checks."],
        ),
        _entry(
            "Low-turnover trend filter",
            blockers=["cost_stress"],
            rules=["Rebalance only at the daily close after a persistent signal."],
        ),
    ]

    results = retrieve_for_blocker(entries, blocker=blocker, limit=2)

    assert results[0]["concept"] == preferred_concept


def test_retriever_excludes_unrelated_entries_when_blocker_matches_exist():
    relevant = _entry(
        "Cash fallback",
        blockers=["drawdown"],
        priority_score=20,
    )
    unrelated = _entry(
        "High priority but unrelated",
        blockers=["signal_quality"],
        priority_score=100,
    )

    results = retrieve_for_blocker(
        [unrelated, relevant], blocker="drawdown", limit=5
    )

    assert [entry["concept"] for entry in results] == ["Cash fallback"]


def test_retriever_preserves_knowledge_provenance():
    entry = _entry("Cash fallback", blockers=["drawdown"])

    result = retrieve_for_blocker([entry], blocker="drawdown", limit=1)[0]

    assert result["book_id"] == entry["book_id"]
    assert result["source_title"] == entry["source_title"]
    assert result["source_path"] == entry["source_path"]
    assert result["source_sha256"] == entry["source_sha256"]


def test_hermes_prompt_contains_only_retrieved_entries_and_provenance():
    selected = _entry("Parameter stability", blockers=["walk_forward_robustness"])
    ignored = _entry("Intraday scalping", blockers=["unrelated"])

    prompt = build_hermes_knowledge_prompt(
        entries=[selected, ignored],
        dominant_blocker="walk_forward_robustness",
        limit=1,
    )

    assert "Parameter stability" in prompt
    assert "Intraday scalping" not in prompt
    assert "book-aaaaaaaaaaaa" in prompt
    assert "/private/raw/example.pdf" not in prompt
    assert "aaaaaaaaaaaa" in prompt
    assert "knowledge provenance" in prompt.lower()


def test_gitignore_blocks_private_hermes_book_artifacts():
    repo_root = Path(__file__).resolve().parents[1]
    private_paths = [
        "private/hermes_books/raw/example.pdf",
        "private/hermes_books/index/book_index.json",
        "private/hermes_books/extracted_notes/notes.jsonl",
        "scratch/hermes_books/raw/example.pdf",
        "scratch/hermes_books/extracted_notes/notes.jsonl",
    ]

    for private_path in private_paths:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", "--", private_path],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, private_path


def test_git_has_no_tracked_or_staged_private_hermes_book_artifacts():
    repo_root = Path(__file__).resolve().parents[1]
    commands = [
        ["git", "ls-files"],
        ["git", "diff", "--cached", "--name-only"],
    ]

    for command in commands:
        result = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        private_artifacts = [
            path
            for path in result.stdout.splitlines()
            if "hermes_books/" in path.replace("\\", "/").casefold()
            and (
                "/raw/" in path.replace("\\", "/").casefold()
                or "/extracted_notes/" in path.replace("\\", "/").casefold()
                or path.replace("\\", "/").casefold().endswith(
                    "/index/book_index.json"
                )
            )
        ]
        assert private_artifacts == []
