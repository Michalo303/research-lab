from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.apify_dataroma import DEFAULT_SUPERINVESTORS, run_dataroma_actor


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a limited Apify Dataroma holdings import.")
    parser.add_argument("--superinvestors", default=",".join(DEFAULT_SUPERINVESTORS))
    parser.add_argument("--max-results", type=int, default=200)
    args = parser.parse_args()
    investors = [item.strip() for item in args.superinvestors.split(",") if item.strip()]
    items = run_dataroma_actor(Path.cwd(), superinvestors=investors, max_results=args.max_results)
    print(f"apify dataroma holdings imported: {len(items)}")

