from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.self_improvement import run_self_improvement


if __name__ == "__main__":
    report = run_self_improvement(Path.cwd())
    print(f"self-improvement report written: {report}")

