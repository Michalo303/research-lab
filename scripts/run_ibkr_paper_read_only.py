from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution.ibkr.paper_gateway import read_only_account_snapshot


if __name__ == "__main__":
    result = read_only_account_snapshot(Path.cwd())
    print(f"IBKR paper read-only snapshot: {result['status']}")
    if result.get("error"):
        print(result["error"])
