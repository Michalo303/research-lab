from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution.ibkr.paper_order_simulator import simulate_paper_orders


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local research-only paper order simulation.")
    parser.add_argument("--candidates-json", required=True, help="JSON list of strategy candidates with target_weights.")
    parser.add_argument("--prices-json", required=True, help="JSON object of latest prices by symbol.")
    parser.add_argument("--equity", required=True, type=float, help="Simulation equity in USD.")
    args = parser.parse_args()

    candidates = json.loads(Path(args.candidates_json).read_text(encoding="utf-8"))
    prices = json.loads(Path(args.prices_json).read_text(encoding="utf-8"))
    result = simulate_paper_orders(Path.cwd(), candidates, prices, args.equity)
    counts = {}
    for order in result["orders"]:
        counts[order["status"]] = counts.get(order["status"], 0) + 1
    print(f"Paper order simulation appended: registry/paper_order_simulations.jsonl")
    print(f"orders: {counts}")


if __name__ == "__main__":
    main()
