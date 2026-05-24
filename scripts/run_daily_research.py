from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.runner import run_daily_research


if __name__ == "__main__":
    results = run_daily_research(Path.cwd())
    print(f"daily research completed: {len(results)} experiments")
