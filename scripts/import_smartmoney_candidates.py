from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.smartmoney_bridge import import_smartmoney_candidates


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import local smartmoney shortlist as swing research hypotheses.")
    parser.add_argument("--smartmoney-path", default=r"C:\Users\lojka\trading\smartmoney")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--min-final-score", type=float, default=70.0)
    args = parser.parse_args()
    imported = import_smartmoney_candidates(
        Path.cwd(),
        Path(args.smartmoney_path),
        limit=args.limit,
        min_final_score=args.min_final_score,
    )
    print(f"smartmoney hypotheses imported: {len(imported)}")

