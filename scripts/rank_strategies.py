from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.registry import write_allocation_model, write_leaderboard


if __name__ == "__main__":
    root = Path.cwd()
    leaderboard = root / "registry" / "leaderboard.csv"
    if not leaderboard.exists():
        raise SystemExit("registry/leaderboard.csv does not exist; run scripts/run_daily_research.py first")
    print(f"ranking already materialized at {leaderboard}")
    print(f"allocation model already materialized at {root / 'registry' / 'allocation_model.csv'}")
