from __future__ import annotations

import subprocess
from pathlib import Path


RUNTIME_TRACKED_PATHS = [
    "data/manifests/daily_universe.json",
    "data/manifests/intraday_BTCUSDT.json",
    "registry/allocation_model.csv",
    "registry/experiments.jsonl",
    "registry/hypothesis_queue.jsonl",
    "registry/leaderboard.csv",
    "registry/strategy_registry.jsonl",
]

RUNTIME_IGNORE_SAMPLES = [
    "data/manifests/daily_universe.json",
    "data/manifests/intraday_BTCUSDT.json",
    "registry/allocation_model.csv",
    "registry/experiments.jsonl",
    "registry/hypothesis_queue.jsonl",
    "registry/leaderboard.csv",
    "registry/strategy_registry.jsonl",
]


def test_runtime_artifacts_are_not_tracked_by_git():
    root = Path(__file__).resolve().parents[1]

    tracked = subprocess.run(
        ["git", "ls-files", *RUNTIME_TRACKED_PATHS],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()

    assert tracked == []


def test_runtime_artifacts_are_ignored_by_git():
    root = Path(__file__).resolve().parents[1]

    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", *RUNTIME_IGNORE_SAMPLES],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()

    assert ignored == RUNTIME_IGNORE_SAMPLES
